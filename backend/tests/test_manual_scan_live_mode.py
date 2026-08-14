from datetime import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import scan


class _PendingTaskResult:
    id = "manual-scan-task"
    state = "PENDING"


class _CapturedTask:
    def __init__(self):
        self.args = None

    def delay(self, *args):
        self.args = args
        return _PendingTaskResult()


def test_manual_tv_plus_scan_forces_live_snapshot_during_session(monkeypatch):
    task = _CapturedTask()
    monkeypatch.setattr(scan, "run_market_scan_task", task)
    monkeypatch.setattr(scan, "is_a_share_intraday_session", lambda now=None: True)

    response = scan.scan_market(
        strategy_type="tv_dual_strict",
        local_only=True,
        data_date="",
    )

    assert response["task_id"] == "manual-scan-task"
    assert task.args[11] is False
    assert task.args[12] is None
    assert task.args[18] is True
    assert task.args[19] is True


def test_manual_tv_plus_historical_scan_keeps_requested_date_local(monkeypatch):
    task = _CapturedTask()
    monkeypatch.setattr(scan, "run_market_scan_task", task)
    monkeypatch.setattr(scan, "is_a_share_intraday_session", lambda now=None: True)

    scan.scan_market(
        strategy_type="tv_dual_strict",
        local_only=True,
        data_date="2026-08-07",
    )

    assert task.args[11] is True
    assert task.args[12] == "2026-08-07"
    assert task.args[18] is False


def test_live_policy_applies_only_to_tv_strategies(monkeypatch):
    monkeypatch.setattr(scan, "is_a_share_intraday_session", lambda now=None: True)

    assert scan.manual_scan_requires_live_snapshot("tv_dual", None)
    assert scan.manual_scan_requires_live_snapshot("tv_dual_strict", None)
    assert not scan.manual_scan_requires_live_snapshot("pine", None)
    assert not scan.manual_scan_requires_live_snapshot("tv_dual", "2026-08-07")


def test_live_policy_is_disabled_outside_intraday_session(monkeypatch):
    monkeypatch.setattr(scan, "is_a_share_intraday_session", lambda now=None: False)

    assert not scan.manual_scan_requires_live_snapshot(
        "tv_dual_strict", None, now=datetime(2026, 8, 10, 15, 30)
    )


def test_scan_status_returns_metadata_even_when_result_is_empty(monkeypatch):
    class _SuccessResult:
        state = "SUCCESS"
        result = {
            "results": [],
            "scan_meta": {
                "data_date": "2026-08-10",
                "data_mode": "LIVE_SNAPSHOT",
                "as_of": "2026-08-10T13:30:00",
            },
        }

    monkeypatch.setattr(scan.celery_app, "AsyncResult", lambda _task_id: _SuccessResult())

    response = scan.get_scan_status("manual-scan-task")

    assert response["results"] == []
    assert response["scan_meta"]["data_mode"] == "LIVE_SNAPSHOT"
    assert response["scan_meta"]["data_date"] == "2026-08-10"
