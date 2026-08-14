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
    shadow_calls = []
    sent = []
    settings = {}

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda notify=True, trade_mode="REAL": {"alerts": []})
    monkeypatch.setattr("routers.watchlist.refresh_watchlist_decisions", lambda: None)
    monkeypatch.setattr("routers.watchlist.auto_prune_watchlist", lambda max_watch_days=15: {"updated": 0})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: {"count": 0})
    monkeypatch.setattr("routers.watchlist.send_watchlist_status_report", lambda slot: {"bark": False, "count": 0})
    monkeypatch.setattr("core.db.get_scan_history_by_date", lambda date_str, engine=None: [{"代码": "300145", "名称": "南方泵业", "sop_grade": "M", "trade_bucket": "OBSERVE", "strategy_type": "tv_dual"}])
    monkeypatch.setattr(
        "core.sentinel.send_after_close_watchlist",
        lambda scan_results, scan_date=None, now=None, limit=5: calls.append((scan_date, len(scan_results), now)) or "body",
    )
    monkeypatch.setattr("core.db.get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr("core.db.save_setting", lambda key, value: settings.update({key: value}) or True)
    monkeypatch.setattr(
        "core.sentinel.run_new_strategy_shadow_cycle",
        lambda candidates, completed_day=False, notify=True: shadow_calls.append(
            (candidates, completed_day, notify)
        ) or ("shadow body" if notify else None),
    )

    result = tasks.intraday_monitor_checkpoint(slot="after_close_review")

    assert result["slot"] == "after_close_review"
    assert result["next_day_push"] == 1
    assert result["shadow_close_push"] == 0
    assert result["daily_report_push"] == 0
    assert calls and calls[0][0] == datetime.now().strftime("%Y-%m-%d")
    assert calls[0][1] == 1
    assert len(shadow_calls[0][0]) == 1
    assert shadow_calls[0][1] is True
    assert shadow_calls[0][2] is False
    assert sent == []


def test_open_risk_keeps_healthy_self_check_and_watchlist_silent(monkeypatch):
    calls = []

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr("core.bark_health.send_bark_self_check", lambda: {"notification": {"bark": False}})
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda notify=True, trade_mode="REAL": {"alerts": []})
    monkeypatch.setattr("routers.watchlist.refresh_watchlist_decisions", lambda: None)
    monkeypatch.setattr("routers.watchlist.auto_prune_watchlist", lambda max_watch_days=15: {"updated": 0})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: {"count": 0})
    result = tasks.intraday_monitor_checkpoint(slot="open_risk")

    assert calls == []
    assert result["watch_status_push"] == 0
    assert result["bark_self_check"] == 0


def test_morning_confirm_runs_tv_or_scan_and_pushes_results(monkeypatch):
    calls = []

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(
        "routers.paper_trade.check_operation_triggers",
        lambda notify=True, trade_mode="REAL": {"alerts": []},
    )
    monkeypatch.setattr(
        "routers.watchlist.check_watchlist_triggers",
        lambda notify=True: {"count": 0},
    )
    monkeypatch.setattr(
        "routers.scan.run_market_scan_task",
        lambda **kwargs: calls.append(("scan", kwargs)) or [{
            "代码": "000001",
            "strategy_type": kwargs["strategy_type"],
        }],
    )
    monkeypatch.setattr(
        "core.sentinel.send_intraday_notification",
        lambda rows: calls.append(("push", rows)) or "body",
    )
    monkeypatch.setattr(
        "core.next_day_confirmation.send_next_day_confirmation",
        lambda engine, rows, now=None: calls.append(("confirm", rows)) or {
            "reviewed": 2, "confirmed": 1, "bark": True,
        },
    )

    result = tasks.intraday_monitor_checkpoint(slot="morning_confirm")

    assert calls[0][1]["strategy_type"] == "tv_dual"
    assert calls[1][0] == "push"
    assert calls[2][0] == "confirm"
    assert [row["代码"] for row in calls[1][1]] == ["000001"]
    assert result["formal_scan_count"] == 1
    assert result["formal_scan_push"] == 1
    assert result["formal_scan_completed"] == 1
    assert result["next_day_confirmation"]["confirmed"] == 1
    assert result["next_day_reviewed"] == 2
    assert result["next_day_confirmed"] == 1
    assert result["next_day_confirmation_bark"] == 1
    assert result["errors"] == []


