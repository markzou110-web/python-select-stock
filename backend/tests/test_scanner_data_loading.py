from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from core import scanner


@pytest.mark.parametrize(
    "strategy_type,keep_candidate",
    [("squeeze", True), ("tv_dual_strict", True), ("squeeze", False)],
)
def test_data_loading_reads_candidate_context_and_persists_filtered_history(
    monkeypatch, strategy_type, keep_candidate
):
    candidates = pd.DataFrame({"code": ["600667", "002015"]})
    history = pd.DataFrame({
        "code": ["600667", "002015"],
        "日期": ["2026-09-30", "2026-09-30"],
        "收盘": [17.81, 17.19],
    })
    snapshot = candidates.copy()
    snapshot.attrs["data_date"] = "2026-09-30"
    ctx = SimpleNamespace(
        data_date="2026-09-30",
        strategy_type=strategy_type,
        snapshot_df=snapshot,
        candidates=candidates,
        audit_payload={"params_snapshot": {}},
        resolved_data_date="2026-09-30",
        scan_started_at=datetime.now(),
        hist_map={},
    )
    engine = MagicMock()
    query_codes = []

    def read_history(query, connection, params):
        query_codes.extend(value for key, value in params.items() if key.startswith("code_"))
        return history.copy()

    def filter_candidates(received, loaded):
        pd.testing.assert_frame_equal(received, candidates)
        pd.testing.assert_frame_equal(loaded, history)
        return received.iloc[:1 if keep_candidate else 0].copy(), {}

    audit_save = MagicMock()
    pine_calculate = MagicMock(side_effect=lambda frame: frame)
    monkeypatch.setattr(scanner, "get_db_engine", lambda: engine)
    monkeypatch.setattr(scanner, "get_index_hist", lambda code: pd.DataFrame())
    monkeypatch.setattr(scanner.pd, "read_sql", read_history)
    monkeypatch.setattr(scanner, "_apply_liquidity_and_new_stock_filters", filter_candidates)
    monkeypatch.setattr(scanner, "batch_calculate_indicators", lambda frame, **kwargs: frame)
    monkeypatch.setattr(scanner, "calculate_pine_indicators", pine_calculate)
    monkeypatch.setattr(scanner, "save_scan_audit_log", audit_save)
    phases = []

    empty = scanner._scan_load_data(ctx, phases.append)

    assert query_codes == ["600667", "002015"]
    assert empty is not keep_candidate
    assert ctx.audit_payload["candidate_count"] == int(keep_candidate)
    assert ctx.candidates["code"].tolist() == (["600667"] if keep_candidate else [])
    if keep_candidate:
        assert set(ctx.hist_map) == {"600667"}
        assert phases == ["indicator_batch"]
        audit_save.assert_not_called()
        assert pine_calculate.call_count == int(strategy_type == "tv_dual_strict")
    else:
        assert ctx.hist_map == {}
        audit_save.assert_not_called()


def test_initial_empty_candidates_do_not_query_history(monkeypatch):
    ctx = SimpleNamespace(
        data_date="2026-09-30", strategy_type="squeeze",
        snapshot_df=pd.DataFrame(), candidates=pd.DataFrame(), audit_payload={},
    )
    history_query = MagicMock()
    benchmark = MagicMock()
    monkeypatch.setattr(scanner, "get_db_engine", lambda: object())
    monkeypatch.setattr(scanner.pd, "read_sql", history_query)
    monkeypatch.setattr(scanner, "get_index_hist", benchmark)
    assert scanner._scan_load_data(ctx, lambda phase: None) is True
    assert ctx.start_time is not None
    history_query.assert_not_called()
    benchmark.assert_not_called()


def test_missing_history_preserves_http_404(monkeypatch):
    from fastapi import HTTPException

    ctx = SimpleNamespace(
        data_date="2026-09-30", strategy_type="squeeze",
        snapshot_df=pd.DataFrame(), candidates=pd.DataFrame({"code": ["600667"]}),
        audit_payload={},
    )
    monkeypatch.setattr(scanner, "get_db_engine", lambda: MagicMock())
    monkeypatch.setattr(scanner, "get_index_hist", lambda code: pd.DataFrame())
    monkeypatch.setattr(scanner.pd, "read_sql", lambda *args, **kwargs: pd.DataFrame())
    with pytest.raises(HTTPException) as error:
        scanner._scan_load_data(ctx, lambda phase: None)
    assert error.value.status_code == 404
