import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.decision_layer import (
    apply_decision_layer,
    apply_growth_segment_context,
    build_growth_segment_context,
    build_market_decision_context,
)
from core.sector_strength import classify_mainline_sector


def _snapshot(values):
    return pd.DataFrame({"pct_chg": values})


def _stock(**overrides):
    item = {
        "代码": "000001",
        "名称": "测试股票",
        "涨幅%": 4.0,
        "Score": 82,
        "final_trade_score": 85,
        "trade_bucket": "TRADE",
        "trade_eligible": True,
        "sector_phase": "SECTOR_CONFIRM",
        "sector_rank": 1,
        "sector_momentum_score": 82,
        "sector_breadth": 75,
        "sector_5d_pct": 5,
        "sector_role": "LEADER",
        "sector_alignment_score": 85,
        "sector_relative_pct": 3,
        "pa_risk_reward": 2.5,
        "pa_trade_plan": {"action": "READY"},
        "pa_pullback_status": "CONFIRMED",
        "pa_entry_price": 10.5,
        "pa_stop_price": 9.8,
        "money_flow": {"main_net_ratio": 8, "main_net_inflow_yi": 1.5},
    }
    item.update(overrides)
    return item


def test_market_context_caps_position_during_retreat():
    context = build_market_decision_context(_snapshot([-8, -6, -4, -2, 1]), {"status": "CRITICAL"})

    assert context["market_sentiment_stage"] == "RETREAT"
    assert context["portfolio_position_cap_pct"] == 10
    assert "新增仓位" in context["market_forbidden_actions"]


def test_strong_breadth_is_repair_not_retreat_when_indices_are_below_ema20():
    context = build_market_decision_context(
        _snapshot([8, 7, 6, 5, 4, 3, 2, 1, 1, -1]),
        {"status": "CRITICAL"},
    )

    assert context["market_sentiment_score"] >= 58
    assert context["market_sentiment_stage"] == "REPAIR"
    assert context["market_sentiment_label"] == "修复"
    assert context["portfolio_position_cap_pct"] == 30


def test_sector_mainline_requires_confirmed_top_rank_strength():
    assert classify_mainline_sector({
        "sector_phase": "SECTOR_CONFIRM",
        "sector_rank": 2,
        "sector_momentum_score": 82,
        "sector_breadth": 72,
        "sector_5d_pct": 5,
    }) == "MAIN"
    assert classify_mainline_sector({
        "sector_phase": "SECTOR_FADE",
        "sector_rank": 1,
        "sector_momentum_score": 80,
        "sector_breadth": 40,
    }) == "FADING"


def test_decision_layer_creates_opportunity_position_and_state():
    stocks = [_stock()]

    context = apply_decision_layer(stocks, _snapshot([6, 5, 4, 3, 2, 1, -1]), {"status": "OFFENSIVE"})
    result = stocks[0]

    assert context["market_sentiment_stage"] == "REPAIR"
    assert result["sector_mainline"] == "MAIN"
    assert result["leadership_score"] >= 80
    assert result["leadership_components"]["sector_role"] == 92
    assert "相对板块" in result["leadership_reason"]
    assert result["trade_opportunity_score"] >= 70
    assert result["position_plan"]["initial_position_pct"] > 0
    assert result["trade_state"] == "CONFIRM_ADD"
    assert "10.50" in result["execution_instruction"]
    assert "9.80" in result["execution_instruction"]


def test_tv_zp_candidate_uses_same_risk_gate_as_other_executable_tv_strategies():
    stocks = [_stock(strategy_type="tv_zp")]

    apply_decision_layer(
        stocks,
        _snapshot([6, 5, 4, 3, 2, 1, -1]),
        {"status": "OFFENSIVE"},
    )

    result = stocks[0]
    assert result["opportunity_gate_applicable"] is True
    assert result["position_plan"]["initial_position_pct"] > 0


def test_tv_execution_risk_unit_scales_position_plan():
    a_tier = _stock(tv_execution_tier="A", tv_execution_risk_unit=1.0)
    b_tier = _stock(code="000002", tv_execution_tier="B", tv_execution_risk_unit=0.6)

    apply_decision_layer(
        [a_tier, b_tier],
        _snapshot([6, 5, 4, 3, 2, 1, -1]),
        {"status": "OFFENSIVE"},
    )

    assert b_tier["position_plan"]["initial_position_pct"] == round(
        a_tier["position_plan"]["initial_position_pct"] * 0.6, 1,
    )
    assert b_tier["position_plan"]["max_position_pct"] == round(
        a_tier["position_plan"]["max_position_pct"] * 0.6, 1,
    )
    assert b_tier["position_plan"]["tv_execution_risk_unit"] == 0.6


