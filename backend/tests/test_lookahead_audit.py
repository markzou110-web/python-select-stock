import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.indicators import calculate_indicators
from core.lookahead_audit import audit_causal_consistency


def _history(size=120):
    close = np.linspace(10, 14, size) + np.sin(np.arange(size) / 5) * 0.2
    return pd.DataFrame({
        "日期": pd.date_range("2025-01-01", periods=size, freq="B"),
        "开盘": close * 0.998, "最高": close * 1.02, "最低": close * 0.98,
        "收盘": close, "成交量": np.linspace(10000, 20000, size),
    })


def test_current_indicators_and_signals_are_prefix_consistent():
    report = audit_causal_consistency(_history(), sample_points=5)
    assert report["status"] == "PASSED"
    assert report["checked_points"] == 5
    assert report["mismatch_count"] == 0


def test_audit_detects_calculator_that_reads_future_rows():
    def cheating_calculator(df, **kwargs):
        result = calculate_indicators(df, **kwargs)
        result["CHEAT"] = float(df["收盘"].iloc[-1])
        return result

    report = audit_causal_consistency(
        _history(), sample_points=3, columns=["CHEAT"], indicator_calculator=cheating_calculator,
        check_signals=False,
    )
    assert report["status"] == "FAILED"
    assert report["mismatch_count"] > 0
    assert report["mismatches"][0]["field"] == "CHEAT"
