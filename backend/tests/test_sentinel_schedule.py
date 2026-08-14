"""Intraday strategy scan schedule defaults and migration."""

import os
import sys
from datetime import datetime

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.config import DEFAULT_SENTINEL_SCHEDULE_TIMES
from core import sentinel as sentinel_mod
from core import tasks
from routers import settings as settings_router


EXPECTED_TIMES = [
    "09:30", "10:00", "10:30", "11:00",
    "13:00", "13:30", "14:00", "14:30",
]


def test_default_schedule_scans_every_30_minutes_during_trading_sessions():
    assert DEFAULT_SENTINEL_SCHEDULE_TIMES.split(",") == EXPECTED_TIMES


def test_legacy_single_scan_schedule_migrates_once(monkeypatch):
    values = {
        "sentinel_schedule_times": "14:20",
        "sentinel_schedule_policy_version": "",
    }
    monkeypatch.setattr(sentinel_mod.config, "SENTINEL_SCHEDULE_TIMES", DEFAULT_SENTINEL_SCHEDULE_TIMES)
    monkeypatch.setattr(sentinel_mod, "get_setting", lambda key, default="": values.get(key, default))
    monkeypatch.setattr(sentinel_mod, "save_setting", lambda key, value: values.update({key: value}) or True)

    sentinel = sentinel_mod.IntradaySentinel()
    sentinel._load_schedule()

    assert sentinel.schedule_times == EXPECTED_TIMES
    assert values["sentinel_schedule_times"] == DEFAULT_SENTINEL_SCHEDULE_TIMES
    assert values["sentinel_schedule_policy_version"] == "half-hour-intraday-v1"


def test_custom_schedule_is_preserved_during_policy_upgrade(monkeypatch):
    values = {
        "sentinel_schedule_times": "10:15,14:45",
        "sentinel_schedule_policy_version": "",
    }
    monkeypatch.setattr(sentinel_mod, "get_setting", lambda key, default="": values.get(key, default))
    monkeypatch.setattr(sentinel_mod, "save_setting", lambda key, value: values.update({key: value}) or True)

    sentinel = sentinel_mod.IntradaySentinel()
    sentinel._load_schedule()

    assert sentinel.schedule_times == ["10:15", "14:45"]


def test_settings_api_uses_half_hour_schedule_as_fallback(monkeypatch):
    monkeypatch.setattr(settings_router, "get_setting", lambda key, default="": default)

    payload = settings_router.get_settings_api()

    assert payload["sentinel_schedule_times"].split(",") == EXPECTED_TIMES


def test_blank_schedule_is_rejected():
    with pytest.raises(HTTPException) as exc:
        settings_router._validate_schedule_times("  ")

    assert exc.value.status_code == 400


def test_due_slot_has_five_minute_catch_up_window():
    sentinel = sentinel_mod.IntradaySentinel()
    sentinel.schedule_times = ["09:30", "10:00"]

    assert sentinel._due_schedule_slot(datetime(2026, 8, 10, 9, 34)) == "09:30"
    assert sentinel._due_schedule_slot(datetime(2026, 8, 10, 9, 36)) is None
    sentinel.triggered_today.add("09:30")
    assert sentinel._due_schedule_slot(datetime(2026, 8, 10, 10, 1)) == "10:00"


def test_half_hour_scan_is_queued_with_a_unique_slot(monkeypatch):
    calls = []

    class FakeTask:
        @staticmethod
        def apply_async(**kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("core.tasks.intraday_monitor_checkpoint", FakeTask())
    sentinel = sentinel_mod.IntradaySentinel()

    sentinel._enqueue_strategy_scan("09:30")
    sentinel._enqueue_strategy_scan("10:30")

    assert calls[0]["kwargs"] == {"slot": "strategy_scan_0930"}
    assert calls[1]["kwargs"] == {"slot": "morning_confirm"}
    assert all(call["queue"] == "scan" for call in calls)


def test_periodic_slot_runs_formal_tv_scan(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(
        "routers.scan.run_market_scan_task",
        lambda **kwargs: calls.append(("scan", kwargs)) or [{"代码": "603259"}],
    )
    monkeypatch.setattr(
        sentinel_mod,
        "send_intraday_notification",
        lambda rows: calls.append(("push", rows)) or "body",
    )
    monkeypatch.setattr("core.db.get_setting", lambda key, default=None: "false")

    result = tasks.intraday_monitor_checkpoint(slot="strategy_scan_0930")

    assert calls[0][0] == "scan"
    assert calls[0][1]["strategy_type"] == "tv_dual"
    assert calls[1][0] == "push"
    assert result["formal_scan_completed"] == 1


def test_noon_periodic_scan_preserves_position_and_watchlist_checks(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(
        "routers.paper_trade.check_operation_triggers",
        lambda notify=True, trade_mode="REAL": calls.append("operation") or {"alerts": [1]},
    )
    monkeypatch.setattr(
        "routers.watchlist.check_watchlist_triggers",
        lambda notify=True: calls.append("watch") or {"count": 2},
    )
    monkeypatch.setattr(
        "routers.watchlist.send_watchlist_status_report",
        lambda slot: calls.append(f"report:{slot}") or {"bark": True, "count": 3},
    )
    monkeypatch.setattr("routers.scan.run_market_scan_task", lambda **kwargs: [])
    monkeypatch.setattr(sentinel_mod, "send_intraday_heartbeat", lambda rows, reason: "body")

    result = tasks.intraday_monitor_checkpoint(slot="strategy_scan_1300")

    assert calls == ["operation", "watch"]
    assert result["operation_alerts"] == 1
    assert result["watch_alerts"] == 2
    assert result["watch_status_push"] == 0


def test_1430_periodic_scan_preserves_candidate_maintenance(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: {"count": 0})
    monkeypatch.setattr(
        "routers.watchlist.refresh_watchlist_decisions",
        lambda: calls.append("refresh"),
    )
    monkeypatch.setattr(
        "routers.watchlist.auto_prune_watchlist",
        lambda max_watch_days=15: calls.append("prune") or {"updated": 4},
    )
    monkeypatch.setattr("routers.scan.run_market_scan_task", lambda **kwargs: [])
    monkeypatch.setattr(sentinel_mod, "send_intraday_heartbeat", lambda rows, reason: "body")

    result = tasks.intraday_monitor_checkpoint(slot="strategy_scan_1430")

    assert calls == ["refresh", "prune"]
    assert result["pruned"] == 4


def test_unchanged_half_hour_heartbeat_is_not_pushed_twice(monkeypatch):
    sent = []
    state = {}
    monkeypatch.setattr(sentinel_mod, "is_a_share_intraday_session", lambda: True)
    monkeypatch.setattr("core.data.get_market_regime", lambda: {"status": "SIDEWAYS"})
    monkeypatch.setattr("core.data.get_market_snapshot", lambda: None)
    monkeypatch.setattr("core.data.format_freshness", lambda snapshot: "行情 10:00 · 实时")
    monkeypatch.setattr(sentinel_mod, "_append_real_position_status", lambda lines: None)
    monkeypatch.setattr(sentinel_mod, "get_setting", lambda key, default=None: state.get(key, default))
    monkeypatch.setattr(sentinel_mod, "save_setting", lambda key, value: state.update({key: value}) or True)
    monkeypatch.setattr(
        sentinel_mod,
        "_send_bark_message",
        lambda title, body: sent.append((title, body)) or True,
    )

    first = sentinel_mod.send_intraday_heartbeat([], "无命中")
    second = sentinel_mod.send_intraday_heartbeat([], "无命中")

    assert first
    assert second == ""
    assert len(sent) == 1