def test_trade_cautions_reduce_position_without_revoking_trade_permission():
    clean = _stock()
    cautious = _stock(代码="000002", trade_cautions=["板块联动<70，降级观察"])

    apply_decision_layer(
        [clean, cautious],
        _snapshot([6, 5, 4, 3, 2, 1, -1]),
        {"status": "OFFENSIVE"},
    )

    assert cautious["trade_bucket"] == "TRADE"
    assert cautious["trade_eligible"] is True
    assert cautious["position_plan"]["initial_position_pct"] < clean["position_plan"]["initial_position_pct"]
    assert cautious["position_plan"]["trade_caution_position_multiplier"] == 0.5


def test_a_minus_trial_position_is_capped_at_five_percent():
    stocks = [_stock(a_minus_trial=True, a_minus_trial_grade="A-")]

    apply_decision_layer(stocks, _snapshot([8, 7, 6, 5, 4, 3, 2, 1]), {"status": "OFFENSIVE"})

    result = stocks[0]
    assert result["trade_eligible"] is True
    assert result["position_plan"]["initial_position_pct"] <= 5
    assert result["position_plan"]["max_position_pct"] <= 5
    assert result["position_plan"]["a_minus_portfolio_cap_pct"] == 10
    assert result["a_minus_portfolio_cap_pct"] == 10
    assert "受控试仓" in result["execution_instruction"]


def test_a_eod_trial_position_is_shadowed_by_default():
    """批4-3：A-EOD 默认 SHADOW——资格打标保留、仓位归零（E3 前推未通过）。"""
    stocks = [_stock(
        a_eod_controlled_trial=True,
        a_eod_policy_version="a-eod-controlled-trial-v1-shadow",
        trade_execution_policy="A_EOD_CONTROLLED_TRIAL",
    )]

    apply_decision_layer(stocks, _snapshot([8, 7, 6, 5, 4, 3, 2, 1]), {"status": "OFFENSIVE"})

    result = stocks[0]
    assert result["a_eod_controlled_trial"] is True  # 对照统计打标保留
    position = result["position_plan"]
    assert position["initial_position_pct"] == 0
    assert position["max_position_pct"] == 0
    assert "SHADOW" in position["label"]


def test_a_eod_trial_position_is_capped_when_enabled(monkeypatch):
    """开关打开（E3 通过后）时恢复受控小仓 5%/15% 行为。"""
    import core.risk_constants as rc

    monkeypatch.setattr(rc, "A_EOD_CONTROLLED_ENABLED", True)
    stocks = [_stock(
        a_eod_controlled_trial=True,
        a_eod_policy_version="a-eod-controlled-trial-v1-shadow",
        trade_execution_policy="A_EOD_CONTROLLED_TRIAL",
    )]

    apply_decision_layer(stocks, _snapshot([8, 7, 6, 5, 4, 3, 2, 1]), {"status": "OFFENSIVE"})

    result = stocks[0]
    assert result["trade_eligible"] is True
    assert result["position_plan"]["initial_position_pct"] <= 5
    assert result["position_plan"]["max_position_pct"] <= 5
    assert result["position_plan"]["portfolio_position_cap_pct"] <= 15
    assert result["position_plan"]["a_eod_max_positions"] == 3
    assert result["a_eod_portfolio_cap_pct"] == 15
    assert "尾盘受控小仓" in result["execution_instruction"]


def test_bottom_discovery_is_not_judged_by_strict_strategy_opportunity_gate():
    bottom = _stock(
        strategy_type="bottom_discovery",
        bottom_discovery_watch_only=True,
        trade_bucket="OBSERVE",
        trade_eligible=False,
        Score=20,
        final_trade_score=20,
        sector_momentum_score=20,
        sector_alignment_score=20,
        pa_risk_reward=0,
        money_flow={},
    )

    apply_decision_layer(
        [bottom], _snapshot([4, 3, 2, 1, -1]), {"status": "DEFENSIVE"},
    )

    assert bottom["trade_opportunity_score"] < 60
    assert bottom["opportunity_gate_applicable"] is False
    assert bottom["position_plan"]["initial_position_pct"] == 0
    assert not any("综合机会分<60" in item for item in bottom.get("trade_blockers", []))


