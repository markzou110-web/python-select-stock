import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import tasks


def test_limit_up_collection_window_includes_after_close_final_capture(monkeypatch):
    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: False)
    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)

    assert tasks._is_limit_up_collection_window(datetime(2026, 6, 12, 15, 1)) is True
    assert tasks._is_limit_up_collection_window(datetime(2026, 6, 12, 15, 6)) is False


def test_after_close_review_pushes_next_day_watchlist(monkeypatch):
    calls = []
    sent = []
    settings = {}

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda notify=True, trade_mode="REAL": {"alerts": []})
    monkeypatch.setattr("routers.watchlist.refresh_watchlist_decisions", lambda: None)
    monkeypatch.setattr("routers.watchlist.auto_prune_watchlist", lambda max_watch_days=15: {"updated": 0})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: {"count": 0})
    monkeypatch.setattr("routers.watchlist.send_watchlist_status_report", lambda slot: {"bark": False, "count": 0})
    monkeypatch.setattr("core.db.get_scan_history_by_date", lambda date_str, engine=None: [{"代码": "300145", "名称": "南方泵业", "sop_grade": "M", "trade_bucket": "OBSERVE"}])
    monkeypatch.setattr(
        "core.sentinel.send_after_close_watchlist",
        lambda scan_results, scan_date=None, now=None, limit=5: calls.append((scan_date, len(scan_results), now)) or "body",
    )
    monkeypatch.setattr("core.db.get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr("core.db.save_setting", lambda key, value: settings.update({key: value}) or True)
    monkeypatch.setattr("routers.review.get_daily_strategy_report", lambda date="": {"body": f"日报 {date}"})
    monkeypatch.setattr("core.sentinel._send_bark_message", lambda title, body: sent.append((title, body)) or True)

    result = tasks.intraday_monitor_checkpoint(slot="after_close_review")

    assert result["slot"] == "after_close_review"
    assert result["next_day_push"] == 1
    assert result["daily_report_push"] == 1
    assert calls and calls[0][0] == datetime.now().strftime("%Y-%m-%d")
    assert calls[0][1] == 1
    assert sent and sent[0][1].startswith("日报 ")
    assert settings["daily_strategy_report_last_date"] == datetime.now().strftime("%Y-%m-%d")


def test_open_risk_pushes_watchlist_morning_plan(monkeypatch):
    calls = []

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr("core.bark_health.send_bark_self_check", lambda: {"notification": {"bark": True}})
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda notify=True, trade_mode="REAL": {"alerts": []})
    monkeypatch.setattr("routers.watchlist.refresh_watchlist_decisions", lambda: None)
    monkeypatch.setattr("routers.watchlist.auto_prune_watchlist", lambda max_watch_days=15: {"updated": 0})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: {"count": 0})
    monkeypatch.setattr(
        "routers.watchlist.send_watchlist_status_report",
        lambda slot: calls.append(slot) or {"bark": True, "count": 4},
    )

    result = tasks.intraday_monitor_checkpoint(slot="open_risk")

    assert calls == ["morning"]
    assert result["watch_status_push"] == 4
    assert result["bark_self_check"] == 1


def test_late_decision_pushes_watchlist_conclusion(monkeypatch):
    calls = []

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda notify=True, trade_mode="REAL": {"alerts": []})
    monkeypatch.setattr("routers.watchlist.refresh_watchlist_decisions", lambda: None)
    monkeypatch.setattr("routers.watchlist.auto_prune_watchlist", lambda max_watch_days=15: {"updated": 0})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: {"count": 0})
    monkeypatch.setattr(
        "routers.watchlist.send_watchlist_status_report",
        lambda slot: calls.append(slot) or {"bark": True, "count": 3},
    )

    result = tasks.intraday_monitor_checkpoint(slot="late_decision")

    assert calls == ["late"]
    assert result["watch_status_push"] == 3


