from datetime import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import scan


class _PendingTaskResult:
    id = "manual-scan-task"
    state = "PENDING"


def test_scan_capabilities_advertise_match_modes():
    assert scan.scan_capabilities() == {"match_modes": ["any", "all"]}


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
    assert scan.manual_scan_requires_live_snapshot("tv_zp", None)
    assert scan.manual_scan_requires_live_snapshot("h2", None)
    assert not scan.manual_scan_requires_live_snapshot("pine", None)
    assert not scan.manual_scan_requires_live_snapshot("tv_dual", "2026-08-07")


def test_manual_multi_strategy_scan_passes_selection_to_task(monkeypatch):
    task = _CapturedTask()
    monkeypatch.setattr(scan, "run_market_scan_task", task)
    monkeypatch.setattr(scan, "is_a_share_intraday_session", lambda now=None: True)

    scan.scan_market(
        strategy_type="h2",
        strategy_types="h2,consensus",
        match_mode="all",
        local_only=True,
        data_date="",
    )

    assert task.args[11] is False
    assert task.args[18] is True
    assert task.args[20] == "h2,consensus"
    assert task.args[21] == "all"


def test_multi_strategy_match_mode_uses_union_or_intersection():
    rows = [
        {"代码": "000001", "strategy_type": "h2", "Score": 60},
        {"代码": "000001", "strategy_type": "consensus", "Score": 70},
        {"代码": "000002", "strategy_type": "h2", "Score": 80},
    ]
    selected = ["h2", "consensus"]

    any_results = scan._merge_strategy_results(rows, selected, "any")
    all_results = scan._merge_strategy_results(rows, selected, "all")

    assert {item["代码"] for item in any_results} == {"000001", "000002"}
    assert [item["代码"] for item in all_results] == ["000001"]
    assert all_results[0]["matched_strategies"] == selected


def test_all_mode_filters_task_results_without_publishing_unfiltered_watchlist(monkeypatch):
    from core import scanner, sentinel

    calls = []
    sent = []

    def fake_scan(**kwargs):
        calls.append((kwargs["strategy_type"], kwargs["publish_to_sentinel"]))
        strategy = kwargs["strategy_type"]
        codes = ["000001", "000002"] if strategy == "tv_dual" else ["000001"]
        return [{"代码": code, "strategy_type": strategy, "Score": 50} for code in codes]

    monkeypatch.setattr(scanner, "perform_market_scan", fake_scan)
    monkeypatch.setattr(scan, "record_lifecycle_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(sentinel, "send_after_close_watchlist", lambda *args, **kwargs: sent.append(True))

    result = scan.run_market_scan_task(
        strategy_type="tv_dual", strategy_types="tv_dual,weekly_four_patterns",
        match_mode="all", include_scan_metadata=True,
    )

    assert [item["代码"] for item in result["results"]] == ["000001"]
    assert result["scan_meta"]["match_mode"] == "all"
    assert calls == [("tv_dual", False), ("weekly_four_patterns", False)]
    assert sent == []


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