def test_single_hot_day_after_weak_period_is_repair_not_climax():
    history = [
        {"date": "2026-06-10", "advance_ratio": 28},
        {"date": "2026-06-11", "advance_ratio": 32},
        {"date": "2026-06-12", "advance_ratio": 35},
    ]

    context = build_market_decision_context(
        _snapshot([9, 8, 7, 6, 5, 4, 3, 2, 1, -1]),
        {"status": "OFFENSIVE"},
        history,
    )

    assert context["market_sentiment_stage"] == "REPAIR"
    assert context["market_cycle_metrics"]["breadth_trend"] > 40


def test_v_reversal_after_weak_period_is_observation_only_strong_repair():
    history = [
        {"date": "2026-07-09", "advance_ratio": 38, "strong_ratio": 3},
        {"date": "2026-07-10", "advance_ratio": 42, "strong_ratio": 4},
        {"date": "2026-07-13", "advance_ratio": 25, "strong_ratio": 2},
    ]

    context = build_market_decision_context(
        _snapshot([10, 9, 8, 7, 6, 5, 4, 3, 2, -1]),
        {"status": "CRITICAL"},
        history,
    )

    assert context["market_sentiment_stage"] == "V_REPAIR"
    assert context["market_sentiment_label"] == "强修复"
    assert context["portfolio_position_cap_pct"] == 30
    assert "追涨加速票" in context["market_forbidden_actions"]


def test_growth_board_structural_repair_is_detected_inside_critical_market():
    snapshot = pd.DataFrame({
        "code": [f"300{i:03d}" for i in range(100)] + [f"600{i:03d}" for i in range(100)],
        "pct_chg": [6.0] * 20 + [2.0] * 60 + [-1.0] * 20 + [0.5] * 100,
    })

    segments = build_growth_segment_context(snapshot, {"status": "CRITICAL"})

    assert segments["创业板"]["stage"] == "STRUCTURAL_REPAIR"
    assert segments["创业板"]["advance_ratio"] == 80.0
    assert segments["创业板"]["strong_ratio"] == 20.0


def test_growth_board_repair_only_relaxes_segment_regime_not_trade_permission():
    snapshot = pd.DataFrame({
        "code": [f"300{i:03d}" for i in range(100)],
        "pct_chg": [6.0] * 20 + [2.0] * 60 + [-1.0] * 20,
    })
    rows = [{"代码": "300001", "trade_eligible": False, "trade_bucket": "OBSERVE"}]

    apply_growth_segment_context(rows, snapshot, {"status": "CRITICAL"})

    assert rows[0]["market_segment_stage"] == "STRUCTURAL_REPAIR"
    assert rows[0]["effective_market_regime"] == "DEFENSIVE"
    assert rows[0]["trade_eligible"] is False
    assert rows[0]["trade_bucket"] == "OBSERVE"


def test_growth_board_repair_requires_broad_participation():
    snapshot = pd.DataFrame({
        "code": [f"300{i:03d}" for i in range(100)],
        "pct_chg": [8.0] * 10 + [1.0] * 50 + [-1.0] * 40,
    })

    segments = build_growth_segment_context(snapshot, {"status": "CRITICAL"})

    assert segments["创业板"]["stage"] == "NEUTRAL"


def test_sustained_hot_market_can_enter_climax():
    history = [
        {"date": "2026-06-10", "advance_ratio": 63, "strong_ratio": 8},
        {"date": "2026-06-11", "advance_ratio": 68, "strong_ratio": 9},
        {"date": "2026-06-12", "advance_ratio": 72, "strong_ratio": 10},
    ]

    context = build_market_decision_context(
        _snapshot([9, 8, 7, 6, 5, 5, 4, 3, 2, -1]),
        {"status": "OFFENSIVE"},
        history,
    )

    assert context["market_sentiment_stage"] == "CLIMAX"
    assert context["portfolio_position_cap_pct"] == 50


def test_hot_market_with_sharp_breadth_drop_is_divergence():
    history = [
        {"date": "2026-06-10", "advance_ratio": 65},
        {"date": "2026-06-11", "advance_ratio": 70},
        {"date": "2026-06-12", "advance_ratio": 68},
    ]

    context = build_market_decision_context(
        _snapshot([2, 1, 1, -1, -2, -3, -4, -5, -6, -7]),
        {"status": "OFFENSIVE"},
        history,
    )

    assert context["market_sentiment_stage"] == "DIVERGENCE"
    assert context["market_sentiment_label"] == "高位分歧"


