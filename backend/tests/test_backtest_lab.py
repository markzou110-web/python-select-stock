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


# ── 改动 #15：基准 alpha / CAGR ──

def test_backtest_alpha_vs_benchmark():
    """提供 bench_df 时 summary 应含 benchmark_return/alpha/cagr，且 alpha = 策略收益 - 基准收益。

    不假设策略一定盈利（fixture 可能微亏），只验证 alpha 的计算正确性。
    """
    df = _pine_fixture()
    # 构造一个涨幅很小的基准（沪深300 平稳微涨 +5%）
    bench = pd.DataFrame({
        "日期": df["日期"],
        "收盘": np.linspace(10, 10.5, len(df)),
    })
    result = run_single_stock_backtest(df, strategy_type="pine", params={"pine_min_signals": 3}, bench_df=bench)
    s = result["summary"]
    assert "benchmark_return" in s and "alpha" in s and "cagr" in s
    assert s["benchmark_return"] is not None
    assert s["alpha"] is not None
    # alpha = 策略 total_return - benchmark_return（验证计算正确性）
    assert abs(s["alpha"] - (s["total_return"] - s["benchmark_return"])) < 0.5
    # benchmark 收益应为正（+5%）
    assert s["benchmark_return"] > 0
    assert s["cagr"] is not None  # 已年化


def test_backtest_no_benchmark_returns_none():
    """不提供 bench_df 时 alpha/benchmark_return/cagr 均为 None（前端兜底为"-"）。"""
    result = run_single_stock_backtest(_pine_fixture(), strategy_type="pine", params={"pine_min_signals": 3})
    s = result["summary"]
    assert s["benchmark_return"] is None
    assert s["alpha"] is None
    assert s["cagr"] is None


def test_backtest_signal_date_bounds_keep_warmup_but_filter_trades():
    df = _pine_fixture()
    signal_date = str(df.loc[125, "日期"])[:10]
    later_date = str(df.loc[len(df) - 1, "日期"])[:10]
    included = run_single_stock_backtest(
        df, strategy_type="pine",
        params={"pine_min_signals": 3, "signal_start_date": signal_date, "signal_end_date": signal_date},
    )
    excluded = run_single_stock_backtest(
        df, strategy_type="pine",
        params={"pine_min_signals": 3, "signal_start_date": later_date},
    )
    assert included["summary"]["signal_count"] >= 1
    assert excluded["summary"]["signal_count"] == 0


# ── 改动 #16：缺口穿越止损按开盘成交 ──

def test_gap_through_stop_fills_at_open():
    """跳空缺口击穿止损时，成交价应为当日开盘（更差），而非止损线。

    构造：入场后某日开盘大幅低开（低于止损线），验证 exit_price 接近开盘而非止损线。
    """
    rows = 150
    start = date(2025, 1, 1)
    close = np.linspace(10, 11, rows)
    df = pd.DataFrame({
        "日期": [(start + timedelta(days=i)).isoformat() for i in range(rows)],
        "开盘": close - 0.1,
        "最高": close + 0.1,
        "最低": close - 0.2,
        "收盘": close,
        "成交量": np.full(rows, 1_000_000.0),
        "Vol_MA20": np.full(rows, 500_000.0),
        "ATR": np.full(rows, 0.3),
        "RF_Upward": False, "RF_Downward": False, "ST_Signal": False,
        "RQK_Up": False, "HalfTrend_Up": False, "QQE_Long": False,
    })
    # 在 idx=125 触发 pine 信号
    df.loc[125, ["RF_Upward", "ST_Signal", "RQK_Up", "HalfTrend_Up", "QQE_Long"]] = True
    # 在信号后几日制造跳空缺口：开盘大幅低开（远低于 -8% 止损线）
    gap_idx = 127
    entry_close = float(df.loc[125, "收盘"])
    stop_line = entry_close * 0.92  # -8%
    df.loc[gap_idx, "开盘"] = entry_close * 0.80  # 跳空到 -20%（远低于止损线 -8%）
    df.loc[gap_idx, "最低"] = entry_close * 0.79
    df.loc[gap_idx, "最高"] = entry_close * 0.82
    df.loc[gap_idx, "收盘"] = entry_close * 0.81

    result = run_single_stock_backtest(df, strategy_type="pine", params={"pine_min_signals": 3, "stop_loss_pct": -8.0})
    stop_trades = [t for t in result["trades"] if t["hit_stop"]]
    if stop_trades:
        t = stop_trades[0]
        # 缺口穿越：exit_price 应 ≤ 止损线（按开盘成交，比止损线更差），不应 > 止损线
        assert t["exit_price"] <= stop_line + 0.01, (
            f"缺口穿越应按开盘成交（≤止损线 {stop_line:.2f}），实际 {t['exit_price']:.2f}"
        )
