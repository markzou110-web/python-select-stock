import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.scanner import _apply_sop_filter, single_stock_task


def _base_candidate(**overrides):
    candidate = {
        "代码": "000001",
        "名称": "测试股票",
        "行业": "小金属",
        "Score": 90,
        "涨幅%": 2.5,
        "影线比": 0.1,
        "pct_5d": 4.0,
        "历史胜率": "58%",
        "回测统计": {"profit_factor": 1.8},
        "ROE": 9,
        "净利YOY": 18,
        "strategy_type": "tv_dual",
        "pa_trade_plan": {"action": "READY"},
        "pa_trade_setup": "H2二次入场",
        "price_action_score": 72,
        "sector_momentum_score": 82,
        "sector_breadth": 70,
        "sector_alignment_score": 80,
    }
    candidate.update(overrides)
    return candidate


def test_ready_candidate_enters_trade_bucket():
    results = [_base_candidate()]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "A"
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert result["trade_blockers"] == []
    assert result["final_trade_score"] > result["final_rank_score"]


def test_avoid_action_is_blocked_even_with_good_scores():
    results = [_base_candidate(pa_trade_plan={"action": "AVOID"})]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "D"
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "BLOCK"
    assert "价格行为回避" in result["sop_vetoes"]


def test_low_quality_setup_is_blocked():
    results = [_base_candidate(pa_trade_plan={"action": "WATCH"}, pa_trade_setup="外包K")]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "D"
    assert result["trade_bucket"] == "BLOCK"
    assert any("外包K" in blocker for blocker in result["trade_blockers"])


def test_pine_candidate_gets_short_term_management_hint():
    results = [_base_candidate(strategy_type="pine", pa_trade_plan={}, pa_trade_setup="")]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_timeframe"] == "SHORT_1_2D"
    assert "1-2" in result["exit_hint"]


def test_strong_daily_mover_keeps_quality_grade_but_is_observe_only():
    results = [_base_candidate(**{"涨幅%": 8.2})]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "A"
    assert "涨幅>7%" not in result["sop_vetoes"]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "涨幅偏高，等待回踩确认" in result["trade_blockers"]


def test_near_limit_threshold_respects_board_limit():
    main = _base_candidate(代码="000001", **{"涨幅%": 9.8})
    chinext = _base_candidate(代码="300001", **{"涨幅%": 9.8})

    _apply_sop_filter([main, chinext], {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert "涨停/近涨停，等待隔日确认" in main["trade_blockers"]
    assert "涨停/近涨停，等待隔日确认" not in chinext["trade_blockers"]
    assert "涨幅偏高，等待回踩确认" in chinext["trade_blockers"]


def test_five_day_surge_is_ranking_risk_not_sop_veto():
    normal = _base_candidate()
    surged = _base_candidate(**{"pct_5d": 16.0})

    _apply_sop_filter([normal, surged], {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert surged["sop_grade"] == "A"
    assert "5日涨>15%" not in surged["sop_vetoes"]
    assert surged["sop_risks"] == ["5日涨幅>15%，排序扣分"]
    assert surged["final_rank_score"] == normal["final_rank_score"] - 6


def test_single_stock_task_rejects_abnormal_price_jump():
    close = np.full(130, 10.0)
    close[-2] = 13.0
    close[-1] = 10.0
    df = pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=130, freq="D"),
        "收盘": close,
    })

    result = single_stock_task(
        "000001",
        "异常股票",
        price=10,
        vol=100000,
        open_price=10,
        threshold=0.12,
        vol_multiplier=1.5,
        rsi_min=55,
        use_macd_filter=True,
        use_bb_sqz=False,
        sqz_lookback=10,
        use_weekly=False,
        preloaded_df=df,
        strategy_type="tv_dual",
    )

    assert result["reason"].startswith("异常价格跳变")