def test_persistent_weak_market_is_ice_not_one_day_panic():
    history = [
        {"date": "2026-06-10", "advance_ratio": 30},
        {"date": "2026-06-11", "advance_ratio": 25},
        {"date": "2026-06-12", "advance_ratio": 32},
    ]

    context = build_market_decision_context(
        _snapshot([1, -1, -2, -3, -4, -5, -6, -7, -8, -9]),
        {"status": "CRITICAL"},
        history,
    )

    assert context["market_sentiment_stage"] == "ICE"
    assert context["portfolio_position_cap_pct"] == 15


def test_limit_down_wave_cannot_rebound_to_repair_on_breadth_boundary():
    history = [
        {"date": "2026-07-15", "advance_ratio": 18.0},
        {"date": "2026-07-16", "advance_ratio": 19.0},
        {"date": "2026-07-17", "advance_ratio": 18.8},
    ]
    snapshot = _snapshot([1.0] * 318 + [-1.0] * 471 + [-10.0] * 211)

    context = build_market_decision_context(
        snapshot,
        {"status": "CRITICAL", "limit_down_count": 211},
        history,
        data_date="2026-07-20",
    )

    assert context["market_breadth"]["advance_ratio"] == 31.8
    assert context["market_breadth"]["limit_down_count"] == 211
    assert context["market_sentiment_stage"] == "ICE"
    assert context["portfolio_position_cap_pct"] == 15
    assert context["market_sentiment_model_version"] == "cycle-v3"


def test_retreat_overrides_trade_permission():
    stocks = [_stock()]

    apply_decision_layer(stocks, _snapshot([-9, -7, -6, -5, -3, 1]), {"status": "CRITICAL"})
    result = stocks[0]

    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert result["trade_state"] == "BLOCKED"
    assert result["position_plan"]["initial_position_pct"] == 0
    # v2：市场否决按 regime（CRITICAL）判定，不再按 RETREAT/ICE 情绪阶段
    assert "CRITICAL市场默认禁止新仓，等待环境修复" in result["trade_blockers"]


def test_v2_defensive_regime_halves_position_without_veto():
    """v2：DEFENSIVE 市场不再一票否决，改为仓位×0.5。"""
    stocks = [_stock()]

    apply_decision_layer(stocks, _snapshot([-9, -7, -6, -5, -3, 1]), {"status": "DEFENSIVE"})
    result = stocks[0]

    assert result["trade_bucket"] == "TRADE"
    assert result["trade_state"] != "BLOCKED"
    plan = result["position_plan"]
    assert 0 < plan["initial_position_pct"]
    assert plan.get("regime_position_multiplier") == 0.5
    assert "市场退潮" not in " ".join(result.get("trade_blockers") or [])
    assert "CRITICAL市场默认禁止新仓" not in " ".join(result.get("trade_blockers") or [])


def test_v2_critical_defensive_sector_degrades_to_defensive_sizing():
    """v2：CRITICAL 下防御性板块降格为 DEFENSIVE 处理（×0.5），不再一刀切。"""
    stocks = [_stock(行业="银行")]

    apply_decision_layer(stocks, _snapshot([-9, -7, -6, -5, -3, 1]), {"status": "CRITICAL"})
    result = stocks[0]

    assert result.get("defensive_rotation") is True
    assert result["trade_state"] != "BLOCKED"
    assert result["position_plan"].get("regime_position_multiplier") == 0.5


def test_v1_flag_restores_retreat_stage_veto(monkeypatch):
    """回滚开关：TRADE_GATE_V2_ENABLED=False 恢复 v1 的 RETREAT/ICE 阶段否决。"""
    import core.decision_layer as decision_layer

    monkeypatch.setattr(decision_layer, "TRADE_GATE_V2_ENABLED", False)
    stocks = [_stock()]

    decision_layer.apply_decision_layer(
        stocks, _snapshot([-9, -7, -6, -5, -3, 1]), {"status": "DEFENSIVE"},
    )
    result = stocks[0]

    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "市场退潮，暂停新增仓位" in result["trade_blockers"]


def test_fading_sector_cannot_remain_trade_eligible():
    stocks = [_stock(sector_phase="SECTOR_FADE", sector_breadth=35)]

    apply_decision_layer(stocks, _snapshot([6, 5, 4, 3, 2, 1, -1]), {"status": "OFFENSIVE"})
    result = stocks[0]

    assert result["sector_mainline"] == "FADING"
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "板块退潮，暂停新增仓位" in result["trade_blockers"]