def test_noon_scan_push_translates_ready_as_confirmation_not_buy(monkeypatch):
    sent = []

    async def fake_send(title, body, channels=None, group=None, url=None):
        sent.append((title, body, channels, group, url))
        return True

    monkeypatch.setattr(tasks.notifier, "send", fake_send)
    monkeypatch.setattr("core.data.get_stale_cache", lambda key: None)
    monkeypatch.setattr("core.data.format_freshness", lambda cache: "行情 13:05 (实时) · 腾讯")

    ok = tasks._send_noon_scan_push([
        {
            "代码": "002841",
            "名称": "视源股份",
            "现价": 47.39,
            "Score": 75.5,
            "sop_grade": "M",
            "pa_trade_action": "READY",
            "data_date": "2026-07-08",
        },
        {
            "代码": "600288",
            "名称": "大恒科技",
            "现价": 16.99,
            "Score": 58.7,
            "sop_grade": "D",
            "pa_trade_action": "READY",
            "data_date": "2026-07-08",
        },
    ])

    assert ok is True
    body = sent[0][1]
    assert "建议：待确认：可复核，不等于立即买入" in body
    assert "大恒科技" in body
    assert "建议：排除/不买" in body


def test_noon_workflow_syncs_scans_and_pushes_reports(monkeypatch):
    calls = []

    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)
    monkeypatch.setattr(tasks, "daily_sync", lambda slot="晚上": calls.append(("sync", slot)) or "Scheduled sync completed successfully (午间)")
    monkeypatch.setattr("routers.scan.run_market_scan_task", lambda **kwargs: calls.append(("scan", kwargs)) or [{"代码": "603259", "名称": "药明康德", "Score": 88, "data_date": "2026-06-30"}])
    monkeypatch.setattr(tasks, "_send_noon_scan_push", lambda results: calls.append(("scan_push", len(results))) or True)
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda notify=True, trade_mode="REAL": calls.append(("operation", trade_mode)) or {"alerts": [{"code": "603259"}]})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: calls.append(("watch_trigger", notify)) or {"count": 2})
    monkeypatch.setattr("routers.watchlist.send_watchlist_status_report", lambda slot: calls.append(("watch_report", slot)) or {"bark": True, "count": 4})

    result = tasks.noon_sync_scan_review()

    assert result["sync"] == "Scheduled sync completed successfully (午间)"
    assert result["scan_count"] == 1
    assert result["scan_push"] is True
    assert result["operation_alerts"] == 1
    assert result["watch_alerts"] == 2
    assert result["watch_status_push"] == 4
    assert calls[0] == ("sync", "午间")
    assert calls[1][0] == "scan"
    assert calls[-1] == ("watch_report", "noon")


def test_noon_sync_only_does_not_scan_or_push(monkeypatch):
    calls = []

    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)
    monkeypatch.setattr(tasks, "daily_sync", lambda slot="晚上": calls.append(("sync", slot)) or "Scheduled sync completed successfully (午间)")

    result = tasks.noon_sync_scan_review(sync_first=True, run_review=False)

    assert result["sync"] == "Scheduled sync completed successfully (午间)"
    assert result["scan_count"] == 0
    assert result["operation_alerts"] == 0
    assert calls == [("sync", "午间")]


def test_noon_review_continues_when_scan_fails(monkeypatch):
    calls = []

    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)

    def fail_scan(**kwargs):
        calls.append(("scan", kwargs))
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr("routers.scan.run_market_scan_task", fail_scan)
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda notify=True, trade_mode="REAL": calls.append(("operation", trade_mode)) or {"alerts": [{"code": "603259"}]})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: calls.append(("watch_trigger", notify)) or {"count": 2})
    monkeypatch.setattr("routers.watchlist.send_watchlist_status_report", lambda slot: calls.append(("watch_report", slot)) or {"bark": True, "count": 4})

    result = tasks.noon_sync_scan_review(sync_first=False, run_review=True)

    assert result["scan_count"] == 0
    assert result["operation_alerts"] == 1
    assert result["watch_alerts"] == 2
    assert result["watch_status_push"] == 4
    assert result["errors"] == ["scan: snapshot unavailable"]
    assert ("operation", "REAL") in calls


def test_noon_workflow_stops_when_sync_did_not_complete(monkeypatch):
    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)
    monkeypatch.setattr(tasks, "daily_sync", lambda slot="晚上": "Sync locked")

    result = tasks.noon_sync_scan_review()

    assert result["sync"] == "Sync locked"
    assert result["scan_count"] == 0
    assert result["scan_push"] is False