def test_late_decision_keeps_watchlist_conclusion_in_app(monkeypatch):
    calls = []

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda notify=True, trade_mode="REAL": {"alerts": []})
    monkeypatch.setattr("routers.watchlist.refresh_watchlist_decisions", lambda: None)
    monkeypatch.setattr("routers.watchlist.auto_prune_watchlist", lambda max_watch_days=15: {"updated": 0})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: {"count": 0})
    monkeypatch.setattr(
        "routers.scan.run_market_scan_task",
        lambda **kwargs: calls.append(("scan", kwargs)) or [{
            "代码": "000001",
            "strategy_type": kwargs["strategy_type"],
        }],
    )
    monkeypatch.setattr(
        "core.sentinel.send_intraday_notification",
        lambda rows: calls.append(("push", rows)) or "body",
    )

    result = tasks.intraday_monitor_checkpoint(slot="late_decision")

    assert calls[0][1]["strategy_type"] == "tv_dual"
    assert calls[1][0] == "push"
    assert result["formal_scan_count"] == 1
    assert result["formal_scan_push"] == 1
    assert result["formal_scan_completed"] == 1
    assert result["watch_status_push"] == 0


def test_candidate_scan_runs_formal_scan_instead_of_only_refreshing_watchlist(monkeypatch):
    calls = []
    results = [{"代码": "000001", "strategy_type": "tv_dual_strict"}]

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda **kwargs: {"alerts": []})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda **kwargs: {"count": 0})
    monkeypatch.setattr("routers.watchlist.refresh_watchlist_decisions", lambda: calls.append("refresh"))
    monkeypatch.setattr("routers.watchlist.auto_prune_watchlist", lambda max_watch_days=15: {"updated": 0})
    monkeypatch.setattr("routers.scan.run_market_scan_task", lambda **kwargs: calls.append("scan") or results)
    monkeypatch.setattr("core.sentinel.send_intraday_notification", lambda rows: calls.append("push") or "body")

    result = tasks.intraday_monitor_checkpoint(slot="candidate_scan")

    assert calls == ["scan", "push", "refresh"]
    assert result["formal_scan_completed"] == 1
    assert result["formal_scan_count"] == 1
    assert result["formal_scan_push"] == 1


def test_noon_scan_push_translates_ready_as_confirmation_not_buy(monkeypatch):
    sent = []

    async def fake_send(title, body, channels=None, group=None, url=None):
        sent.append((title, body, channels, group, url))
        return {"bark": True}

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
            "trade_bucket": "TRADE",
            "trade_eligible": True,
            "data_date": "2026-07-08",
        },
        {
            "代码": "600288",
            "名称": "大恒科技",
            "现价": 16.99,
            "Score": 58.7,
            "sop_grade": "D",
            "pa_trade_action": "READY",
            "trade_bucket": "BLOCK",
            "trade_eligible": False,
            "data_date": "2026-07-08",
        },
    ])

    assert ok is True
    body = sent[0][1]
    assert "建议：待确认：可复核，不等于立即买入" in body
    assert "大恒科技" not in body


def test_noon_scan_push_does_not_treat_failed_result_dict_as_success(monkeypatch):
    async def fake_send(*args, **kwargs):
        return {"bark": False}

    monkeypatch.setattr(tasks.notifier, "send", fake_send)
    monkeypatch.setattr("core.data.get_stale_cache", lambda key: None)
    monkeypatch.setattr("core.data.format_freshness", lambda cache: "行情实时")

    assert tasks._send_noon_scan_push([{
        "代码": "000001", "名称": "测试股", "现价": 10, "Score": 80,
        "sop_grade": "M", "data_date": "2026-07-16",
        "trade_bucket": "TRADE", "trade_eligible": True,
    }]) is False


def test_noon_workflow_syncs_scans_and_pushes_reports(monkeypatch):
    calls = []

    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)
    monkeypatch.setattr(tasks, "daily_sync", lambda slot="晚上": calls.append(("sync", slot)) or "Scheduled sync completed successfully (午间)")
    monkeypatch.setattr("routers.scan.run_market_scan_task", lambda **kwargs: calls.append(("scan", kwargs)) or [{"代码": "603259", "名称": "药明康德", "Score": 88, "data_date": "2026-06-30"}])
    monkeypatch.setattr(tasks, "_send_noon_scan_push", lambda results: calls.append(("scan_push", len(results))) or True)
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda notify=True, trade_mode="REAL": calls.append(("operation", trade_mode)) or {"alerts": [{"code": "603259"}]})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: calls.append(("watch_trigger", notify)) or {"count": 2})

    result = tasks.noon_sync_scan_review()

    assert result["sync"] == "Scheduled sync completed successfully (午间)"
    assert result["scan_count"] == 1
    assert result["scan_push"] is True
    assert result["operation_alerts"] == 1
    assert result["watch_alerts"] == 2
    assert result["watch_status_push"] == 0
    assert calls[0] == ("sync", "午间")
    assert calls[1][0] == "scan"
    assert ("watch_trigger", False) in calls


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

    result = tasks.noon_sync_scan_review(sync_first=False, run_review=True)

    assert result["scan_count"] == 0
    assert result["operation_alerts"] == 1
    assert result["watch_alerts"] == 2
    assert result["watch_status_push"] == 0
    assert len(result["errors"]) == 1
    assert "snapshot unavailable" in result["errors"][0]
    assert [call[1]["strategy_type"] for call in calls if call[0] == "scan"] == ["tv_dual"]
    assert ("operation", "REAL") in calls
    assert ("watch_trigger", False) in calls


