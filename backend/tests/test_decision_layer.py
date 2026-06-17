import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.decision_layer import apply_decision_layer, build_market_decision_context
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


def test_retreat_overrides_trade_permission():
    stocks = [_stock()]

    apply_decision_layer(stocks, _snapshot([-9, -7, -6, -5, -3, 1]), {"status": "CRITICAL"})
    result = stocks[0]

    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert result["trade_state"] == "BLOCKED"
    assert result["position_plan"]["initial_position_pct"] == 0
    assert "市场退潮，暂停新增仓位" in result["trade_blockers"]


def test_fading_sector_cannot_remain_trade_eligible():
    stocks = [_stock(sector_phase="SECTOR_FADE", sector_breadth=35)]

    apply_decision_layer(stocks, _snapshot([6, 5, 4, 3, 2, 1, -1]), {"status": "OFFENSIVE"})
    result = stocks[0]

    assert result["sector_mainline"] == "FADING"
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert "板块退潮，暂停新增仓位" in result["trade_blockers"]


# ── 修复：cycle_history 日期判断（避免 T+1 执行时误判 RETREAT）──

def test_cycle_history_last_day_always_excluded_from_prior():
    """cycle_history 最后一条（最新数据日）应始终从 prior 中剔除。

    修复前：用 str(today) 判断，当扫描在 T+1 执行但数据只到 T 日时，
    T 日不被剔除 → 被算入 prior3 → 拉低均值 → 误判 RETREAT。
    修复后：始终剔除最后一条，无论日期。
    """
    # 构造：前3日强(70%) + 最后1日退潮(30%)
    cycle = [
        {"date": "2026-06-12", "advance_ratio": 71.0, "strong_ratio": 7.6, "weak_ratio": 3.8, "avg_return": 1.1},
        {"date": "2026-06-15", "advance_ratio": 70.3, "strong_ratio": 16.9, "weak_ratio": 1.3, "avg_return": 2.0},
        {"date": "2026-06-16", "advance_ratio": 50.3, "strong_ratio": 9.0, "weak_ratio": 1.6, "avg_return": 0.7},
        {"date": "2026-06-17", "advance_ratio": 30.5, "strong_ratio": 6.9, "weak_ratio": 2.4, "avg_return": -0.55},
    ]
    # snapshot 用 6/17 的宽度数据
    snap = _snapshot([3] * 305 + [-1] * 695)  # 30.5% 上涨
    ctx = build_market_decision_context(snap, {"status": "OFFENSIVE"}, cycle)
    # 修复后：prior3 = [6/12, 6/15, 6/16]（剔除6/17），avg=63.9 ≥ 58 + trend=-33 ≤ -15
    # → 应命中 DIVERGENCE，而非 RETREAT
    assert ctx["market_sentiment_stage"] == "DIVERGENCE", (
        f"应命中 DIVERGENCE（高位分歧），实际 {ctx['market_sentiment_stage']}"
    )
    assert ctx["portfolio_position_cap_pct"] == 40  # DIVERGENCE 仓位上限 40%


def test_retreat_still_correct_when_genuinely_weak():
    """真正持续弱势时仍应判 RETREAT（修复不应误放松）。"""
    # 构造：连续多日弱势（全部 advance < 38）
    cycle = [
        {"date": "d1", "advance_ratio": 25.0, "strong_ratio": 2.0, "weak_ratio": 15.0, "avg_return": -1.5},
        {"date": "d2", "advance_ratio": 30.0, "strong_ratio": 3.0, "weak_ratio": 12.0, "avg_return": -0.8},
        {"date": "d3", "advance_ratio": 28.0, "strong_ratio": 2.5, "weak_ratio": 14.0, "avg_return": -1.0},
        {"date": "d4", "advance_ratio": 30.0, "strong_ratio": 2.0, "weak_ratio": 10.0, "avg_return": -0.5},
    ]
    snap = _snapshot([3] * 300 + [-1] * 700)  # 30% 上涨
    ctx = build_market_decision_context(snap, {"status": "DEFENSIVE"}, cycle)
    # 持续弱势 → 应判 ICE 或 RETREAT（而非 DIVERGENCE/ADVANCE）
    assert ctx["market_sentiment_stage"] in ("ICE", "RETREAT"), (
        f"真正弱势应判 ICE/RETREAT，实际 {ctx['market_sentiment_stage']}"
    )