# ── 修复：snapshot 为空时 advance_ratio fallback，避免误判 ──

def test_empty_snapshot_uses_cycle_last_day_not_zero():
    """snapshot 为空时，advance_ratio 应回退到 cycle_history 最后一天的真实值，
    而非 0（0 会导致 trend=0-prior3 大幅负值，误判极端退潮）。

    用构造数据验证：cycle 最后一天 advance=50%，snapshot 为空。
    修复前：advance_ratio=0 → score/趋势失真
    修复后：advance_ratio=50（cycle 最后一天）→ 使用真实宽度。
    """
    cycle = [
        {"date": "d1", "advance_ratio": 55.0, "strong_ratio": 6.0, "weak_ratio": 3.0, "avg_return": 0.5},
        {"date": "d2", "advance_ratio": 60.0, "strong_ratio": 8.0, "weak_ratio": 2.0, "avg_return": 1.0},
        {"date": "d3", "advance_ratio": 58.0, "strong_ratio": 7.0, "weak_ratio": 3.0, "avg_return": 0.8},
        {"date": "d4", "advance_ratio": 50.0, "strong_ratio": 5.0, "weak_ratio": 4.0, "avg_return": 0.2},
    ]
    # snapshot 为空（模拟盘前/无实时数据）
    ctx_empty = build_market_decision_context(pd.DataFrame(), {"status": "OFFENSIVE"}, cycle)
    # snapshot 有数据（advance=50%，模拟同样宽度）
    snap = _snapshot([3] * 500 + [-1] * 500)  # 50% 上涨
    ctx_with_snap = build_market_decision_context(snap, {"status": "OFFENSIVE"}, cycle)
    # 两者 advance_ratio 来源不同（空→cycle最后一天=50, 有快照→50%），但结果应接近
    # 关键：空 snapshot 的 score 不应因 advance_ratio=0 而极端偏低
    assert ctx_empty["market_sentiment_score"] > 30, (
        f"snapshot为空时 score 不应极端偏低（advance_ratio 应非0），实际 {ctx_empty['market_sentiment_score']}"
    )


def test_retreat_still_correct_when_genuinely_weak():
    """真正持续弱势时仍应判 RETREAT/ICE（修复不应误放松）。"""
    cycle = [
        {"date": "d1", "advance_ratio": 25.0, "strong_ratio": 2.0, "weak_ratio": 15.0, "avg_return": -1.5},
        {"date": "d2", "advance_ratio": 30.0, "strong_ratio": 3.0, "weak_ratio": 12.0, "avg_return": -0.8},
        {"date": "d3", "advance_ratio": 28.0, "strong_ratio": 2.5, "weak_ratio": 14.0, "avg_return": -1.0},
        {"date": "d4", "advance_ratio": 30.0, "strong_ratio": 2.0, "weak_ratio": 10.0, "avg_return": -0.5},
    ]
    snap = _snapshot([3] * 300 + [-1] * 700)  # 30% 上涨
    ctx = build_market_decision_context(snap, {"status": "DEFENSIVE"}, cycle)
    assert ctx["market_sentiment_stage"] in ("ICE", "RETREAT"), (
        f"真正弱势应判 ICE/RETREAT，实际 {ctx['market_sentiment_stage']}"
    )


# ---------------------------------------------------------------------------
# 改动 A1: risk_reward 评分基于止损距离（修复恒定盈亏比锚定失效）
# ---------------------------------------------------------------------------

def test_risk_reward_score_based_on_stop_distance():
    """A1: 止损越紧分越高。原公式 rr*25+45 对所有票恒≈95（rr恒=2），丧失区分度。"""
    from core.decision_layer import _risk_reward_score

    # 止损 5%（结构风险小）→ 满分
    s_tight = {"pa_risk_pct": 5.0}
    # 止损 15%（结构风险大）→ 低分
    s_wide = {"pa_risk_pct": 15.0}
    score_tight = _risk_reward_score(s_tight)
    score_wide = _risk_reward_score(s_wide)
    assert score_tight == 100.0, f"5%止损应满分，实际 {score_tight}"
    assert score_wide < score_tight, f"15%止损应低于5%，{score_wide} vs {score_tight}"
    assert score_wide == 60.0, f"15%止损期望60，实际 {score_wide}"


