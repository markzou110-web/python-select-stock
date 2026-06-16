"""Tests for core/score_calibration.py — 历史胜率纳入综合排序分（改动 #6）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.score_calibration import calibrate_scan_scores


def test_calibrated_score_includes_win_rate():
    """两只其它分相同但历史胜率不同 → 胜率高者 calibrated_score 更高。"""
    base = {
        "strategy_type": "tv_dual_strict",
        "Score": 85,
        "pa_structure_score": 60,
        "pa_execution_score": 60,
        "pa_risk_score": 60,
        "price_action_score": 60,
        "sector_alignment_score": 60,
        "trade_opportunity_score": 60,
        "pa_trade_action": "BREAKOUT",
    }
    low_wr = {**base, "历史胜率": "40%"}
    high_wr = {**base, "历史胜率": "75%"}
    calibrate_scan_scores([low_wr, high_wr])

    assert high_wr["calibrated_score"] > low_wr["calibrated_score"]
    # 胜率分量应体现在 components
    assert high_wr["score_components"]["historical_win_rate"] == 75.0
    assert low_wr["score_components"]["historical_win_rate"] == 40.0


def test_win_rate_missing_defaults_to_neutral():
    """无历史胜率数据时，win_rate 分量取中性 50，不拉高也不拉低。"""
    row = {
        "strategy_type": "tv_dual_strict",
        "Score": 85,
        "pa_structure_score": 60,
        "pa_execution_score": 60,
        "pa_risk_score": 60,
        "sector_alignment_score": 60,
        "trade_opportunity_score": 60,
        "pa_trade_action": "BREAKOUT",
    }
    calibrate_scan_scores([row])
    assert row["score_components"]["historical_win_rate"] == 50.0


def test_weights_sum_to_one():
    """五项权重之和必须为 1.0（保证 calibrated 仍在 0-100 区间）。"""
    from core.score_calibration import (
        W_STRATEGY_PERCENTILE, W_PRICE_ACTION, W_SECTOR_ALIGNMENT,
        W_TRADE_OPPORTUNITY, W_HISTORICAL_WIN_RATE,
    )
    total = (W_STRATEGY_PERCENTILE + W_PRICE_ACTION + W_SECTOR_ALIGNMENT
             + W_TRADE_OPPORTUNITY + W_HISTORICAL_WIN_RATE)
    assert abs(total - 1.0) < 1e-9
