import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.celery_app import celery_app


def test_early_value_scan_is_independent_scheduled_task():
    schedule = celery_app.conf.beat_schedule["early-value-independent-scan-1315"]

    assert schedule["task"] == "tasks.early_value_scan"
    assert schedule["options"]["expires"] == 900
    assert "noon-scan-review-1305" not in celery_app.conf.beat_schedule
    assert "intraday-candidate-scan-1420" not in celery_app.conf.beat_schedule


def test_bottom_discovery_has_staggered_independent_schedules():
    morning = celery_app.conf.beat_schedule["bottom-discovery-morning-0945"]
    afternoon = celery_app.conf.beat_schedule["bottom-discovery-afternoon-1325"]

    assert morning["task"] == "tasks.bottom_discovery_scan"
    assert morning["kwargs"] == {"slot": "09:45"}
    assert morning["options"]["expires"] == 900
    assert afternoon["task"] == "tasks.bottom_discovery_scan"
    assert afternoon["kwargs"] == {"slot": "13:25"}
    assert afternoon["options"]["expires"] == 900


def test_celery_routes_realtime_and_long_scans_to_separate_queues():
    routes = celery_app.conf.task_routes

    assert routes["tasks.check_realtime_alerts"]["queue"] == "realtime"
    assert routes["tasks.check_position_operation_alerts"]["queue"] == "realtime"
    assert routes["tasks.send_premarket_position_advice"]["queue"] == "realtime"
    assert routes["tasks.collect_limit_up_leadership"]["queue"] == "realtime"
    assert routes["tasks.intraday_monitor_checkpoint"]["queue"] == "scan"
    assert routes["tasks.recover_late_formal_scan"]["queue"] == "scan"
    assert routes["tasks.early_value_scan"]["queue"] == "scan"
    assert routes["tasks.bottom_discovery_scan"]["queue"] == "scan"
    assert routes["tasks.database_backup"]["queue"] == "maintenance"
    assert celery_app.conf.beat_schedule["collect-limit-up-leadership-every-minute"]["options"]["expires"] == 50
    assert celery_app.conf.beat_schedule["intraday-late-decision-1450"]["options"]["expires"] == 300


def test_bottom_discovery_task_runs_research_scan_and_observation_push(monkeypatch):
    from core import tasks

    calls = {}
    candidate = {
        "代码": "000001", "strategy_type": "bottom_discovery",
        "bottom_discovery_watch_only": True, "trade_eligible": False,
    }
    monkeypatch.setattr(tasks, "_scheduled_scan_skip_reason", lambda now, slot: None)
    monkeypatch.setattr(
        "routers.scan.run_market_scan_task",
        lambda **kwargs: calls.update({"scan": kwargs}) or [candidate],
    )
    monkeypatch.setattr(
        "core.sentinel.send_intraday_notification",
        lambda rows: calls.update({"push": rows}) or "body",
    )

    result = tasks.bottom_discovery_scan(slot="09:45")

    assert result["status"] == "ok"
    assert result["scan_count"] == 1
    assert result["bark_status"] == "sent"
    assert calls["scan"]["strategy_type"] == "bottom_discovery"
    assert calls["scan"]["require_live_snapshot"] is True
    assert calls["push"] == [candidate]


def test_scheduled_scan_slot_rejects_closed_and_stale_tasks(monkeypatch):
    from core import tasks

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: False)
    assert tasks._scheduled_scan_skip_reason(datetime(2026, 9, 4, 22, 2), "13:15") == "market_closed"

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    assert tasks._scheduled_scan_skip_reason(datetime(2026, 9, 4, 13, 20), "13:15") is None
    assert tasks._scheduled_scan_skip_reason(datetime(2026, 9, 4, 13, 31), "13:15") == "stale_task_slot"
    assert tasks._scheduled_scan_skip_reason(datetime(2026, 9, 4, 13, 25), "09:45") == "stale_task_slot"


def test_early_value_does_not_scan_when_stale(monkeypatch):
    from core import tasks

    monkeypatch.setattr(tasks, "_scheduled_scan_skip_reason", lambda now, slot: "stale_task_slot")
    monkeypatch.setattr(
        "routers.scan.run_market_scan_task",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("stale task must not scan")),
    )

    result = tasks.early_value_scan()

    assert result == {
        "status": "skipped",
        "reason": "stale_task_slot",
        "strategy_type": "early_value",
        "slot": "13:15",
    }
