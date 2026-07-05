import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.scanner import (
    _apply_sop_filter,
    _build_momentum_acceleration_candidates,
    _classify_historical_revival,
    single_stock_task,
)


def _base_candidate(**overrides):
    candidate = {
        "代码": "000001",
        "名称": "测试股票",
        "行业": "小金属",
        "Score": 90,
        "涨幅%": 2.5,
        "影线比": 0.1,
        "pct_5d": 4.0,
        "历史胜率": "68%",
        "回测统计": {"profit_factor": 2.5},
        "ROE": 9,
        "净利YOY": 18,
        "strategy_type": "tv_dual_strict",
        "pa_trade_plan": {"action": "READY"},
        "pa_trade_setup": "H2二次入场",
        "price_action_score": 72,
        "price_action_signal": "强多头趋势K",
        "pa_volume_confirmed": True,
        "pa_entry_price": 10.0,
        "pa_close_position": 0.72,
        "pa_upper_shadow_pct": 1.2,
        "pa_risk_pct": 6.0,
        "现价": 10.05,
        "sector_momentum_score": 82,
        "sector_breadth": 70,
        "sector_alignment_score": 80,
        "共振": "🔥 核心热点",  # 多重共振标记（调整3 严格门槛要求）
    }
    candidate.update(overrides)
    return candidate


def _momentum_hist(closes):
    rows = []
    for idx, close in enumerate(closes):
        rows.append({
            "日期": f"2026-06-{idx + 1:02d}",
            "开盘": close * 0.96,
            "最高": close,
            "最低": close * 0.95,
            "收盘": close,
            "成交量": 100000 + idx * 1000,
        })
    return pd.DataFrame(rows)


def test_ready_candidate_enters_trade_bucket():
    results = [_base_candidate()]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "A"
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert result["trade_blockers"] == []
    assert result["final_trade_score"] > result["final_rank_score"]


