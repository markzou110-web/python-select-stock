"""Tests for 改动 #13 — 度量统一规范 helper (analytics.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.analytics import (
    compute_equity_curve_drawdown,
    compute_profit_factor,
    compute_win_rate,
)


def test_compute_equity_drawdown_compounding():
    """回撤应基于复利权益（非累加和）：+50% 后 -50% 净值为 75，回撤 50%。"""
    max_dd, curve = compute_equity_curve_drawdown([50.0, -50.0])
    # 复利：100→150→75，峰值 150，回撤 (150-75)/150=50%
    assert max_dd == 50.0
    assert len(curve) == 3  # 起点 + 2 笔
    assert curve[-1]["equity"] == 75.0


def test_compute_equity_drawdown_no_loss():
    """全盈利无回撤。"""
    max_dd, _ = compute_equity_curve_drawdown([10.0, 20.0, 5.0])
    assert max_dd == 0.0


def test_compute_equity_drawdown_empty():
    max_dd, curve = compute_equity_curve_drawdown([])
    assert max_dd == 0.0
    assert len(curve) == 1  # 仅起点


def test_compute_profit_factor_gross_ratio():
    """毛额 profit_factor = 总盈利 / |总亏损|。"""
    # 盈利 30，亏损 15 → 2.0
    assert compute_profit_factor([10, 20, -5, -10]) == 2.0


def test_compute_profit_factor_cap():
    """无亏损但有盈利 → 返回 cap。"""
    assert compute_profit_factor([10, 20], cap=99.0) == 99.0
    assert compute_profit_factor([10, 20], cap=9.9) == 9.9


def test_compute_profit_factor_no_gain_no_loss():
    assert compute_profit_factor([]) == 0.0
    assert compute_profit_factor([0, 0]) == 0.0


def test_compute_win_rate():
    """胜率 = 盈利笔/总数（>0 计盈）。"""
    assert compute_win_rate([5, -3, 2, -1]) == 50.0  # 2 盈 / 4 总
    assert compute_win_rate([5, 3, 0]) == round(2 / 3 * 100, 1)  # 0% 计非盈
    assert compute_win_rate([]) == 0.0


def test_old_additive_drawdown_was_wrong():
    """回归保护：验证旧的累加和算法与复利口径不同（证明修复的必要性）。

    旧实现：running_sum = [50, 0]，peak=50，回撤=50（巧合相同）
    但对 [50, -30, -30]：复利 100→150→105→73.5 回撤 51%；累加 50→20→-10 回撤 60%。
    本测试确认 helper 用的是复利（51%），不是累加（60%）。
    """
    max_dd, _ = compute_equity_curve_drawdown([50.0, -30.0, -30.0])
    # 复利：100→150→105→73.5，峰值150，回撤(150-73.5)/150=51.0%
    assert max_dd == 51.0
    # 若是旧的累加和算法会得到 60（50→20→-10，峰值50回撤60），证明口径已修正
    assert max_dd != 60.0
