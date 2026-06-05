import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.backtest_lab import run_single_stock_backtest


def _pine_fixture(rows: int = 150) -> pd.DataFrame:
    start = date(2025, 1, 1)
    close = np.linspace(10, 14, rows)
    df = pd.DataFrame({
        "日期": [(start + timedelta(days=i)).isoformat() for i in range(rows)],
        "开盘": close - 0.5,
        "最高": close + 0.1,
        "最低": close - 0.2,
        "收盘": close,
        "成交量": np.full(rows, 1_000_000.0),
        "Vol_MA20": np.full(rows, 500_000.0),
        "ATR": np.full(rows, 0.3),
        "RF_Upward": False,
        "RF_Downward": False,
        "ST_Signal": False,
        "RQK_Up": False,
        "HalfTrend_Up": False,
        "QQE_Long": False,
    })
    for idx in [125, 138]:
        df.loc[idx, ["RF_Upward", "ST_Signal", "RQK_Up", "HalfTrend_Up", "QQE_Long"]] = True
    return df


def test_single_stock_backtest_returns_trades_and_equity_curve():
    result = run_single_stock_backtest(
        _pine_fixture(),
        strategy_type="pine",
        params={"pine_min_signals": 3, "max_hold_days": 5, "stop_loss_pct": -8},
    )

    assert result["summary"]["signal_count"] >= 1
    assert result["summary"]["final_equity"] > 100000
    assert result["trades"][0]["entry_date"] == "2025-05-06"
    assert result["trades"][0]["return_pct"] > 0
    assert len(result["equity_curve"]) == result["summary"]["signal_count"] + 1


def test_single_stock_backtest_handles_no_signal():
    df = _pine_fixture()
    df[["RF_Upward", "ST_Signal", "RQK_Up", "HalfTrend_Up", "QQE_Long"]] = False

    result = run_single_stock_backtest(df, strategy_type="pine")

    assert result["summary"]["signal_count"] == 0
    assert result["trades"] == []
    assert result["reason"] == "回测区间内无策略信号"


def test_next_open_backtest_skips_high_open_signal():
    df = _pine_fixture()
    signal_idx = 125
    df.loc[signal_idx + 1, "开盘"] = df.loc[signal_idx, "收盘"] * 1.05

    result = run_single_stock_backtest(
        df,
        strategy_type="pine",
        params={
            "pine_min_signals": 3,
            "entry_mode": "next_open_confirm",
            "max_open_gap_pct": 3,
            "max_hold_days": 5,
        },
    )

    assert result["summary"]["skipped_high_open"] == 1
    assert result["trades"][0]["signal_date"] == "2025-05-19"
    assert result["trades"][0]["entry_date"] == "2025-05-20"


def test_backtest_skips_suspected_adjustment_gap_trade():
    df = _pine_fixture()
    signal_idx = 125
    df.loc[signal_idx + 1, "开盘"] = df.loc[signal_idx, "收盘"] * 0.68
    df.loc[signal_idx + 1, "最高"] = df.loc[signal_idx, "收盘"] * 0.69
    df.loc[signal_idx + 1, "最低"] = df.loc[signal_idx, "收盘"] * 0.66
    df.loc[signal_idx + 1, "收盘"] = df.loc[signal_idx, "收盘"] * 0.67

    result = run_single_stock_backtest(
        df,
        strategy_type="pine",
        params={"pine_min_signals": 3, "max_hold_days": 5},
    )

    assert result["summary"]["skipped_adjustment_gap"] == 1
    assert result["trades"][0]["signal_date"] == "2025-05-19"


def test_backtest_applies_slippage_position_and_lot_size():
    result = run_single_stock_backtest(
        _pine_fixture(),
        strategy_type="pine",
        params={
            "pine_min_signals": 3,
            "max_hold_days": 5,
            "slippage_bps": 10,
            "position_pct": 0.5,
            "lot_size": 100,
        },
    )

    trade = result["trades"][0]
    assert result["summary"]["slippage_bps"] == 10
    assert result["summary"]["position_pct"] == 0.5
    assert trade["shares"] % 100 == 0
    assert trade["entry_price"] > trade["raw_entry_price"]
    assert trade["exit_price"] < trade["raw_exit_price"]
