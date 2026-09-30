"""_simulate_backtest 参数化测试（perform_market_scan 拆分前的安全网）。

覆盖此前零测试的关键分支：次日开盘入场（T+1 前视消除）、板块感知涨停跳过
（修复：20% 板高开 9.5%~19.5% 被写死的 0.095 误判为涨停）、止损命中。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.strategy import _limit_up_open_threshold, _simulate_backtest


def _series(entry_close=10.0, gap_open=None, days_after=30, daily_move=0.0):
    """构造信号在第 100 根K线的行情；四条数组严格同长、逐日对齐。

    信号日(idx=100)收盘 entry_close；次日(idx=101)开盘 gap_open，此后按
    daily_move 逐日演化 days_after+1 根。"""
    n_pre = 100
    flat = [entry_close] * (n_pre + 1)          # 信号日及之前全部平盘
    closes = list(flat)
    opens = list(flat)
    highs = list(flat)
    lows = list(flat)
    price = gap_open if gap_open is not None else entry_close
    for _ in range(days_after + 1):
        opens.append(price)
        price = price * (1 + daily_move)
        highs.append(price * 1.002)
        lows.append(price * 0.998)
        closes.append(price)
    return (
        np.array(closes, dtype=float),
        np.array(highs, dtype=float),
        np.array(lows, dtype=float),
        np.array(opens, dtype=float),
        [n_pre],
    )


def test_next_open_entry_uses_gap_price():
    """信号次日高开 10.5（信号日收盘 10，+5% 未及涨停容差）→ 入场价必须按 10.5 计。"""
    close_vals, high_vals, low_vals, open_vals, signals = _series(gap_open=10.5)
    bt = _simulate_backtest(
        close_vals, high_vals, low_vals, signals,
        stop_loss_pct=-8, max_hold_days=5,
        use_trailing_stop=False, open_vals=open_vals, code="600000",
    )
    assert bt["signal_count"] == 1
    # 次日开盘 10.5 入场，持有 5 日横盘（10.5 平仓）→ 价差收益 0，仅剩摩擦成本；
    # 若误用信号日收盘 10 入场则收益为 +5%，前视偏差即由此暴露
    assert abs(bt["avg_return"]) < 0.3


def test_limit_up_skip_on_main_board():
    """主板次日开盘 +9.6%（≥9.5% 容差）→ 信号被跳过。"""
    close_vals, high_vals, low_vals, open_vals, signals = _series(gap_open=10.0 * 1.096)
    bt = _simulate_backtest(
        close_vals, high_vals, low_vals, signals,
        stop_loss_pct=-8, max_hold_days=5,
        use_trailing_stop=False, open_vals=open_vals, code="600000",
    )
    assert bt["limit_up_skipped"] == 1
    assert bt["signal_count"] == 0


def test_20pct_board_high_open_not_skipped():
    """修复回归：创业板(300)高开 +15% 可成交，不得按 0.095 误判为涨停跳过。"""
    close_vals, high_vals, low_vals, open_vals, signals = _series(gap_open=10.0 * 1.15)
    bt = _simulate_backtest(
        close_vals, high_vals, low_vals, signals,
        stop_loss_pct=-8, max_hold_days=5,
        use_trailing_stop=False, open_vals=open_vals, code="300001",
    )
    assert bt["limit_up_skipped"] == 0
    assert bt["signal_count"] == 1


def test_star_board_threshold_matches_20pct_limit():
    assert _limit_up_open_threshold("688001") == 0.195
    assert _limit_up_open_threshold("301001") == 0.195
    assert _limit_up_open_threshold("830001") == 0.295
    assert _limit_up_open_threshold("600000") == 0.095
    assert _limit_up_open_threshold("") == 0.095  # 缺 code 回退主板口径


def test_stop_loss_hit_when_low_breaks_stop():
    """入场后价格连续下跌击穿 -8% 止损 → stop_loss_hits>=1，收益为负。"""
    close_vals, high_vals, low_vals, open_vals, signals = _series(gap_open=10.0, days_after=10)
    # 人为把入场后的低点压到 9.0（-10% < -8% 止损）
    low_vals[101:] = 9.0
    close_vals[101:] = 9.0
    high_vals[101:] = 9.05
    bt = _simulate_backtest(
        close_vals, high_vals, low_vals, signals,
        stop_loss_pct=-8, max_hold_days=10,
        use_trailing_stop=False, open_vals=open_vals, code="600000",
    )
    assert bt["stop_loss_hits"] >= 1
    assert bt["avg_return"] < 0
