from types import SimpleNamespace
import time
from unittest.mock import MagicMock

from fastapi import HTTPException
import pandas as pd
import pytest

from core import scanner


@pytest.fixture
def empty_scan(monkeypatch):
    from core import db, decision_layer, limit_up_leadership, sentinel, strategy_health

    ctx = SimpleNamespace(
        engine=object(), results=[], audit_payload={"params_snapshot": {}, "version_snapshot": {}},
        snapshot_df=pd.DataFrame(), max_date="2026-09-30", data_date="2026-09-30",
        snapshot_as_of="2026-09-30", data_mode="LOCAL_DB", resolved_data_date="2026-09-30",
        discovery_pool="TREND", phase_timings={}, start_time=time.time(), strategy_type="tv_dual",
        publish_to_sentinel=True, market_regime={}, sector_trends={},
    )
    monkeypatch.setattr(limit_up_leadership, "load_limit_up_event_map", lambda *args: {})
    monkeypatch.setattr(scanner, "load_active_event_catalysts", lambda *args, **kwargs: [])
    monkeypatch.setattr(decision_layer, "load_market_cycle_history", lambda *args, **kwargs: [])
    monkeypatch.setattr(scanner, "apply_decision_layer", lambda *args, **kwargs: {})
    monkeypatch.setattr(strategy_health, "build_strategy_health", lambda *args: {})
    monkeypatch.setattr(db, "load_sector_fund_flow_map", lambda *args: {})
    old_candidates = [{"代码": "600667"}]
    monkeypatch.setattr(sentinel.sentinel, "last_top_5", old_candidates)
    save = MagicMock(return_value=True)
    audit = MagicMock()
    broadcast = MagicMock()
    monkeypatch.setattr(scanner, "save_scan_results", save)
    monkeypatch.setattr(scanner, "save_scan_audit_log", audit)
    monkeypatch.setattr(scanner.ws_manager, "broadcast_threadsafe", broadcast)
    return ctx, save, audit, broadcast, sentinel.sentinel, old_candidates


def test_empty_scan_completes_persistence_and_clears_execution_candidates(empty_scan):
    ctx, save, audit, broadcast, sentinel, _ = empty_scan
    scanner._scan_decide_and_persist(ctx, lambda phase: None)
    save.assert_called_once_with([], ctx.engine, data_date="2026-09-30", replace_strategy_types=["tv_dual"])
    assert sentinel.last_top_5 == []
    assert ctx.audit_payload["result_count"] == 0
    audit.assert_called_once()
    assert broadcast.call_args.args[0]["type"] == "scan_end"


def test_failed_result_save_never_announces_success_or_changes_candidates(empty_scan):
    ctx, save, audit, broadcast, sentinel, old_candidates = empty_scan
    save.return_value = False
    with pytest.raises(HTTPException) as error:
        scanner._scan_decide_and_persist(ctx, lambda phase: None)
    assert error.value.status_code == 500
    assert "保存失败" in error.value.detail
    assert sentinel.last_top_5 is old_candidates
    audit.assert_not_called()
    broadcast.assert_not_called()


def test_non_execution_scan_preserves_sentinel_candidates(empty_scan):
    ctx, _, _, _, sentinel, old_candidates = empty_scan
    ctx.publish_to_sentinel = False
    scanner._scan_decide_and_persist(ctx, lambda phase: None)
    assert sentinel.last_top_5 is old_candidates


def test_orchestrator_finishes_empty_scan_instead_of_skipping_persistence(monkeypatch):
    def prepare(ctx, mark):
        ctx.max_date = ctx.resolved_data_date = "2026-09-30"
        return False

    completion = MagicMock()
    monkeypatch.setattr(scanner, "_scan_prepare_environment", prepare)
    monkeypatch.setattr(scanner, "_scan_filter_candidates", lambda ctx, mark: None)
    monkeypatch.setattr(scanner, "_scan_load_data", lambda ctx, mark: True)
    monkeypatch.setattr(scanner, "_scan_decide_and_persist", completion)
    assert scanner.perform_market_scan() == []
    completion.assert_called_once()