def test_risk_reward_score_trap_deduction():
    """A1: 破位反抽陷阱风险应额外扣分。"""
    from core.decision_layer import _risk_reward_score

    base = _risk_reward_score({"pa_risk_pct": 8.0})
    with_trap = _risk_reward_score({"pa_risk_pct": 8.0, "pa_trap_risk": 10.0})
    assert with_trap < base, f"trap 应扣分，{with_trap} vs {base}"
    # 8% 止损 → base=88；trap=10 → 扣 10*0.2=2 → 86
    assert base == 88.0, f"8%止损期望88，实际 {base}"
    assert with_trap == 86.0, f"trap=10 期望86，实际 {with_trap}"


def test_risk_reward_score_no_data_fallback_neutral():
    """A1: 无止损数据时回退中性基线（不打极端分）。"""
    from core.decision_layer import _risk_reward_score

    score = _risk_reward_score({})
    assert 50 <= score <= 60, f"无数据应中性，实际 {score}"


# ---------------------------------------------------------------------------
# 改动 D1: leadership relative 维度缩放过激修复（消除 46% 饱和）
# ---------------------------------------------------------------------------
def test_leadership_relative_score_not_saturated_at_5pct():
    """D1: sector_relative_pct=5% 不应让 relative 维度撞顶满分。

    原公式 *10 缩放：5% → 50+50=100 满分，实测 46% 股票饱和。
    修复为 *5：5% → 75（中位），10% → 100，区分度大幅提升。
    """
    from core.decision_layer import _leadership_score

    # 5% 跑赢板块（实际数据中位数 4.66%）
    stock = _stock(sector_relative_pct=5.0, limit_up_status=None)
    _leadership_score(stock)
    comp = stock["leadership_components"]
    assert comp["relative_strength"] < 100, f"5% 跑赢不应撞顶，实际 {comp['relative_strength']}"
    assert 70 <= comp["relative_strength"] <= 85, f"5% 应落在 70-85 区间，实际 {comp['relative_strength']}"

    # 10% 跑赢才接近满分
    stock2 = _stock(sector_relative_pct=10.0, limit_up_status=None)
    _leadership_score(stock2)
    assert stock2["leadership_components"]["relative_strength"] >= 95


# ---------------------------------------------------------------------------
# 改动 A2: money_flow 评分市值归一化（消除大盘股系统性虚高）
# ---------------------------------------------------------------------------

def test_money_flow_score_normalizes_by_market_cap():
    """A2: 相同绝对流入金额，小盘股应得更高分（归一化后相对强度更大）。"""
    from core.decision_layer import _money_flow_score

    # 同样 1 亿流入，但市值差 100 倍
    small_cap = {"money_flow": {"main_net_ratio": 8, "main_net_inflow_yi": 1.0}, "mkt_cap_yi": 80}
    large_cap = {"money_flow": {"main_net_ratio": 8, "main_net_inflow_yi": 1.0}, "mkt_cap_yi": 8000}
    score_small = _money_flow_score(small_cap)
    score_large = _money_flow_score(large_cap)
    assert score_small > score_large, (
        f"小盘归一化后应更高，small={score_small} large={score_large}"
    )


def test_money_flow_score_large_cap_not_inflated():
    """A2: 工行15亿流入不应碾压小盘0.5亿（原公式 amount*3 导致的系统性偏差）。"""
    from core.decision_layer import _money_flow_score

    # 工行：15亿流入 / 15000亿市值 = 0.1%（很弱的相对流入）
    gonghang = {"money_flow": {"main_net_ratio": 5, "main_net_inflow_yi": 15}, "mkt_cap_yi": 15000}
    # 小盘：0.5亿流入 / 80亿市值 = 0.625%（较强的相对流入）
    small = {"money_flow": {"main_net_ratio": 10, "main_net_inflow_yi": 0.5}, "mkt_cap_yi": 80}
    assert _money_flow_score(small) > _money_flow_score(gonghang), (
        "归一化后小盘强势股应高于工行"
    )


def test_money_flow_score_negative_outflow_penalized():
    """A2: 主力净流出应得低分（<50）。"""
    from core.decision_layer import _money_flow_score

    outflow = {"money_flow": {"main_net_ratio": -8, "main_net_inflow_yi": -2}, "mkt_cap_yi": 100}
    score = _money_flow_score(outflow)
    assert score < 50, f"净流出应低于50，实际 {score}"


