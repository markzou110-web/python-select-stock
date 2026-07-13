"""Detect indicator and signal changes caused by data after the audited date."""
from typing import Any, Callable, Dict, Iterable

import numpy as np
import pandas as pd

from core.indicators import calculate_indicators
from core.strategy import (
    _find_consensus_signal_indices,
    _find_pine_signal_indices,
    _find_squeeze_signal_indices,
    _find_tv_zp_signal_indices,
)


AUDIT_VERSION = "causal-prefix-audit-v1"
DEFAULT_COLUMNS = (
    "EMA5", "EMA10", "EMA20", "EMA60", "RSI", "RSI_WILDER", "MACD_DIF",
    "MACD_DEA", "MACD_HIST", "BB_Mid", "BB_Upper", "BB_Lower", "BB_Width",
    "Vol_MA20", "ATR", "Sqz_Ratio", "RF_Filter", "RQK_Line",
)


def _signals(frame: pd.DataFrame) -> Dict[str, bool]:
    last = len(frame) - 1
    tv_long, _tv_short, _debug = _find_tv_zp_signal_indices(frame)
    return {
        "squeeze": last in _find_squeeze_signal_indices(frame, 0.12, 1.5, 55),
        "pine": last in _find_pine_signal_indices(frame, 3),
        "consensus": last in _find_consensus_signal_indices(frame),
        "tv_zp": last in tv_long,
    }


def _equal(left: Any, right: Any, tolerance: float) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    if isinstance(left, (bool, np.bool_)) or isinstance(right, (bool, np.bool_)):
        return bool(left) == bool(right)
    try:
        return bool(np.isclose(float(left), float(right), rtol=tolerance, atol=tolerance, equal_nan=True))
    except (TypeError, ValueError):
        return str(left) == str(right)


def audit_causal_consistency(
    raw: pd.DataFrame,
    *,
    sample_points: int = 20,
    warmup: int = 80,
    columns: Iterable[str] = DEFAULT_COLUMNS,
    tolerance: float = 1e-9,
    indicator_calculator: Callable[..., pd.DataFrame] = calculate_indicators,
    check_signals: bool = True,
) -> Dict[str, Any]:
    """Compare full-history results with independently recomputed date prefixes."""
    required = {"日期", "开盘", "最高", "最低", "收盘", "成交量"}
    if raw is None or raw.empty or not required.issubset(raw.columns):
        return {"version": AUDIT_VERSION, "status": "INSUFFICIENT_DATA", "checked_points": 0, "mismatches": []}
    source = raw.sort_values("日期").reset_index(drop=True).copy()
    if len(source) < warmup:
        return {"version": AUDIT_VERSION, "status": "INSUFFICIENT_DATA", "checked_points": 0, "mismatches": []}

    # A constant benchmark prevents network access while preserving the causal RS calculation path.
    benchmark = pd.DataFrame({"日期": source["日期"], "收盘": 1.0})
    full = indicator_calculator(source.copy(), bench_df=benchmark.copy(), enable_pine_indicators=True)
    start = max(warmup - 1, len(source) - max(1, int(sample_points)))
    points = list(range(start, len(source)))
    mismatches = []
    audited_columns = [column for column in columns if column in full.columns]
    for idx in points:
        prefix_raw = source.iloc[:idx + 1].copy()
        prefix_benchmark = benchmark.iloc[:idx + 1].copy()
        prefix = indicator_calculator(prefix_raw, bench_df=prefix_benchmark, enable_pine_indicators=True)
        for column in audited_columns:
            if column not in prefix.columns or not _equal(full.iloc[idx][column], prefix.iloc[-1][column], tolerance):
                mismatches.append({
                    "date": str(source.iloc[idx]["日期"])[:10], "kind": "indicator", "field": column,
                    "full_value": None if column not in full.columns or pd.isna(full.iloc[idx][column]) else full.iloc[idx][column],
                    "prefix_value": None if column not in prefix.columns or pd.isna(prefix.iloc[-1].get(column)) else prefix.iloc[-1][column],
                })
        if check_signals:
            full_signals = _signals(full.iloc[:idx + 1].copy())
            prefix_signals = _signals(prefix)
            for strategy, value in full_signals.items():
                if value != prefix_signals[strategy]:
                    mismatches.append({
                        "date": str(source.iloc[idx]["日期"])[:10], "kind": "signal", "field": strategy,
                        "full_value": value, "prefix_value": prefix_signals[strategy],
                    })
    return {
        "version": AUDIT_VERSION,
        "status": "FAILED" if mismatches else "PASSED",
        "checked_points": len(points),
        "checked_columns": audited_columns,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:100],
        "notes": ["PASSED仅说明被抽样日期的全量计算与逐日截断计算一致，不代表策略有正收益。"],
    }
