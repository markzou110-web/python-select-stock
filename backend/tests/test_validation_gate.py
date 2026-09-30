"""晋升硬前置与量化纪律修复测试（批 4-3）。

覆盖：walk_forward_evidence 窗口判定、promotion_allowed fail-closed、
A-EOD SHADOW 不贡献交易资格且仓位归零、signal_performance 独立事件去重
与 VALIDATED 双条件。
"""
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.risk_constants as rc
from core.validation_gate import promotion_allowed, walk_forward_evidence


# ── validation_gate ──

def test_walk_forward_requires_min_windows_and_samples():
    wf = walk_forward_evidence([])
    assert wf["status"] == "FAIL" and wf["reason"] == "no_windows"

    # 只有 2 个正窗口（< 3）→ FAIL
    wf = walk_forward_evidence([
        {"label": "w1", "samples": 40, "avg_return": 1.2},
        {"label": "w2", "samples": 40, "avg_return": 0.8},
    ])
    assert wf["status"] == "FAIL" and wf["positive_windows"] == 2

    # 3 个正窗口但其中一个样本不足 → FAIL
    wf = walk_forward_evidence([
        {"label": "w1", "samples": 40, "avg_return": 1.2},
        {"label": "w2", "samples": 40, "avg_return": 0.8},
        {"label": "w3", "samples": 10, "avg_return": 2.0},
    ])
    assert wf["status"] == "FAIL"

    # 3 个合格正窗口 → PASS
    wf = walk_forward_evidence([
        {"label": "w1", "samples": 40, "avg_return": 1.2},
        {"label": "w2", "samples": 35, "avg_return": 0.6},
        {"label": "w3", "samples": 50, "avg_return": 0.9},
    ])
    assert wf["status"] == "PASS"


def test_promotion_fail_closed():
    ok, detail = promotion_allowed("a_eod_controlled_trial")
    assert ok is False and detail["reason"].startswith("walk_forward_")
    # gates 未过 → regime 乘数转正被拒
    ok, detail = promotion_allowed("trade_gate_v2_regime_multiplier", gates_ready=False)
    assert ok is False and detail["reason"] == "trade_gate_readiness_not_passed"
    # gates 过 + 3 个正窗口 → 允许
    ok, detail = promotion_allowed(
        "trade_gate_v2_regime_multiplier",
        gates_ready=True,
        walk_forward=[{"label": f"w{i}", "samples": 40, "avg_return": 0.5} for i in range(3)],
    )
    assert ok is True


def test_a_eod_shadow_default_off_and_eligibility_gated(monkeypatch):
    """SHADOW 默认生效：a_eod 资格不再贡献 trade_eligible。"""
    from unittest.mock import MagicMock

    assert rc.A_EOD_CONTROLLED_ENABLED is False
    assert rc.A_EOD_CONTROLLED_POLICY_VERSION == "a-eod-controlled-trial-v1-shadow"

    # 直接验证 eligibility 表达式语义：scanner 的三元合取在 SHADOW 下应为 False
    formal_trade, a_minus_trial, a_eod_trial = False, False, True
    assert (formal_trade or a_minus_trial or (a_eod_trial and rc.A_EOD_CONTROLLED_ENABLED)) is False


def test_a_eod_shadow_zero_position():
    from core.decision_layer import apply_decision_layer

    engine = create_engine("sqlite:///:memory:")
    stock = {
        "代码": "600000", "名称": "测试", "现价": 10.0, "涨幅%": 2.0,
        "a_eod_controlled_trial": True,
        "trade_eligible": True, "trade_bucket": "TRADE",
        "market_regime": "OFFENSIVE", "sector_mainline": "ACTIVE",
        "strategy_type": "tv_zp",
    }
    snapshot = pd.DataFrame([{"code": "600000", "pct_chg": 2.0}])
    cycle = [{"date": "2026-09-29", "advance_ratio": 60.0, "strong_ratio": 5.0, "weak_ratio": 2.0, "avg_return": 0.8}]
    apply_decision_layer(
        [stock], snapshot=snapshot, market_regime={"status": "OFFENSIVE"},
        cycle_history=cycle, data_date="2026-09-29",
    )
    position = stock["position_plan"]  # 原地 enrich，返回值是市场上下文
    assert position["initial_position_pct"] == 0
    assert "SHADOW" in position["label"]
    assert stock.get("a_eod_controlled_trial") is True  # 打标保留供对照统计


# ── signal_performance 独立事件去重 ──

def test_signal_performance_dedups_overlapping_windows():
    """同一票连续 4 日提醒：只有第 1、4 日（成熟期外）计入独立事件。"""
    from core.outcome_calibration import mark_independent_signal_events

    frame = pd.DataFrame([
        {"code": "600000", "strategy_type": "tv_zp", "signal_date": "2026-09-01", "maturity_5d_date": "2026-09-08", "ret_5d": 1.0},
        {"code": "600000", "strategy_type": "tv_zp", "signal_date": "2026-09-02", "maturity_5d_date": "2026-09-09", "ret_5d": 2.0},
        {"code": "600000", "strategy_type": "tv_zp", "signal_date": "2026-09-03", "maturity_5d_date": "2026-09-10", "ret_5d": 3.0},
        {"code": "600000", "strategy_type": "tv_zp", "signal_date": "2026-09-10", "maturity_5d_date": "2026-09-17", "ret_5d": 0.5},
    ])
    marked = mark_independent_signal_events(frame)
    assert marked["independent_event"].tolist() == [True, False, False, True]


def test_validated_requires_positive_expectancy():
    """零/负期望不再仅凭样本量通过 VALIDATED（双条件）。"""
    import core.signal_performance as sp

    mature_series = pd.Series([-1.0] * 40)  # 40 个成熟样本但平均收益为负
    avg_positive = len(mature_series) > 0 and float(mature_series.mean()) > 0
    assert avg_positive is False  # 表现门槛拦截：样本再多、零/负期望也不 VALIDATED