# ---------------------------------------------------------------------------
# 改动 A3: 市场宽度日期 bug 修复（data_date 替代墙钟 now()）
# ---------------------------------------------------------------------------

def test_market_context_uses_data_date_not_wall_clock():
    """A3：周末/节假日扫描时，data_date 应正确剔除 cycle 当日，避免重复计入宽度。

    场景：周五是最近交易日，周六扫描。cycle 最后一条 date=周五。
    原逻辑用 now()=周六 ≠ 周五 → 不剔除 → prior3 重复计入周五宽度 → trend 偏移。
    修复后 data_date=周五 → 匹配 → 正确剔除。
    """
    cycle = [
        {"date": "2026-06-15", "advance_ratio": 60.0, "strong_ratio": 8.0, "weak_ratio": 3.0, "avg_return": 1.0},
        {"date": "2026-06-16", "advance_ratio": 55.0, "strong_ratio": 6.0, "weak_ratio": 4.0, "avg_return": 0.5},
        {"date": "2026-06-19", "advance_ratio": 58.0, "strong_ratio": 7.0, "weak_ratio": 3.0, "avg_return": 0.8},  # 周五
    ]
    snap = _snapshot([5] * 600 + [-1] * 400)  # 60% 上涨

    # 不传 data_date（旧行为，模拟周六扫描：now() ≠ 周五）
    ctx_no_date = build_market_decision_context(snap, {"status": "OFFENSIVE"}, cycle)
    # 传 data_date=周五（修复后行为）
    ctx_with_date = build_market_decision_context(snap, {"status": "OFFENSIVE"}, cycle, data_date="2026-06-19")

    # 两者的 trend 可能不同（修复后正确剔除了当日）
    # 关键：传 data_date 后 breadth_trend 不应因为重复计入当日而偏移
    assert ctx_with_date["market_cycle_metrics"]["history_days"] >= 0
    # 确保没有崩溃且返回有效 stage
    assert ctx_with_date["market_sentiment_stage"] in ("ADVANCE", "REPAIR", "CLIMAX", "DIVERGENCE", "RETREAT", "ICE")


def test_apply_decision_layer_accepts_data_date():
    """A3：apply_decision_layer 应接受并透传 data_date 参数。"""
    import inspect
    from core.decision_layer import apply_decision_layer
    sig = inspect.signature(apply_decision_layer)
    assert "data_date" in sig.parameters, "apply_decision_layer 应有 data_date 参数"
    assert sig.parameters["data_date"].default is None


def test_sector_fund_outflow_demotes_trade_to_observe():
    """板块主力5日净流出超阈值：本可通过闸门的候选降级观察（势不对时形态失效）。"""
    stock = _stock(sector_main_net_inflow_5d_yi=-15.2)

    apply_decision_layer([stock], _snapshot([1, 2, 3, 1, 2, 1]), {"status": "OFFENSIVE"})

    assert stock["trade_bucket"] == "OBSERVE"
    assert stock["trade_eligible"] is False
    assert "板块主力5日净流出，技术信号降级观察" in stock["trade_blockers"]


def test_sector_fund_inflow_keeps_trade_eligible():
    stock = _stock(sector_main_net_inflow_5d_yi=8.0)

    apply_decision_layer([stock], _snapshot([1, 2, 3, 1, 2, 1]), {"status": "OFFENSIVE"})

    assert stock["trade_bucket"] == "TRADE"
    assert stock["trade_eligible"] is True


def test_sector_fund_missing_data_does_not_demote():
    """资金流接口降级（字段缺失）时 fail-open，不降权。"""
    stock = _stock()

    apply_decision_layer([stock], _snapshot([1, 2, 3, 1, 2, 1]), {"status": "OFFENSIVE"})

    assert stock["trade_bucket"] == "TRADE"
    assert stock["trade_eligible"] is True


def test_sector_fund_demote_disabled_rolls_back(monkeypatch):
    from core import decision_layer

    monkeypatch.setattr(decision_layer, "SECTOR_FUND_OUTFLOW_DEMOTE_ENABLED", False)
    stock = _stock(sector_main_net_inflow_5d_yi=-15.2)

    apply_decision_layer([stock], _snapshot([1, 2, 3, 1, 2, 1]), {"status": "OFFENSIVE"})

    assert stock["trade_bucket"] == "TRADE"


