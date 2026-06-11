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

    assert context["market_sentiment_stage"] in {"ADVANCE", "CLIMAX"}
    assert result["sector_mainline"] == "MAIN"
    assert result["leadership_score"] >= 80
    assert result["trade_opportunity_score"] >= 70
    assert result["position_plan"]["initial_position_pct"] > 0
    assert result["trade_state"] == "CONFIRM_ADD"
    assert "10.50" in result["execution_instruction"]
    assert "9.80" in result["execution_instruction"]


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
