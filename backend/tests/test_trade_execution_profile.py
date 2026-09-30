import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.scanner import (
    _apply_close_confirmation_timing,
    _apply_frozen_execution_plan,
    _apply_sop_filter,
    _build_momentum_acceleration_candidates,
    _classify_historical_revival,
    _combined_sector_alignment,
    _confirmation_price_reached,
    _dedupe_trade_blockers,
    _sector_strength_score,
    _strong_sector_core_candidate,
    _strong_sector_rear_candidate,
    _stock_sector_fit_score,
    _trade_setup_quality,
    single_stock_task,
)


def test_confirmation_tolerance_accepts_one_tick_rounding_gap():
    assert _confirmation_price_reached(85.99, 86.00) is True
    assert _confirmation_price_reached(85.80, 86.00) is False


def test_close_confirmation_is_provisional_before_late_session():
    result = _base_candidate()

    _apply_close_confirmation_timing(result, "2026-07-15T11:30:00", "LIVE_SNAPSHOT")
    _apply_sop_filter([result], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert result["pa_close_confirmation_phase"] == "INTRADAY_PROVISIONAL"
    assert result["pa_close_time_eligible"] is False
    assert result["pa_volume_confirmation_state"] == "PROVISIONAL"
    assert result["pa_close_confirmed"] is False
    assert result["pa_confirmation_state"] == "PRICE_TRIGGERED"
    assert any("14:30" in blocker for blocker in result["trade_blockers"])


def test_close_confirmation_is_eligible_at_late_session_and_for_daily_close():
    late = _base_candidate()
    daily = _base_candidate()

    _apply_close_confirmation_timing(late, "2026-07-15T14:30:00", "LIVE_SNAPSHOT")
    _apply_close_confirmation_timing(
        daily,
        "2026-07-14",
        "LOCAL_DB",
        now="2026-07-15T11:30:00",
    )

    assert late["pa_close_confirmation_phase"] == "LATE_SESSION"
    assert late["pa_close_time_eligible"] is True
    assert daily["pa_close_confirmation_phase"] == "AFTER_CLOSE"
    assert daily["pa_close_time_eligible"] is True


def test_current_day_local_snapshot_stays_provisional_before_late_session():
    result = _base_candidate()

    _apply_close_confirmation_timing(
        result,
        "2026-07-15",
        "LOCAL_DB",
        now="2026-07-15T11:35:00",
    )

    assert result["pa_close_confirmation_phase"] == "INTRADAY_PROVISIONAL"
    assert result["pa_close_time_eligible"] is False


def test_frozen_plan_preserves_prior_trigger_and_keeps_generated_plan_for_audit():
    result = {"现价": 93.67, "pa_entry_price": 94.39, "pa_stop_price": 90.35}
    plan = {
        "plan_date": "2026-07-09",
        "pa_entry_price": 86.00,
        "pa_stop_price": 82.02,
        "pa_target_price": 94.40,
        "price_action_detail": {"pa_close_guard_price": 84.20},
    }

    _apply_frozen_execution_plan(result, plan)

    assert result["frozen_confirmation_price"] == 86.00
    assert result["generated_confirmation_price"] == 94.39
    assert result["frozen_entry_extension_pct"] == 8.92
    assert result["frozen_confirmation_triggered"] is True
    assert result["execution_plan_frozen"] is True
    assert result["active_confirmation_price"] == 86.00
    assert result["frozen_close_guard_price"] == 84.20
    assert result["active_close_guard_price"] == 84.20
    assert result["active_execution_plan_source"] == "FROZEN"


def test_observation_candidates_do_not_inherit_old_execution_plan():
    for flag in ("revival_watch_only", "momentum_acceleration_watch_only"):
        result = {
            "现价": 10.2,
            "pa_entry_price": 10.3,
            "pa_stop_price": 9.7,
            flag: True,
        }

        _apply_frozen_execution_plan(result, {
            "plan_date": "2026-07-01",
            "pa_entry_price": 9.5,
            "pa_stop_price": 9.0,
            "pa_target_price": 11.0,
        })

        assert result.get("execution_plan_frozen") is not True
        assert result.get("active_confirmation_price") is None


def test_frozen_plan_trigger_does_not_wait_for_new_daily_high():
    result = _base_candidate(现价=10.1, pa_entry_price=10.5)
    _apply_frozen_execution_plan(result, {
        "plan_date": "2026-07-01", "pa_entry_price": 10.0,
        "pa_stop_price": 9.5, "pa_target_price": 11.5,
    })

    _apply_sop_filter([result], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert result["pa_plan_triggered"] is True
    assert result["pa_confirmation_state"] == "ENTRY_CONFIRMED"
    assert not any("未站上确认价" in blocker for blocker in result["trade_blockers"])


def test_frozen_plan_above_three_percent_is_observe_only():
    result = _base_candidate(现价=10.4, pa_entry_price=10.5)
    _apply_frozen_execution_plan(result, {
        "plan_date": "2026-07-01",
        "pa_entry_price": 10.0,
        "pa_stop_price": 9.5,
        "pa_target_price": 11.0,
    })

    _apply_sop_filter([result], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert result["trade_eligible"] is False
    assert any("距冻结确认价涨幅>3%" in blocker for blocker in result["trade_blockers"])


def test_correlated_trade_blockers_are_counted_once_per_category():
    blockers = _dedupe_trade_blockers([
        "未站上确认价，等待突破确认",
        "未站稳历史/今日确认价",
        "涨停/近涨停，等待隔日确认",
        "5日涨幅偏高且质量未确认",
        "策略近期负期望，自动暂停",
    ])

    assert blockers == [
        "未站上确认价，等待突破确认",
        "涨停/近涨停，等待隔日确认",
        "策略近期负期望，自动暂停",
    ]


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
        "pa_h2_quality": "强",
        "price_action_score": 72,
        "price_action_signal": "强多头趋势K",
        "pa_volume_confirmed": True,
        "pa_entry_price": 10.0,
        "pa_close_position": 0.72,
        "pa_upper_shadow_pct": 1.2,
        "pa_risk_pct": 6.0,
        "pa_risk_reward": 2.5,
        "现价": 10.05,
        "execution_plan_frozen": True,
        "frozen_plan_date": "2026-07-14",
        "frozen_confirmation_price": 10.0,
        "frozen_stop_price": 9.4,
        "sector_momentum_score": 82,
        "sector_breadth": 70,
        "sector_strength_score": 82,
        "stock_sector_fit_score": 72,
        "sector_alignment_score": 80,
        "sector_role": "CORE",
        "共振": "🔥 核心热点",  # 多重共振标记（调整3 严格门槛要求）
        "a_minus_trial_health": {"enabled": True, "status": "COLLECTING"},
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
    assert "sop_grade" not in result
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert result["trade_execution_policy"] == "BARK_CONFIRMED_TRADE"
    assert result["requires_bark_confirmation"] is False
    assert result["sector_core_role_candidate"] is True
    assert result["sector_core_role_label"] == "强板块核心股"
    assert result["trade_blockers"] == []
    assert result["pa_execution_stage"] == "NEXT_SESSION_EXECUTABLE"
    assert result["final_trade_score"] > result["final_rank_score"]


def test_signal_day_ready_setup_waits_for_next_session():
    result = _base_candidate(
        execution_plan_frozen=False,
        frozen_plan_date=None,
        frozen_confirmation_price=None,
        frozen_stop_price=None,
    )

    _apply_sop_filter([result], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert result["pa_execution_stage"] == "EOD_CONFIRMED"
    assert any("次一交易日" in blocker for blocker in result["trade_blockers"])


def test_structural_growth_repair_uses_defensive_score_without_bypassing_quality_gates():
    result = _base_candidate(
        代码="300001",
        market_segment="创业板",
        market_segment_stage="STRUCTURAL_REPAIR",
        effective_market_regime="DEFENSIVE",
    )

    _apply_sop_filter([result], {"status": "CRITICAL"}, {"小金属": {"trend": "LEAD"}})

    assert result["sop_quality_dimensions"]["regime"] == 50
    assert "创业板结构性强修复" in result["sop_checks"]
    assert "成长板块独立强势" in result["sop_bonuses"]
    assert result["trade_eligible"] is True


def test_confirmed_quality_between_60_and_65_enters_controlled_a_minus_trial():
    candidate = _base_candidate(
        历史胜率="40%",
        回测统计={"profit_factor": 1.2, "expectancy": 0},
        ROE=3,
        净利YOY=0,
        sector_phase="SECTOR_NEUTRAL",
        pa_trade_setup="趋势突破",
        price_action_signal="普通突破",
    )

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert 60 <= candidate["sop_quality_score"] < 65
    assert "sop_grade" not in candidate
    assert candidate["a_minus_trial"] is True
    assert candidate["trade_eligible"] is True
    assert candidate["trade_bucket"] == "TRADE"
    assert candidate["trade_execution_policy"] == "A_MINUS_CONTROLLED_TRIAL"


def test_v2_softens_sector_and_weekly_conditions_for_confirmed_trade():
    candidate = _base_candidate(
        strategy_type="tv_dual",
        sector_momentum_score=65,
        sector_strength_score=65,
        stock_sector_fit_score=55,
        sector_alignment_score=62,
        sector_role="FOLLOWER",
        pa_weekly_context="周线中性",
        price_action_score=72,
        pa_trade_setup="趋势突破",
    )

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert "sop_grade" not in candidate
    assert candidate["a_eod_controlled_trial"] is True
    assert candidate["trade_eligible"] is True
    assert candidate["trade_bucket"] == "TRADE"
    assert candidate["trade_execution_policy"] == "A_EOD_CONTROLLED_TRIAL"
    assert candidate["trade_blockers"] == []
    assert candidate["trade_cautions"] == [
        "板块强度不足，降低仓位优先级",
        "板块联动<70，降低仓位优先级",
        "周线中性，降低仓位优先级",
        # 批4-3a：A-EOD 默认 SHADOW，cautions 追加影子提示（不签发意图）
        "A-EOD受控通道SHADOW中（E3前推验证未通过），仅观察不签发",
    ]


def test_pa_60_to_69_waits_for_t1_instead_of_same_day_trade():
    candidate = _base_candidate(
        price_action_score=65,
        pa_trade_setup="趋势突破",
    )

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert candidate["pa_execution_tier"] == "T1_CONFIRM"
    assert candidate["trade_eligible"] is False
    assert candidate["trade_bucket"] == "OBSERVE"
    assert candidate["trade_execution_policy"] == "PA_T1_CONFIRMATION"
    assert candidate.get("a_eod_controlled_trial") is not True


def test_pa_50_to_59_is_pullback_watch_not_trade():
    candidate = _base_candidate(
        price_action_score=55,
        pa_trade_setup="趋势突破",
    )

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert candidate["pa_execution_tier"] == "PULLBACK_WATCH"
    assert candidate["trade_eligible"] is False
    assert candidate["trade_bucket"] == "OBSERVE"
    assert candidate["trade_execution_policy"] == "PA_PULLBACK_WATCH"


def test_calibrated_a_eod_trial_keeps_confirmation_extension_and_chase_hard_gates():
    cases = (
        _base_candidate(
            现价=9.90,
            pa_trade_setup="趋势突破",
            price_action_score=65,
        ),
        _base_candidate(
            现价=10.40,
            pa_entry_price=10.00,
            pa_trade_setup="趋势突破",
            price_action_score=65,
        ),
        _base_candidate(
            pct_5d=10.01,
            pa_trade_setup="趋势突破",
            price_action_score=65,
        ),
        _base_candidate(
            pa_trade_setup="趋势突破",
            price_action_score=65,
            **{"涨幅%": 9.95},
        ),
    )

    for candidate in cases:
        _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
        assert candidate.get("a_eod_controlled_trial") is not True


def test_calibrated_a_eod_trial_does_not_soften_fatal_sector_blocker():
    candidate = _base_candidate(
        sector_momentum_score=65,
        sector_strength_score=65,
        stock_sector_fit_score=55,
        sector_alignment_score=62,
        pa_weekly_context="周线中性",
        pa_trade_setup="趋势突破",
        price_action_score=65,
    )

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "DOWN"}})

    assert candidate.get("a_eod_controlled_trial") is not True
    assert candidate["trade_eligible"] is False
    assert "板块下跌" in candidate["sop_vetoes"]


def test_confirmed_quality_between_65_and_70_uses_continuous_score():
    candidate = _base_candidate(
        历史胜率="50%",
        回测统计={"profit_factor": 1.5, "expectancy": 0},
        ROE=6,
        净利YOY=8,
        sector_phase="SECTOR_NEUTRAL",
        pa_trade_setup="趋势突破",
        price_action_signal="普通突破",
    )

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert 65 <= candidate["sop_quality_score"] < 70
    assert "sop_grade" not in candidate
    assert candidate["trade_eligible"] is True
    assert candidate.get("a_minus_trial") is not True


def test_a_minus_trial_requires_volume_risk_reward_and_live_health():
    no_volume = _base_candidate(pa_volume_confirmed=False)
    weak_rr = _base_candidate(pa_risk_reward=1.5)
    paused = _base_candidate(a_minus_trial_health={"enabled": False, "status": "PAUSED"})
    for candidate in (no_volume, weak_rr, paused):
        candidate.update({
            "历史胜率": "50%",
            "回测统计": {"profit_factor": 1.5, "expectancy": 0},
            "ROE": 6,
            "净利YOY": 8,
            "sector_phase": "SECTOR_NEUTRAL",
            "pa_trade_setup": "趋势突破",
            "price_action_signal": "普通突破",
        })

    _apply_sop_filter(
        [no_volume, weak_rr, paused],
        {"status": "OFFENSIVE"},
        {"小金属": {"trend": "LEAD"}},
    )

    assert no_volume.get("a_minus_trial") is not True
    assert weak_rr.get("a_minus_trial") is not True
    assert paused.get("a_minus_trial") is not True


def test_plain_tv_dual_is_now_core_trade_strategy():
    # tv_dual 已从发现层升格为核心交易策略，与 tv_dual_strict 同样可交易。
    results = [_base_candidate(strategy_type="tv_dual")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert "sop_grade" not in result
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert result["trade_execution_policy"] == "BARK_CONFIRMED_TRADE"
    assert result["requires_bark_confirmation"] is False
    # 发现层 blocker 不应再触发
    assert "普通tv_dual仅用于发现，需严格双策略确认" not in result["trade_blockers"]


def test_soft_veto_blocks_high_quality_candidate_without_letter_grade():
    candidate = _base_candidate(
        Score=120,
        raw_score=120,
        回测统计={"adjusted_win_rate": 100, "profit_factor": 3, "expectancy": 2},
        ROE=15,
        净利YOY=30,
        pa_structure_score=100,
        sector_alignment_score=100,
        mkt_cap_yi=20,
    )

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert candidate["sop_quality_score"] >= 70
    assert "sop_grade" not in candidate
    assert candidate["trade_eligible"] is False
    assert "市值<30亿" in candidate["sop_vetoes"]


def test_avoid_action_is_blocked_even_with_good_scores():
    results = [_base_candidate(pa_trade_plan={"action": "AVOID"})]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert "sop_grade" not in result
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "BLOCK"
    assert "价格行为回避" in result["sop_vetoes"]


def test_low_quality_setup_is_blocked():
    results = [_base_candidate(pa_trade_plan={"action": "WATCH"}, pa_trade_setup="外包K")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert "sop_grade" not in result
    assert result["trade_bucket"] == "BLOCK"
    assert any("外包K" in blocker for blocker in result["trade_blockers"])


def test_pine_candidate_gets_short_term_management_hint():
    results = [_base_candidate(strategy_type="pine", pa_trade_plan={}, pa_trade_setup="")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_timeframe"] == "SHORT_1_2D"
    assert "1-2" in result["exit_hint"]


def test_strong_daily_mover_keeps_quality_score_but_is_observe_only():
    results = [_base_candidate(**{"涨幅%": 8.2, "pa_volume_confirmed": False, "price_action_signal": "普通突破"})]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert "sop_grade" not in result
    assert "涨幅>7%" not in result["sop_vetoes"]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "涨幅偏高且质量未确认，等待回踩/次日确认" in result["trade_cautions"]


def test_high_quality_right_side_mover_can_stay_trade_eligible():
    results = [_base_candidate(**{"涨幅%": 8.2})]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert "sop_grade" not in result
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert not any("涨幅偏高" in blocker for blocker in result["trade_blockers"])


def test_h1_first_entry_requires_strong_mainline_confirmation():
    results = [_base_candidate(pa_trade_setup="H1首次入场", sector_alignment_score=80)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert result["pa_trade_setup_quality"] == "H1_RAW"
    assert "H1首次入场仅强主线放量确认可小仓复核" in result["trade_blockers"]


def test_h1_first_entry_can_trade_when_mainline_volume_confirmed():
    results = [_base_candidate(pa_trade_setup="H1首次入场", sector_alignment_score=90)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert result["pa_trade_setup_quality"] == "H1_TRADABLE"
    assert not any("H1首次入场" in blocker for blocker in result["trade_blockers"])


def test_weak_sector_alignment_is_caution_in_v2():
    results = [_base_candidate(sector_alignment_score=45)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert "弱板块联动，降低仓位优先级" in result["trade_cautions"]
    assert "弱板块联动胜率偏低" in result["sop_risks"]


def test_sector_strength_and_stock_fit_are_separate_dimensions():
    candidate = _base_candidate(
        sector_momentum_score=82,
        sector_breadth=76,
        sector_phase="SECTOR_CONFIRM",
        sector_relative_pct=3.2,
        stock_rank_in_sector=2,
        sector_role="LEADER",
        **{"涨幅%": 4.5},
    )

    sector_strength = _sector_strength_score(candidate)
    stock_fit = _stock_sector_fit_score(candidate)
    combined = _combined_sector_alignment(sector_strength, stock_fit)

    assert sector_strength >= 75
    assert stock_fit >= 70
    assert combined >= 75


def test_strong_stock_in_weak_sector_is_watch_not_trade():
    results = [_base_candidate(
        sector_strength_score=45,
        stock_sector_fit_score=82,
        sector_alignment_score=60,
        trade_opportunity_score=68,
    )]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "板块强度弱，降低仓位优先级" in result["trade_cautions"]
    assert "板块强度弱，个股强势不直接交易" in result["sop_risks"]


def test_strong_sector_weak_stock_fit_is_v2_trade_caution():
    results = [_base_candidate(
        sector_strength_score=82,
        stock_sector_fit_score=42,
        sector_alignment_score=72,
    )]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert result.get("a_eod_controlled_trial") is not True
    assert "强板块但个股适配不足，降低仓位优先级" in result["trade_cautions"]
    assert "板块强但个股适配不足，偏补涨观察" in result["sop_risks"]


def test_strong_sector_rear_role_is_v2_trade_caution():
    results = [_base_candidate(
        sector_strength_score=82,
        stock_sector_fit_score=68,
        sector_alignment_score=76,
        sector_role="FOLLOWER",
    )]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sector_rear_role_watch"] is True
    assert result["sector_core_role_label"] == "强板块后排观察"
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert result.get("a_eod_controlled_trial") is not True
    assert "强板块后排角色，等待转强为核心股" in result["trade_cautions"]
    assert "强板块后排角色，等待转强为核心股" in result["sop_risks"]


def test_strong_sector_core_role_helpers_require_core_or_leader():
    core = _base_candidate(sector_role="LEADER", stock_sector_fit_score=66)
    rear = _base_candidate(sector_role="LAGGARD", stock_sector_fit_score=80)
    legacy = _base_candidate(sector_role="", stock_sector_fit_score=72)

    assert _strong_sector_core_candidate(core, 82, 66) is True
    assert _strong_sector_rear_candidate(core, 82) is False
    assert _strong_sector_core_candidate(rear, 82, 80) is False
    assert _strong_sector_rear_candidate(rear, 82) is True
    assert _strong_sector_core_candidate(legacy, 82, 72) is True


def test_h2_boost_requires_volume_and_quality_confirmation():
    weak_h2 = [_base_candidate(
        pa_h2_quality="弱",
        pa_volume_confirmed=False,
        pa_volume_pattern="缩量",
        sector_strength_score=82,
    )]
    strong_h2 = [_base_candidate(
        pa_h2_quality="强",
        pa_volume_confirmed=True,
        sector_strength_score=82,
    )]

    _apply_sop_filter(weak_h2, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    _apply_sop_filter(strong_h2, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert weak_h2[0].get("h2_second_entry_boost") is not True
    assert "H2二次入场" not in weak_h2[0]["sop_bonuses"]
    assert weak_h2[0]["pa_trade_setup_quality"] == "H2_RAW"
    assert strong_h2[0].get("h2_second_entry_boost") is True
    assert strong_h2[0]["pa_trade_setup_quality"] == "H2_TRADABLE"
    assert "H2二次入场" in strong_h2[0]["sop_bonuses"]


def test_h2_raw_shape_is_observe_even_when_other_conditions_look_ready():
    results = [_base_candidate(
        pa_h2_quality="弱",
        pa_volume_confirmed=True,
        pa_volume_pattern="放量突破",
        sector_strength_score=82,
        stock_sector_fit_score=72,
        sector_alignment_score=82,
        pa_risk_pct=6.0,
        pa_close_position=0.75,
        pa_upper_shadow_pct=1.0,
    )]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["pa_trade_setup_quality"] == "H2_RAW"
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "H2二次入场未满足量能/质量/风险确认，仅观察" in result["trade_blockers"]


def test_trade_setup_quality_helper_classifies_raw_and_tradable_shapes():
    h2_raw = _base_candidate(pa_h2_quality="弱")
    h2_tradable = _base_candidate(pa_h2_quality="中")
    h1_raw = _base_candidate(pa_trade_setup="H1首次入场", sector_alignment_score=82)
    h1_tradable = _base_candidate(pa_trade_setup="H1首次入场", sector_alignment_score=90)

    assert _trade_setup_quality(h2_raw, "H2二次入场", 80, 82) == "H2_RAW"
    assert _trade_setup_quality(h2_tradable, "H2二次入场", 80, 82) == "H2_TRADABLE"
    assert _trade_setup_quality(h1_raw, "H1首次入场", 82, 82) == "H1_RAW"
    assert _trade_setup_quality(h1_tradable, "H1首次入场", 90, 82) == "H1_TRADABLE"


def test_neutral_weekly_range_is_v2_trade_caution():
    results = [_base_candidate(pa_weekly_context="周线交易区间")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is True
    assert result["trade_bucket"] == "TRADE"
    assert result.get("a_eod_controlled_trial") is not True
    assert "周线交易区间，降低仓位优先级" in result["trade_cautions"]
    assert "周线交易区间，等待右侧确认" in result["sop_risks"]


def test_pullback_reversal_volume_counts_as_confirmation_and_bonus():
    results = [_base_candidate(pa_volume_confirmed=False, pa_volume_pattern="缩量回调后放量反包")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is True
    assert "站上确认价但量能未确认" not in result["trade_blockers"]
    assert "缩量回调后放量反包" in result["sop_bonuses"]
    assert result["bark_success_profile_match"] is True


def test_opportunity_70_79_strong_linkage_gets_sweet_spot_model():
    results = [_base_candidate(trade_opportunity_score=75, sector_alignment_score=82)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["sweet_spot_trade_candidate"] is True
    assert "70-79机会分强联动买点" in result["sop_bonuses"]
    assert "机会分70-79" in result["sweet_spot_reason"]


def test_observe_candidate_gets_promotion_action_when_near_confirm_line():
    results = [_base_candidate(
        现价=9.94,
        pa_entry_price=10.0,
        trade_opportunity_score=65,
        sector_alignment_score=78,
        pa_trade_setup="H2二次入场",
    )]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] in {"EARLY", "OBSERVE"}
    assert result["observe_promotion_candidate"] is True
    assert "观察转可买" in result["observe_promotion_action"]


def test_bark_success_profile_features_add_scoring_bonus():
    results = [_base_candidate(
        sector_phase="SECTOR_CONFIRM",
        pa_trade_setup="H1首次入场",
        sector_alignment_score=92,
    )]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["bark_success_profile_match"] is True
    assert "Bark高胜率板块阶段" in result["sop_bonuses"]
    assert "Bark高胜率价格结构" in result["sop_bonuses"]


def test_near_limit_threshold_respects_board_limit():
    main = _base_candidate(代码="000001", **{"涨幅%": 9.8})
    chinext = _base_candidate(代码="300001", **{"涨幅%": 9.8, "pa_volume_confirmed": False, "price_action_signal": "普通突破"})

    _apply_sop_filter([main, chinext], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert "涨停/近涨停，等待隔日确认" in main["trade_blockers"]
    assert "涨停/近涨停，等待隔日确认" not in chinext["trade_blockers"]
    assert "涨幅偏高且质量未确认，等待回踩/次日确认" in chinext["trade_cautions"]


def test_five_day_surge_has_no_standalone_15pct_gate():
    # pct_5d=16% 走递减扣分，不再映射字母等级。
    normal = _base_candidate()
    surged = _base_candidate(**{"pct_5d": 16.0, "pa_volume_confirmed": False, "price_action_signal": "普通突破"})

    _apply_sop_filter([normal, surged], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert "sop_grade" not in surged
    assert "5日涨>15%" not in surged["sop_vetoes"]
    # 递减扣分文案应出现在 risks（每超1%扣0.5分，16-10=6，扣3分）
    assert any("超10%起扣线" in r for r in surged["sop_risks"])
    # 不再保留15%这一档的独立排序风险或交易阻断。
    assert "5日涨幅>15%，排序扣分" not in surged["sop_risks"]
    assert surged["trade_eligible"] is False
    assert "5日涨幅偏高且质量未确认" not in surged["trade_blockers"]


def test_price_action_score_still_controls_execution_without_letter_grade():
    candidate = _base_candidate(price_action_score=59)

    _apply_sop_filter([candidate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert "sop_grade" not in candidate
    assert candidate["trade_eligible"] is False


def test_moderate_5d_gain_reduces_continuous_quality_score():
    baseline = _base_candidate(pct_5d=4.0)
    moderate = _base_candidate(pct_5d=20.0)

    _apply_sop_filter([baseline, moderate], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert "sop_grade" not in moderate
    # 20-10=10，每超1%扣0.5 → 扣5分
    assert baseline["sop_quality_score"] - moderate["sop_quality_score"] == 5.0
    # 扣分文案应记录在 risks
    assert any("超10%起扣线" in r for r in moderate["sop_risks"])


def test_excessive_5d_gain_remains_non_executable_without_letter_grade():
    excessive = _base_candidate(pct_5d=26.0)

    _apply_sop_filter([excessive], {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    assert "sop_grade" not in excessive
    assert excessive["trade_eligible"] is False


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
    assert "站上确认价但量能未确认" in result["trade_cautions"]


def test_price_and_volume_still_require_stable_close_confirmation():
    results = [_base_candidate(现价=10.05, pa_entry_price=10.0, pa_close_position=0.42, pa_upper_shadow_pct=4.2)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is False
    assert "冲高回落，站稳未确认" in result["trade_blockers"]


def test_small_cap_without_strong_mainline_is_v2_trade_caution():
    results = [_base_candidate(mkt_cap_yi=42, sector_alignment_score=80)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["mkt_cap_bucket"] == "SMALL"
    assert result["trade_eligible"] is True
    assert "小市值弹性票，需主线强联动和价量确认" in result["trade_cautions"]
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


def test_mega_cap_low_turnover_is_v2_trade_caution():
    results = [_base_candidate(mkt_cap_yi=800, turnover=0.8, 换手率=0.8)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["mkt_cap_bucket"] == "MEGA"
    assert result["trade_eligible"] is True
    assert "超大市值换手不足，等待机构资金确认" in result["trade_cautions"]


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

    assert "sop_grade" not in candidate
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


def test_large_cap_low_turnover_is_v2_trade_caution():
    results = [_base_candidate(mkt_cap_yi=350, turnover=0.8)]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is True
    assert "大市值低换手，右侧弹性不足" in result["trade_cautions"]


def test_missing_money_flow_is_v2_trade_caution():
    results = [_base_candidate(money_flow_status="missing")]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert result["trade_eligible"] is True
    assert "资金流数据缺失，降低仓位优先级" in result["trade_cautions"]


def test_capital_event_risk_is_not_trade_eligible():
    results = [_base_candidate(capital_event_risk=True, warnings=["🏦 定增/资本事件"])]

    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})

    result = results[0]
    assert "sop_grade" not in result
    assert result["trade_eligible"] is False
    assert "地雷预警" in result["sop_vetoes"]
    assert "近期资本事件利好兑现，等待二次确认" in result["trade_cautions"]


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


def test_confirmed_strategy_route_is_independent_of_letter_grades():
    results = [_base_candidate()]
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    assert "sop_grade" not in results[0]
    assert results[0]["trade_eligible"] is True
    assert results[0]["trade_bucket"] == "TRADE"


def test_v2_without_resonance_can_trade_when_other_confirmations_pass():
    cand = _base_candidate(Score=75, 共振="独苗")
    results = [cand]
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    assert "sop_grade" not in results[0]
    assert results[0]["trade_eligible"] is True
    assert results[0]["trade_bucket"] == "TRADE"


def test_strict_gate_off_without_resonance_eligible(monkeypatch):
    """STRICT_REAL_SIGNAL_GATE=False 时按其他确定性条件判断。"""
    from core import scanner
    monkeypatch.setattr(scanner, "STRICT_REAL_SIGNAL_GATE", False)
    cand = _base_candidate(Score=75, 共振="独苗")
    results = [cand]
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    assert results[0]["trade_eligible"] is True
    assert results[0]["trade_bucket"] == "TRADE"


def test_strong_signal_retains_continuous_quality_score():
    results = [_base_candidate(
        Score=81.8, raw_score=103.6,
        历史胜率="40%",
        共振="🔥 核心热点",
    )]
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    assert "sop_grade" not in results[0]
    assert results[0]["sop_quality_score"] > 0


def test_strong_signal_with_resonance_can_trade_when_confirmed():
    results = [_base_candidate(raw_score=103.6)]  # base 含共振+高bonus
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    assert results[0]["trade_eligible"] is True


def test_weak_signal_still_has_continuous_quality_score():
    results = [_base_candidate(
        raw_score=70.0, 历史胜率="40%", 共振="🔥 核心热点",
    )]
    _apply_sop_filter(results, {"status": "OFFENSIVE"}, {"小金属": {"trend": "LEAD"}})
    assert "sop_grade" not in results[0]
    assert 0 <= results[0]["sop_quality_score"] <= 100