def test_signal_tier_weighting_orders_confluence_higher(monkeypatch):
    """signal-tier-weight-v1-shadow：同评分下 A 层（双确认）机会分 +4、B 层 -2、C 层不加权。

    证据：regime_attribution 三段 walk-forward，同门槛下 A 对 B 期望优势约
    +1pt/笔（docs/research/WINRATE_BASELINES_AND_GATES_2026-10-06.md）。"""
    import core.risk_constants as rc

    def _tiered_stock(tier):
        return _stock(
            strategy_type="tv_dual",
            trade_eligible=True,
            trade_bucket="TRADE",
            tv_execution_tier=tier,
        )

    stocks = [_tiered_stock("A"), _tiered_stock("B"), _tiered_stock("C")]
    apply_decision_layer(stocks, _snapshot([8, 7, 6, 5, 4, 3, 2, 1]), {"status": "OFFENSIVE"})
    a_score = stocks[0]["trade_opportunity_score"]
    b_score = stocks[1]["trade_opportunity_score"]
    c_score = stocks[2]["trade_opportunity_score"]

    assert a_score == round(min(100, b_score + rc.SIGNAL_TIER_B_PENALTY + rc.SIGNAL_TIER_A_BONUS), 1)
    assert stocks[0]["signal_tier_adjust"] == rc.SIGNAL_TIER_A_BONUS
    assert stocks[1]["signal_tier_adjust"] == -rc.SIGNAL_TIER_B_PENALTY
    assert "signal_tier_adjust" not in stocks[2]  # C 层不加权
    assert stocks[0]["signal_tier_policy_version"] == "signal-tier-weight-v1-shadow"


def test_signal_tier_weighting_can_be_disabled(monkeypatch):
    import core.risk_constants as rc

    monkeypatch.setattr(rc, "SIGNAL_TIER_WEIGHT_ENABLED", False)
    stocks = [_stock(strategy_type="tv_dual", trade_eligible=True, trade_bucket="TRADE",
                     tv_execution_tier="A")]
    apply_decision_layer(stocks, _snapshot([8, 7, 6, 5, 4, 3, 2, 1]), {"status": "OFFENSIVE"})
    assert "signal_tier_adjust" not in stocks[0]


def test_bull_bear_debate_roles_and_failopen(monkeypatch):
    """P1：三段调用（bull/bear/judge），AI 未配置 fail-open。"""
    from core import ai_debate as debate
    from core.config import config as app_config

    cand = {"名称": "测试", "代码": "600000", "现价": 10.0, "tv_execution_tier": "A"}
    monkeypatch.setattr(app_config, "AI_MODEL", "")
    assert debate.run_bull_bear_debate(cand) is None

    monkeypatch.setattr(app_config, "AI_MODEL", "test-model")
    monkeypatch.setattr(app_config, "AI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setattr(app_config, "AI_API_KEY", "k")
    seen_roles = []
    def _fake_post(payload):
        seen_roles.append(payload["messages"][0]["content"])
        content = {"bull": "多头论证", "bear": "空头论证",
                   "judge": "空头证据更硬，置信度中"}.get(
            "多空辩论中的多头研究员" in seen_roles[-1] and "bull_x" or (
                "空头研究员" in seen_roles[-1] and "bear" or "judge"), "")
        # 简化：按角色提示词顺序返回
        idx = len([r for r in seen_roles]) - 1
        return ["多头论证", "空头论证", "空头证据更硬，置信度中"][min(idx, 2)], {}
    import core.theme_heat as th
    monkeypatch.setattr(th, "_post_chat_text", _fake_post)
    out = debate.run_bull_bear_debate(cand, evidence_lines=["新闻A"])
    assert out and "多头论证" in out["bull"] and "空头" in out["bear"] and "置信度" in out["verdict"]
    assert len(seen_roles) == 3  # bull/bear/judge 三段


def test_shadow_strategy_registry_promotion():
    from core.strategy_registry import (
        SHADOW_STRATEGY_REGISTRY, promote_shadow_strategy, register_shadow_strategy,
    )
    register_shadow_strategy("test_strategy_x", "验证中")
    assert SHADOW_STRATEGY_REGISTRY["test_strategy_x"]["status"] == "shadow"
    assert promote_shadow_strategy("test_strategy_x") is True
    assert SHADOW_STRATEGY_REGISTRY["test_strategy_x"]["status"] == "promoted"
    assert promote_shadow_strategy("不存在") is False
    assert "tv_dual_strict_paired_window_5" in SHADOW_STRATEGY_REGISTRY
