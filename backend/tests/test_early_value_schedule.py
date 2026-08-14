import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.celery_app import celery_app


def test_early_value_scan_is_independent_scheduled_task():
    schedule = celery_app.conf.beat_schedule["early-value-independent-scan-1315"]

    assert schedule["task"] == "tasks.early_value_scan"
    assert "noon-scan-review-1305" not in celery_app.conf.beat_schedule
    assert "intraday-candidate-scan-1420" not in celery_app.conf.beat_schedule


def test_bottom_discovery_has_staggered_independent_schedules():
    morning = celery_app.conf.beat_schedule["bottom-discovery-morning-0945"]
    afternoon = celery_app.conf.beat_schedule["bottom-discovery-afternoon-1325"]

    assert morning["task"] == "tasks.bottom_discovery_scan"
    assert morning["kwargs"] == {"slot": "09:45"}
    assert afternoon["task"] == "tasks.bottom_discovery_scan"
    assert afternoon["kwargs"] == {"slot": "13:25"}


def test_celery_routes_realtime_and_long_scans_to_separate_queues():
    routes = celery_app.conf.task_routes

    assert routes["tasks.check_realtime_alerts"]["queue"] == "realtime"
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
    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now: True)
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
