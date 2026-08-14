"""Tests for core/score_calibration.py — 历史胜率纳入综合排序分（改动 #6）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.score_calibration import apply_score_display_contract, calibrate_scan_scores


def test_display_scores_are_bounded_without_changing_internal_scores():
    row = {
        "Score": 132.02,
        "calibrated_score": 108,
        "sop_quality_score": 104.5,
        "trade_opportunity_score": -3,
        "final_rank_score": 145,
        "final_trade_score": 121,
    }

    apply_score_display_contract([row])

    assert row["Score"] == 132.02
    assert row["final_rank_score"] == 145
    assert row["final_trade_score"] == 121
    assert row["display_signal_score"] == 100
    assert row["display_quality_score"] == 100
    assert row["display_opportunity_score"] == 0
    assert row["display_rank_score"] == 100
    assert row["display_trade_score"] == 100
    assert row["score_display_scale"] == "0-100"


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


def test_percentile_scores_handles_ties():
    """修复 BUG4：并列分数应取平均排名，而非首个索引。

    [10, 10, 20] → 两个10的百分位应相同（25.0），20应为100。
    原 list.index(10)=0 → 两个10都=0（错），修复后取平均排名(0+1)/2=0.5 → 25。
    """
    from core.score_calibration import _percentile_scores
    result = _percentile_scores([10, 10, 20])
    # 两个并列的10应有相同百分位
    assert result[0] == result[1], f"并列分数百分位应相同，实际 {result}"
    # 20应最高
    assert result[2] > result[0]


def test_weights_sum_to_one():
    """五项权重之和必须为 1.0（保证 calibrated 仍在 0-100 区间）。"""
    from core.score_calibration import (
        W_STRATEGY_PERCENTILE, W_PRICE_ACTION, W_SECTOR_ALIGNMENT,
        W_TRADE_OPPORTUNITY, W_HISTORICAL_WIN_RATE,
    )
    total = (W_STRATEGY_PERCENTILE + W_PRICE_ACTION + W_SECTOR_ALIGNMENT
             + W_TRADE_OPPORTUNITY + W_HISTORICAL_WIN_RATE)
    assert abs(total - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# 改动 A5：权重调参（胜率双重计入 + 小样本折扣）
# ---------------------------------------------------------------------------

def test_execution_first_calibration_weights():
    from core.score_calibration import (
        W_HISTORICAL_WIN_RATE,
        W_PRICE_ACTION,
        W_SECTOR_ALIGNMENT,
        W_STRATEGY_PERCENTILE,
        W_TRADE_OPPORTUNITY,
    )
    assert W_HISTORICAL_WIN_RATE == 0.05, "A5: calibration 历史胜率权重应降至0.05"
    assert W_STRATEGY_PERCENTILE == 0.15
    assert W_PRICE_ACTION == 0.50
    assert W_SECTOR_ALIGNMENT == 0.20
    assert W_TRADE_OPPORTUNITY == 0.10


def test_execution_first_score_penalizes_extension_and_updates_ranking():
    base = {
        "strategy_type": "tv_dual_strict",
        "Score": 80,
        "pa_structure_score": 75,
        "pa_execution_score": 75,
        "pa_risk_score": 75,
        "sector_alignment_score": 80,
        "trade_opportunity_score": 70,
        "历史胜率": "55%",
        "pa_trade_action": "READY",
        "trade_bucket": "OBSERVE",
        "market_regime": "OFFENSIVE",
        "final_rank_score": 90,
        "final_trade_score": 92,
    }
    controlled = {**base, "pct_5d": 6, "涨幅%": 3}
    extended = {**base, "代码": "000002", "pct_5d": 18, "涨幅%": 8}

    calibrate_scan_scores([controlled, extended])

    assert controlled["calibrated_score"] > extended["calibrated_score"]
    assert extended["score_components"]["extension_penalty"] == 15.0
    assert controlled["score_model_version"] == "execution-first-v2"
    assert controlled["final_rank_score"] == round(
        90 + controlled["score_ranking_delta"], 2,
    )


def test_small_sample_win_rate_discount():
    """A5：无 Wilson 下界时对原始胜率打 7 折（小样本惩罚）。"""
    from core.scanner import SMALL_SAMPLE_WIN_RATE_DISCOUNT
    assert SMALL_SAMPLE_WIN_RATE_DISCOUNT == 0.7
    # 样本3笔胜率100% → 折扣后 70%（原逻辑用100%严重高估）
    assert 100 * SMALL_SAMPLE_WIN_RATE_DISCOUNT == 70.0


def test_parse_win_rate_prefers_reliability_adjusted():
    """提高胜率区分度：优先用 Wilson 下界胜率，避免小样本虚高。

    2 笔交易 100% 胜率 → Wilson 下界 ≈ 42.9%（而非虚高的 100%）。
    回测统计缺失时才回退到原始 历史胜率 字符串。
    """
    from core.score_calibration import _parse_win_rate

    # 优先用 adjusted_win_rate
    row = {"历史胜率": "100%", "回测统计": {"adjusted_win_rate": 42.9}}
    assert _parse_win_rate(row) == 42.9

    # 回测统计缺失 → 回退原始字符串
    assert _parse_win_rate({"历史胜率": "75%"}) == 75.0

    # 无任何数据 → 中性 50
    assert _parse_win_rate({}) == 50.0
