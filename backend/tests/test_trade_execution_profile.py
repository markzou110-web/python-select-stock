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
        "price_action_signal": "强多头趋势K",
        "pa_volume_confirmed": True,
        "pa_entry_price": 10.0,
        "pa_risk_pct": 6.0,
        "现价": 10.05,
        "sector_momentum_score": 82,
        "sector_breadth": 70,
        "sector_alignment_score": 80,
        "共振": "🔥 核心热点",  # 多重共振标记（调整3 严格门槛要求）
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
    results = [_base_candidate(**{"涨幅%": 8.2, "pa_volume_confirmed": False, "price_action_signal": "普通突破"})]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "A"
    assert "涨幅>7%" not in result["sop_vetoes"]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "涨幅偏高且质量未确认，等待回踩/次日确认" in result["trade_blockers"]


def test_high_quality_right_side_mover_can_stay_trade_eligible():
    results = [_base_candidate(**{"涨幅%": 8.2})]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "A"
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert not any("涨幅偏高" in blocker for blocker in result["trade_blockers"])


def test_near_limit_threshold_respects_board_limit():
    main = _base_candidate(代码="000001", **{"涨幅%": 9.8})
    chinext = _base_candidate(代码="300001", **{"涨幅%": 9.8, "pa_volume_confirmed": False, "price_action_signal": "普通突破"})

    _apply_sop_filter([main, chinext], {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert "涨停/近涨停，等待隔日确认" in main["trade_blockers"]
    assert "涨停/近涨停，等待隔日确认" not in chinext["trade_blockers"]
    assert "涨幅偏高且质量未确认，等待回踩/次日确认" in chinext["trade_blockers"]


def test_five_day_surge_is_ranking_risk_not_sop_veto():
    normal = _base_candidate()
    surged = _base_candidate(**{"pct_5d": 16.0, "pa_volume_confirmed": False, "price_action_signal": "普通突破"})

    _apply_sop_filter([normal, surged], {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert surged["sop_grade"] == "A"
    assert "5日涨>15%" not in surged["sop_vetoes"]
    assert surged["sop_risks"] == ["5日涨幅>15%，排序扣分"]
    assert surged["trade_eligible"] is False
    assert "5日涨幅偏高且质量未确认" in surged["trade_blockers"]


def test_watch_action_is_observe_not_executable():
    results = [_base_candidate(pa_trade_plan={"action": "WATCH"})]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "交易计划未确认" in result["trade_blockers"]


def test_low_raw_score_and_unconfirmed_entry_block_execution():
    results = [_base_candidate(Score=43.5, 现价=6.6, pa_entry_price=6.65)]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert "原始策略分<60，只观察" in result["trade_blockers"]
    assert "未站上确认价，等待突破确认" in result["trade_blockers"]


def test_wide_structure_risk_blocks_execution():
    results = [_base_candidate(pa_risk_pct=16.84)]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "BLOCK"
    assert "结构风险>12%，禁止实盘" in result["trade_blockers"]


def test_large_cap_low_turnover_requires_volume_confirmation():
    results = [_base_candidate(mkt_cap_yi=350, turnover=0.8)]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert "大市值低换手，右侧弹性不足" in result["trade_blockers"]


def test_missing_money_flow_downgrades_real_trade():
    results = [_base_candidate(money_flow_status="missing")]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert "资金流数据缺失，降级观察" in result["trade_blockers"]


def test_capital_event_risk_is_not_trade_eligible():
    results = [_base_candidate(capital_event_risk=True, warnings=["🏦 定增/资本事件"])]

    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "D"
    assert result["trade_eligible"] is False
    assert "地雷预警" in result["sop_vetoes"]
    assert "近期资本事件利好兑现，等待二次确认" in result["trade_blockers"]


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


# ── 调整3：实盘信号门槛（仅 A 级 + 多重共振可交易）──

def test_a_grade_with_resonance_is_trade_eligible():
    """A 级 + 🔥核心热点 → trade_eligible=True（严格门槛下仍可交易）。"""
    results = [_base_candidate()]  # base 已含 共振=🔥核心热点
    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    assert results[0]["sop_grade"] == "A"
    assert results[0]["trade_eligible"] is True
    assert results[0]["trade_bucket"] == "TRADE"


def test_strict_gate_a_grade_without_resonance_not_trade_eligible():
    """严格门槛下：A 级但无多重共振（独苗）→ 降为观察，不推\"可交易\"。

    模拟 000958 场景的本质：即使评级不错，但缺乏多重共振确认，
    对上班族（无暇盯盘纠错）风险过高，宁缺毋滥。
    """
    # A 级 + 无共振（独苗）
    cand = _base_candidate(Score=75, 共振="独苗")
    results = [cand]
    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    assert results[0]["sop_grade"] == "A"
    # 严格门槛：A 级但无 🔥核心热点 → 不可交易
    assert results[0]["trade_eligible"] is False
    assert results[0]["trade_bucket"] != "TRADE"


def test_strict_gate_off_a_grade_without_resonance_eligible(monkeypatch):
    """STRICT_REAL_SIGNAL_GATE=False 时回退：A 级（无论共振）均可交易。"""
    from core import scanner
    monkeypatch.setattr(scanner, "STRICT_REAL_SIGNAL_GATE", False)
    cand = _base_candidate(Score=75, 共振="独苗")  # A 级 + 无共振
    results = [cand]
    _apply_sop_filter(results, {"status": "DEFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    # 回退模式下 A 级（无共振）仍可交易
    assert results[0]["trade_eligible"] is True
    assert results[0]["trade_bucket"] == "TRADE"
