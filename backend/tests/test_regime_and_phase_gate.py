"""道氏市场闸门与趋势阶段仓位约束的回归测试。

覆盖两层保护：
1. 双指数相互验证闸门（REGIME_POSITION_MULTIPLIER）：
   CRITICAL 禁新仓 / DEFENSIVE 减半 / UNKNOWN 按防守处理 / 防御板块降格。
2. 道氏趋势阶段仓位约束（TREND_PHASE_POSITION_MULTIPLIER）：
   衰竭段/加速段减半，其它阶段不惩罚，开关可回滚。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.decision_layer as decision_layer
from core.decision_layer import apply_decision_layer


def _snapshot(values=None):
    return pd.DataFrame({"pct_chg": values or [6, 5, 4, 3, 2, 1, -1]})


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


def _run(stocks, status):
    apply_decision_layer(stocks, _snapshot(), {"status": status})
    return stocks[0]


# ── 1. 双指数相互验证闸门 ──

def test_offensive_regime_keeps_full_position_without_multiplier():
    result = _run([_stock()], "OFFENSIVE")
    assert result["trade_eligible"] is True
    assert result["position_plan"]["initial_position_pct"] > 0
    assert "regime_position_multiplier" not in result["position_plan"]


def test_critical_regime_blocks_new_entries():
    result = _run([_stock()], "CRITICAL")
    assert result["trade_eligible"] is False
    assert result["trade_bucket"] == "OBSERVE"
    assert any("CRITICAL" in reason for reason in result["trade_blockers"])
    assert result["position_plan"]["initial_position_pct"] == 0


def test_critical_regime_defensive_sector_downgrades_instead_of_blocking():
    result = _run([_stock(行业="银行")], "CRITICAL")
    assert result.get("defensive_rotation") is True
    assert result["position_plan"].get("regime_position_multiplier") == 0.5
    assert result["position_plan"]["initial_position_pct"] > 0


def test_defensive_regime_halves_position():
    offensive = _run([_stock()], "OFFENSIVE")
    defensive = _run([_stock()], "DEFENSIVE")
    assert defensive["position_plan"].get("regime_position_multiplier") == 0.5
    assert (
        defensive["position_plan"]["initial_position_pct"]
        == round(offensive["position_plan"]["initial_position_pct"] * 0.5, 1)
    )


def test_unknown_regime_is_treated_as_defensive_not_panic_block():
    result = _run([_stock()], "UNKNOWN")
    assert result["position_plan"].get("regime_position_multiplier") == 0.5
    assert result["trade_eligible"] is True


# ── 2. 道氏趋势阶段仓位约束 ──

def test_exhaustion_phase_halves_position():
    baseline = _run([_stock()], "OFFENSIVE")["position_plan"]["initial_position_pct"]
    result = _run([_stock(pa_trend_phase="衰竭段")], "OFFENSIVE")
    plan = result["position_plan"]
    assert plan["initial_position_pct"] > 0
    assert plan["trend_phase_multiplier"] == 0.5
    assert plan["initial_position_pct"] == round(baseline * 0.5, 1)
    assert result["trend_phase_gate_policy_version"] == "trend-phase-gate-v1"


def test_acceleration_phase_halves_position():
    baseline = _run([_stock()], "OFFENSIVE")["position_plan"]["initial_position_pct"]
    result = _run([_stock(pa_trend_phase="加速段")], "OFFENSIVE")
    assert result["position_plan"]["trend_phase_multiplier"] == 0.5
    assert result["position_plan"]["initial_position_pct"] == round(baseline * 0.5, 1)


def test_second_entry_phase_is_not_penalized():
    result = _run([_stock(pa_trend_phase="二次入场")], "OFFENSIVE")
    assert "trend_phase_multiplier" not in result["position_plan"]


def test_missing_phase_field_fails_open():
    result = _run([_stock()], "OFFENSIVE")
    assert "trend_phase_multiplier" not in result["position_plan"]


def test_phase_gate_can_be_disabled(monkeypatch):
    monkeypatch.setattr(decision_layer, "TREND_PHASE_POSITION_MULTIPLIER_ENABLED", False)
    result = _run([_stock(pa_trend_phase="衰竭段")], "OFFENSIVE")
    assert "trend_phase_multiplier" not in result["position_plan"]


def test_phase_penalty_stacks_with_defensive_regime():
    baseline = _run([_stock()], "DEFENSIVE")["position_plan"]["initial_position_pct"]
    result = _run([_stock(pa_trend_phase="衰竭段")], "DEFENSIVE")
    plan = result["position_plan"]
    assert plan["regime_position_multiplier"] == 0.5
    assert plan["trend_phase_multiplier"] == 0.5
    assert plan["initial_position_pct"] == round(baseline * 0.5, 1)