def test_plain_tv_dual_is_discovery_only_not_trade():
    results = [_base_candidate(strategy_type="tv_dual")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "A"
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "普通tv_dual仅用于发现，需严格双策略确认" in result["trade_blockers"]


def test_avoid_action_is_blocked_even_with_good_scores():
    results = [_base_candidate(pa_trade_plan={"action": "AVOID"})]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "D"
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "BLOCK"
    assert "价格行为回避" in result["sop_vetoes"]


def test_low_quality_setup_is_blocked():
    results = [_base_candidate(pa_trade_plan={"action": "WATCH"}, pa_trade_setup="外包K")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "D"
    assert result["trade_bucket"] == "BLOCK"
    assert any("外包K" in blocker for blocker in result["trade_blockers"])


def test_pine_candidate_gets_short_term_management_hint():
    results = [_base_candidate(strategy_type="pine", pa_trade_plan={}, pa_trade_setup="")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_timeframe"] == "SHORT_1_2D"
    assert "1-2" in result["exit_hint"]


def test_strong_daily_mover_keeps_quality_grade_but_is_observe_only():
    results = [_base_candidate(**{"涨幅%": 8.2, "pa_volume_confirmed": False, "price_action_signal": "普通突破"})]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "A"
    assert "涨幅>7%" not in result["sop_vetoes"]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "涨幅偏高且质量未确认，等待回踩/次日确认" in result["trade_blockers"]


def test_high_quality_right_side_mover_can_stay_trade_eligible():
    results = [_base_candidate(**{"涨幅%": 8.2})]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sop_grade"] == "A"
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert not any("涨幅偏高" in blocker for blocker in result["trade_blockers"])


def test_h1_first_entry_requires_strong_mainline_confirmation():
    results = [_base_candidate(pa_trade_setup="H1首次入场", sector_alignment_score=80)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "H1首次入场仅强主线放量确认可小仓复核" in result["trade_blockers"]


def test_h1_first_entry_can_trade_when_mainline_volume_confirmed():
    results = [_base_candidate(pa_trade_setup="H1首次入场", sector_alignment_score=90)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert not any("H1首次入场" in blocker for blocker in result["trade_blockers"])


def test_near_limit_threshold_respects_board_limit():
    main = _base_candidate(代码="000001", **{"涨幅%": 9.8})
    chinext = _base_candidate(代码="300001", **{"涨幅%": 9.8, "pa_volume_confirmed": False, "price_action_signal": "普通突破"})

    _apply_sop_filter([main, chinext], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert "涨停/近涨停，等待隔日确认" in main["trade_blockers"]
    assert "涨停/近涨停，等待隔日确认" not in chinext["trade_blockers"]
    assert "涨幅偏高且质量未确认，等待回踩/次日确认" in chinext["trade_blockers"]


def test_five_day_surge_is_ranking_risk_not_sop_veto():
    normal = _base_candidate()
    surged = _base_candidate(**{"pct_5d": 16.0, "pa_volume_confirmed": False, "price_action_signal": "普通突破"})

    _apply_sop_filter([normal, surged], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert surged["sop_grade"] == "A"
    assert "5日涨>15%" not in surged["sop_vetoes"]
    assert surged["sop_risks"] == ["5日涨幅>15%，排序扣分"]
    assert surged["trade_eligible"] is False
    assert "5日涨幅偏高且质量未确认" in surged["trade_blockers"]


def test_watch_action_is_observe_not_executable():
    results = [_base_candidate(pa_trade_plan={"action": "WATCH"})]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "交易计划未确认" in result["trade_blockers"]


def test_low_raw_score_and_unconfirmed_entry_block_execution():
    results = [_base_candidate(Score=43.5, 现价=6.6, pa_entry_price=6.65)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert "原始策略分<60，只观察" in result["trade_blockers"]
    assert "未站上确认价，等待突破确认" in result["trade_blockers"]


def test_near_entry_price_is_not_stable_confirmation():
    results = [_base_candidate(现价=9.98, pa_entry_price=10.0, mkt_cap_yi=120, sector_alignment_score=88)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert "未站上确认价，等待突破确认" in result["trade_blockers"]
    assert result["trade_bucket"] == "EARLY"
    assert result["early_trade_grade"] == "A-"
    assert "距确认价<0.8%" in result["early_trade_reason"]


def test_early_entry_does_not_override_near_limit_no_chase():
    results = [_base_candidate(代码="002472", 现价=47.01, pa_entry_price=47.02, **{"涨幅%": 9.99})]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result.get("early_trade_candidate") is not True
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "涨停/近涨停，等待隔日确认" in result["trade_blockers"]


def test_price_above_entry_still_requires_volume_confirmation():
    results = [_base_candidate(现价=10.05, pa_entry_price=10.0, pa_volume_confirmed=False, pa_volume_pattern="量能不足")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert "站上确认价但量能未确认" in result["trade_blockers"]


def test_price_and_volume_still_require_stable_close_confirmation():
    results = [_base_candidate(现价=10.05, pa_entry_price=10.0, pa_close_position=0.42, pa_upper_shadow_pct=4.2)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert "冲高回落，站稳未确认" in result["trade_blockers"]


def test_small_cap_requires_stronger_mainline_confirmation():
    results = [_base_candidate(mkt_cap_yi=42, sector_alignment_score=80)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["mkt_cap_bucket"] == "SMALL"
    assert result["trade_eligible"] is False
    assert "小市值弹性票，需主线强联动和价量确认" in result["trade_blockers"]
    assert "小市值需主线强联动" in result["sop_risks"]


def test_mainline_h2_continuation_gets_bonus_but_still_needs_confirmation():
    results = [_base_candidate(
        mkt_cap_yi=120,
        sector_phase="SECTOR_CONFIRM",
        sector_alignment_score=88,
        price_action_regime="多头趋势",
        pa_trend_phase="二次入场",
        pa_trade_setup="H2二次入场",
        pa_h2_quality="强",
        pa_close_position=0.75,
        pa_upper_shadow_pct=1.0,
    )]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trend_continuation_candidate"] is True
    assert "主线趋势中继二买" in result["sop_bonuses"]
    assert result["final_trade_score"] > result["final_rank_score"]


def test_mega_cap_requires_turnover_confirmation():
    results = [_base_candidate(mkt_cap_yi=800, turnover=0.8, 换手率=0.8)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["mkt_cap_bucket"] == "MEGA"
    assert result["trade_eligible"] is False
    assert "超大市值换手不足，等待机构资金确认" in result["trade_blockers"]


def test_historical_revival_like_002440_waits_for_next_day_confirmation():
    history = {
        "signal_date": "2026-06-22",
        "strategy_type": "tv_dual",
        "price": 10.99,
        "pa_entry_price": 11.0,
        "pa_stop_price": 9.49,
    }
    result = _classify_historical_revival(
        _base_candidate(
            现价=11.28,
            **{"涨幅%": 10.05},
            pa_entry_price=11.29,
            pa_stop_price=10.01,
            pa_volume_confirmed=False,
            pa_volume_pattern="量能中性",
            pa_close_position=1.0,
            pa_upper_shadow_pct=0.0,
            pa_trap_risk=75,
            pa_position_strategy="区间上沿不追价",
        ),
        history,
    )

    assert result["revival_level"] == "NEXT_DAY_CONFIRM"
    assert "量能未确认" in result["revival_blockers"]
    assert "多头陷阱风险偏高" in result["revival_blockers"]
    assert "交易区间上沿不追价" in result["revival_blockers"]


def test_historical_revival_blockers_prevent_trade_eligible():
    history = {
        "signal_date": "2026-06-22",
        "strategy_type": "tv_dual",
        "price": 10.99,
        "pa_entry_price": 11.0,
        "pa_stop_price": 9.49,
    }
    candidate = _base_candidate(
        revival_watch_only=True,
        revival_history=history,
        **_classify_historical_revival(
            _base_candidate(
                现价=11.28,
                **{"涨幅%": 10.05},
                pa_entry_price=11.29,
                pa_stop_price=10.01,
                pa_volume_confirmed=False,
                pa_volume_pattern="量能中性",
                pa_close_position=1.0,
                pa_upper_shadow_pct=0.0,
                pa_trap_risk=75,
                pa_position_strategy="区间上沿不追价",
            ),
            history,
        ),
    )

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert candidate["trade_eligible"] is False
    assert "历史信号复活仅观察，等次日确认" in candidate["trade_blockers"]
    assert "历史信号复活" in candidate["sop_bonuses"]


def test_historical_revival_momentum_acceleration_like_haisco():
    history = {
        "signal_date": "2026-06-24",
        "strategy_type": "tv_dual",
        "price": 58.01,
        "pa_entry_price": 58.51,
        "pa_stop_price": 52.49,
    }
    result = _classify_historical_revival(
        _base_candidate(
            现价=72.96,
            **{"涨幅%": 9.98},
            pct_5d=27.8,
            pa_entry_price=72.98,
            pa_stop_price=61.0,
            price_action_score=75,
            pa_volume_confirmed=True,
            pa_volume_pattern="放量突破",
            pa_close_position=1.0,
            pa_upper_shadow_pct=0.0,
            pa_trap_risk=20,
            sector_alignment_score=93.5,
        ),
        history,
    )

    assert result["revival_level"] == "MOMENTUM_ACCELERATION"
    assert result["revival_action"] == "历史信号复活：动量加速，次日小仓复核"
    assert "未站稳历史/今日确认价" not in result["revival_blockers"]


def test_historical_revival_momentum_acceleration_still_not_direct_trade():
    history = {
        "signal_date": "2026-06-24",
        "strategy_type": "tv_dual",
        "price": 58.01,
        "pa_entry_price": 58.51,
        "pa_stop_price": 52.49,
    }
    candidate = _base_candidate(
        revival_watch_only=True,
        revival_history=history,
        现价=72.96,
        **{"涨幅%": 9.98},
        pct_5d=27.8,
        pa_entry_price=72.98,
        pa_stop_price=61.0,
        price_action_score=75,
        pa_volume_confirmed=True,
        pa_volume_pattern="放量突破",
        pa_close_position=1.0,
        pa_upper_shadow_pct=0.0,
        pa_trap_risk=20,
        sector_alignment_score=93.5,
        **_classify_historical_revival(
            _base_candidate(
                现价=72.96,
                **{"涨幅%": 9.98},
                pct_5d=27.8,
                pa_entry_price=72.98,
                pa_stop_price=61.0,
                price_action_score=75,
                pa_volume_confirmed=True,
                pa_volume_pattern="放量突破",
                pa_close_position=1.0,
                pa_upper_shadow_pct=0.0,
                pa_trap_risk=20,
                sector_alignment_score=93.5,
            ),
            history,
        ),
    )

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert candidate["trade_eligible"] is False
    assert "动量加速票，次日不高开追价后小仓复核" in candidate["trade_blockers"]
    assert "连续强势加速" in candidate["sop_bonuses"]


def test_momentum_acceleration_candidate_catches_strong_trend_without_prior_signal():
    closes = [30 + i * 0.2 for i in range(25)] + [38.19, 42.66, 41.8, 44.25, 43.79, 45.66, 50.23, 55.25]
    candidates = pd.DataFrame([{
        "code": "002407",
        "name": "多氟多",
        "price": 55.25,
        "pct_chg": 9.99,
        "turnover": 6.5,
    }])

    results = _build_momentum_acceleration_candidates(
        candidates,
        set(),
        {"002407": _momentum_hist(closes)},
        {"002407": "化工原料"},
        {"化工原料": {"sector_momentum_score": 90, "sector_breadth": 70}},
        "tv_dual",
    )

    assert len(results) == 1
    assert results[0]["signal"] == "强趋势涨停加速"
    assert results[0]["momentum_acceleration_watch_only"] is True
    assert results[0]["momentum_acceleration_metrics"]["near_new_high"] is True


def test_momentum_acceleration_candidate_is_observe_only():
    candidate = _base_candidate(
        momentum_acceleration_watch_only=True,
        momentum_acceleration_reason="5日涨幅25%，强势日2天",
        **{"涨幅%": 9.99},
        pct_5d=25,
    )

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert candidate["sop_grade"] == "M"
    assert candidate["trade_eligible"] is False
    assert "强趋势加速观察，次日确认后小仓复核" in candidate["trade_blockers"]


def test_wide_structure_risk_blocks_execution():
    # pa_risk_pct=21 > HARD_EXECUTION_RISK_PCT(20) → "禁止实盘"(BLOCK)
    results = [_base_candidate(pa_risk_pct=21.0)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "BLOCK"
    assert "结构风险>20%，禁止实盘" in result["trade_blockers"]


def test_large_cap_low_turnover_requires_volume_confirmation():
    results = [_base_candidate(mkt_cap_yi=350, turnover=0.8)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert "大市值低换手，右侧弹性不足" in result["trade_blockers"]


def test_missing_money_flow_downgrades_real_trade():
    results = [_base_candidate(money_flow_status="missing")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert "资金流数据缺失，降级观察" in result["trade_blockers"]


def test_capital_event_risk_is_not_trade_eligible():
    results = [_base_candidate(capital_event_risk=True, warnings=["🏦 定增/资本事件"])]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

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
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
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
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
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
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    # 回退模式下 A 级（无共振）仍可交易
    assert results[0]["trade_eligible"] is True
    assert results[0]["trade_bucket"] == "TRADE"


# ── 强信号分级加权（raw_score≥95 参与分级，避免信号强度与分级脱节）──

def test_strong_signal_boosted_to_at_least_b():
    """raw_score≥95 的强信号，即使历史胜率不足（checks 少），至少保底 B 级。

    模拟木林森场景：raw_score=103.6（极强信号）但胜率<50%（少1个check），
    原逻辑会判 C，加权后应至少 B。
    """
    results = [_base_candidate(
        Score=81.8, raw_score=103.6,  # 强信号
        历史胜率="40%",  # 胜率<50% → 少1个check（原会降到C）
        共振="🔥 核心热点",
    )]
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    assert results[0]["sop_grade"] in ("A", "B"), f"强信号应≥B，实际{results[0]['sop_grade']}"


def test_strong_signal_can_reach_a_with_resonance():
    """raw_score≥95 + 共振 + 多bonus → 可达 A 级。"""
    results = [_base_candidate(raw_score=103.6)]  # base 含共振+高bonus
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    assert results[0]["sop_grade"] == "A"


def test_weak_signal_not_boosted():
    """raw_score<95 的弱信号不享受分级加权（保持原逻辑，不强升）。"""
    results = [_base_candidate(
        raw_score=70.0, 历史胜率="40%", 共振="🔥 核心热点",
    )]
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    # 弱信号不应被强信号保底逻辑误升到 A
    assert results[0]["sop_grade"] in ("A", "B", "C")