def test_noon_workflow_stops_when_sync_did_not_complete(monkeypatch):
    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)
    monkeypatch.setattr(tasks, "daily_sync", lambda slot="晚上": "Sync locked")

    result = tasks.noon_sync_scan_review()

    assert result["sync"] == "Sync locked"
    assert result["scan_count"] == 0
    assert result["scan_push"] is False


def test_retry_pending_notifications_marks_delivery_result_without_requeue(monkeypatch):
    calls = []

    async def fake_send(*args, **kwargs):
        calls.append((args, kwargs))
        return {"bark": True}

    monkeypatch.setattr(tasks.notifier, "send", fake_send)
    monkeypatch.setattr("core.audit_log.load_due_notifications", lambda limit=20: [{
        "id": 7,
        "channel": "bark",
        "title": "收盘日报",
        "body": "完整正文",
        "url": None,
        "group_name": "AlphaVision",
        "is_archive": 1,
        "attempts": 0,
    }])
    monkeypatch.setattr(
        "core.audit_log.record_notification_retry",
        lambda outbox_id, sent, error=None: calls.append((outbox_id, sent, error)) or True,
    )

    result = tasks.retry_pending_notifications()

    assert result == {"count": 1, "sent": 1, "failed": 0}
    assert calls[0][1]["enqueue_failed"] is False
    assert calls[1] == (7, True, None)


def test_retry_pending_notifications_drops_oversized_bark_without_delivery(monkeypatch):
    calls = []
    monkeypatch.setattr("core.audit_log.load_due_notifications", lambda limit=20: [{
        "id": 8,
        "channel": "bark",
        "title": "超长清单",
        "body": "中" * 1100,
        "url": None,
        "group_name": "AlphaVision",
        "is_archive": 1,
        "attempts": 3,
    }])
    monkeypatch.setattr(
        "core.audit_log.record_notification_retry",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    result = tasks.retry_pending_notifications()

    assert result == {"count": 1, "sent": 0, "failed": 1}
    assert calls == [((8, False, "payload_too_large"), {"permanent": True})]


def test_late_recovery_runs_formal_scan_without_duplicate_watchlist_report(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(
        "routers.scan.run_market_scan_task",
        lambda **kwargs: calls.append(("scan", kwargs)) or [{
            "代码": "600000",
            "strategy_type": kwargs["strategy_type"],
        }],
    )
    monkeypatch.setattr(
        "core.sentinel.send_intraday_notification",
        lambda rows: calls.append(("push", rows)) or "body",
    )

    result = tasks.intraday_monitor_checkpoint(slot="late_recovery")

    assert calls[0][1]["strategy_type"] == "tv_dual"
    assert calls[1][0] == "push"
    assert result["formal_scan_completed"] == 1
    assert result["formal_scan_push"] == 1
    assert result["watch_status_push"] == 0


def test_late_recovery_skips_when_primary_scan_completed(monkeypatch):
    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)
    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(
        tasks, "_late_formal_scan_state",
        lambda now=None: {"completed": True, "running": False, "reason": "completed"},
    )
    monkeypatch.setattr(
        tasks,
        "intraday_monitor_checkpoint",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("recovery must not run")),
    )

    assert tasks.recover_late_formal_scan()["reason"] == "late_scan_already_completed"


def test_late_recovery_runs_once_when_primary_scan_failed(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)
    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(
        tasks, "_late_formal_scan_state",
        lambda now=None: {"completed": False, "running": False, "reason": "incomplete"},
    )
    monkeypatch.setattr(
        tasks,
        "intraday_monitor_checkpoint",
        lambda slot: calls.append(slot) or {"slot": slot, "formal_scan_completed": 1},
    )

    result = tasks.recover_late_formal_scan()

    assert calls == ["late_recovery"]
    assert result["formal_scan_completed"] == 1


def test_late_recovery_does_not_overlap_running_primary_scan(monkeypatch):
    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)
    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(
        tasks, "_late_formal_scan_state",
        lambda now=None: {"completed": False, "running": True, "reason": "still_running"},
    )
    monkeypatch.setattr(
        tasks,
        "intraday_monitor_checkpoint",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("overlapping recovery must not run")),
    )

    assert tasks.recover_late_formal_scan()["reason"] == "late_scan_still_running"