@pytest.mark.parametrize("initial_empty", [True, False])
@pytest.mark.parametrize("save_succeeds", [True, False])
def test_real_empty_loading_and_completion_record_final_status(
    monkeypatch, empty_scan, initial_empty, save_succeeds
):
    _, save, audit, broadcast, sentinel, old_candidates = empty_scan
    engine = MagicMock()
    save.return_value = save_succeeds
    candidates = pd.DataFrame({"code": [] if initial_empty else ["600667"]})
    history = pd.DataFrame({
        "code": ["600667"] * 6, "日期": pd.date_range("2026-09-23", periods=6),
        "开盘": [10.] * 6, "最高": [11.] * 6, "最低": [9.] * 6,
        "收盘": [10.] * 6, "成交量": [100.] * 6,
    })

    def prepare(ctx, mark):
        ctx.engine = engine
        ctx.max_date = ctx.resolved_data_date = ctx.snapshot_as_of = "2026-09-30"
        ctx.data_mode = "LOCAL_DB"
        ctx.snapshot_df = candidates.copy()
        ctx.snapshot_df.attrs["data_date"] = "2026-09-30"
        return False

    def filter_initial(ctx, mark):
        ctx.candidates = candidates.copy()

    evaluation = MagicMock()
    monkeypatch.setattr(scanner, "_scan_prepare_environment", prepare)
    monkeypatch.setattr(scanner, "_scan_filter_candidates", filter_initial)
    monkeypatch.setattr(scanner, "_scan_evaluate_candidates", evaluation)
    monkeypatch.setattr(scanner, "get_db_engine", lambda: engine)
    monkeypatch.setattr(scanner, "get_index_hist", lambda code: pd.DataFrame())
    monkeypatch.setattr(scanner.pd, "read_sql", lambda *args, **kwargs: history.copy())

    if save_succeeds:
        assert scanner.perform_market_scan(strategy_type="tv_dual", data_date="2026-09-30") == []
        assert sentinel.last_top_5 == []
        assert broadcast.call_args.args[0]["type"] == "scan_end"
    else:
        with pytest.raises(HTTPException) as error:
            scanner.perform_market_scan(strategy_type="tv_dual", data_date="2026-09-30")
        assert error.value.status_code == 500
        assert sentinel.last_top_5 is old_candidates
        broadcast.assert_not_called()
    evaluation.assert_not_called()
    save.assert_called_once_with([], engine, data_date="2026-09-30", replace_strategy_types=["tv_dual"])
    audit.assert_called_once()
    assert audit.call_args.args[0]["status"] == ("SUCCESS" if save_succeeds else "FAILED")


@pytest.mark.parametrize("matched", [True, False])
def test_candidate_evaluation_completes_with_timer_from_context(monkeypatch, matched):
    import core.sequoia_research as research

    rows = pd.DataFrame([{"code": "600667", "name": "太极实业", "price": 17.81,
                          "vol": 300000, "open": 18.0}])
    ctx = SimpleNamespace(
        engine=MagicMock(), candidates=rows, hist_map={}, bench_slice=None, start_time=time.time(),
        audit_payload={"params_snapshot": {}}, snapshot_df=rows, max_date="2026-09-30",
        data_date="2026-09-30", resolved_data_date="2026-09-30", strategy_type="trader_vic_2b",
        threshold=.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True, use_bb_sqz=False,
        sqz_lookback=10, use_weekly=False, use_rs_filter=False, local_only=True,
        pine_min_signals=3, min_data_days=220, weekly_ma_period=20, tv_weekly_gate=True,
    )
    monkeypatch.setattr(scanner, "single_stock_task", lambda *args, **kwargs:
                        {"代码": "600667", "Score": 80} if matched else {"reason": "未满足研究形态"})
    for name in ("_build_scan_money_flow_map", "get_sector_map", "get_sector_trends", "get_market_regime",
                 "build_sector_history_context", "build_previous_month_sector_context", "build_sector_strength"):
        monkeypatch.setattr(scanner, name, lambda *args, **kwargs: {})
    monkeypatch.setattr(research, "load_cross_sectional_rps", lambda *args: {"600667": {"rps_120": 90}})
    phases = []
    scanner._scan_evaluate_candidates(ctx, phases.append)
    assert len(ctx.results) == int(matched)
    assert phases == ["strategy_evaluation", "strategy_post_filter"]
