"""《交易之路》规则落地回归测试：
1. 波动率指标与双向预警（_amplitude_metrics）
2. 日线趋势状态（_daily_trend_state）与三周期关系（classify_mtf_relation）
3. 决策层波动率闸门与软约束（amp20 gate + trade_cautions）
4. 连错熔断（loss streak breaker）
5. 周线MACD顶背离（detect_weekly_macd_top_divergence）
6. 季节性提示（get_seasonality_note）
"""
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.decision_layer as decision_layer
from core.decision_layer import apply_decision_layer
from core.execution_intents import (
    _loss_streak_breaker_reason,
    create_bark_execution_intents,
    get_paper_loss_streak,
)
from core.market_regime import (
    detect_weekly_macd_top_divergence,
    get_seasonality_note,
)
from core.models import Base
from core.price_action import (
    _amplitude_metrics,
    _daily_trend_state,
    analyze_price_action,
)
from core.timeframe_context import classify_mtf_relation


def _frame(n, close_fn, amp_pct):
    """构造 OHLCV：收盘按 close_fn(i)，日振幅固定为 amp_pct%%。"""
    closes = [close_fn(i) for i in range(n)]
    dates = pd.bdate_range("2025-01-01", periods=n)
    closes = np.asarray(closes, dtype=float)
    half = closes * (amp_pct / 100 / 2)
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘": closes,
        "最高": closes + half,
        "最低": closes - half,
        "收盘": closes,
        "成交量": np.full(n, 1e7),
    })


# ── 1. 波动率指标 ──

def test_amp_metrics_spike_warn_on_low_vol_breakout():
    # 40 天平波(1.0%) + 20 天骤增(5.5%)：amp20=5.5, amp60=2.5 → 1.8倍骤增 → 变盘观察
    df = _frame(60, lambda i: 10.0, 1.0)
    wild = _frame(20, lambda i: 10.0 + i * 0.02, 5.5)
    wild["日期"] = pd.bdate_range("2025-03-31", periods=20).strftime("%Y-%m-%d")
    df = pd.concat([df, wild], ignore_index=True)
    out = _amplitude_metrics(df)
    assert out["amp20"] == pytest.approx(5.5, abs=0.1)
    assert out["amp60"] == pytest.approx(2.5, abs=0.1)
    assert out["pa_amp_spike_warn"] is True
    assert out["pa_amp_collapse_warn"] is False


def test_amp_metrics_collapse_warn_on_strong_stock_cooling():
    # 40 天高波动(6%)冲高到 20 元，随后 20 天在高位窄幅(2%)横盘：骤降预警
    n_wild, n_calm = 40, 20
    wild = _frame(n_wild, lambda i: 10.0 + i * 0.25, 6.0)
    calm = _frame(n_calm, lambda i: 20.0, 2.0)
    calm["日期"] = pd.bdate_range("2025-03-31", periods=n_calm).strftime("%Y-%m-%d")
    df = pd.concat([wild, calm], ignore_index=True)
    out = _amplitude_metrics(df)
    # amp60 = (40*6 + 20*2)/60 ≈ 4.67，amp20=2.0 <= 0.6*4.67=2.8，且收盘在60日高点90%内
    assert out["pa_amp_collapse_warn"] is True


def test_amp_metrics_short_history_fails_open():
    out = _amplitude_metrics(_frame(10, lambda i: 10.0, 2.0))
    assert out["amp20"] is None
    assert out["pa_amp_collapse_warn"] is False
    assert out["pa_amp_spike_warn"] is False


# ── 2. 日线状态与三周期关系 ──

def test_daily_trend_state():
    assert _daily_trend_state(_frame(120, lambda i: 10 + i * 0.05, 2.0)) == "UP"
    assert _daily_trend_state(_frame(120, lambda i: 30 - i * 0.05, 2.0)) == "DOWN"
    assert _daily_trend_state(_frame(30, lambda i: 10.0, 2.0)) == "UNAVAILABLE"


def test_mtf_relation_mapping():
    assert classify_mtf_relation("UP", "PULLBACK", "UP") == "三周期共振多头"
    assert classify_mtf_relation("UP", "PULLBACK", "MIXED") == "顺大势逆小势·中期回踩"
    assert classify_mtf_relation("DOWN", "WEAK", "UP") == "逆大势反弹·不追"
    assert classify_mtf_relation("DOWN", "WEAK", "MIXED") == "大小同向向下·回避"
    assert classify_mtf_relation("UNAVAILABLE", "UNAVAILABLE", "UP") == "数据不足"
    assert classify_mtf_relation("REPAIR", "NEUTRAL", "MIXED") == "周期方向不明"


def test_analyze_price_action_carries_new_fields():
    df = _frame(160, lambda i: 10 + i * 0.05, 2.5)
    result = analyze_price_action(df)
    assert isinstance(result["amp20"], float)
    assert result["pa_daily_state"] == "UP"
    assert result["pa_mtf_relation"] in {
        "三周期共振多头", "顺大势逆小势·中期回踩", "逆大势反弹·不追",
        "大小同向向下·回避", "周期方向不明", "数据不足",
    }


# ── 3. 决策层：波动率闸门与软约束 ──

def _snapshot():
    return pd.DataFrame({"pct_chg": [6, 5, 4, 3, 2, 1, -1]})


def _stock(**overrides):
    item = {
        "代码": "000001", "名称": "测试股票", "涨幅%": 4.0,
        "Score": 82, "final_trade_score": 85,
        "trade_bucket": "TRADE", "trade_eligible": True,
        "sector_phase": "SECTOR_CONFIRM", "sector_rank": 1,
        "sector_momentum_score": 82, "sector_breadth": 75,
        "sector_5d_pct": 5, "sector_role": "LEADER",
        "sector_alignment_score": 85, "sector_relative_pct": 3,
        "pa_risk_reward": 2.5, "pa_trade_plan": {"action": "READY"},
        "pa_pullback_status": "CONFIRMED",
        "pa_entry_price": 10.5, "pa_stop_price": 9.8,
        "money_flow": {"main_net_ratio": 8, "main_net_inflow_yi": 1.5},
    }
    item.update(overrides)
    return item


def _run(stocks):
    apply_decision_layer(stocks, _snapshot(), {"status": "OFFENSIVE"})
    return stocks[0]


def test_amp20_gate_halves_position():
    baseline = _run([_stock()])["position_plan"]["initial_position_pct"]
    result = _run([_stock(amp20=7.2)])
    plan = result["position_plan"]
    assert plan["amp20_multiplier"] == 0.5
    assert plan["initial_position_pct"] == round(baseline * 0.5, 1)
    assert result["amp_gate_policy_version"] == "amp20-gate-v1"


def test_amp20_below_threshold_not_penalized():
    result = _run([_stock(amp20=5.9)])
    assert "amp20_multiplier" not in result["position_plan"]


def test_amp20_gate_disabled(monkeypatch):
    monkeypatch.setattr(decision_layer, "AMP20_GATE_ENABLED", False)
    result = _run([_stock(amp20=9.0)])
    assert "amp20_multiplier" not in result["position_plan"]


def test_soft_cautions_from_mtf_amp_and_chase():
    baseline = _run([_stock()])["position_plan"]["initial_position_pct"]
    result = _run([_stock(
        pa_mtf_relation="大小同向向下·回避",
        pa_amp_collapse_warn=True,
        **{"现价": 11.1},  # 10.5 → 11.1 = +5.7% > 3% 追价
    )])
    cautions = result["trade_cautions"]
    assert any("大小同向向下" in c for c in cautions)
    assert any("阴跌" in c for c in cautions)
    assert any("中途半端" in c for c in cautions)
    # 软约束走既有 ×0.5 通道
    assert result["position_plan"]["initial_position_pct"] == round(baseline * 0.5, 1)
    assert result["position_plan"].get("trade_caution_position_multiplier") == 0.5


def test_chase_caution_not_triggered_within_tolerance():
    result = _run([_stock(**{"现价": 10.7})])  # 10.5 → 10.7 = +1.9% < 3%
    assert not any("中途半端" in c for c in result.get("trade_cautions") or [])


# ── 4. 连错熔断 ──

def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _insert_closed(engine, pnl_list, last_days_ago=0):
    """插入已平仓记录，pnl_list 为正负百分比列表（按时间由近到远）。"""
    today = datetime.now().date()
    with engine.begin() as conn:
        for i, pnl in enumerate(pnl_list):
            close_date = today - timedelta(days=last_days_ago + i)
            conn.execute(text("""
                INSERT INTO paper_trading(code,name,entry_price,entry_date,current_price,status,close_price,close_date,trade_mode)
                VALUES (:code,'测试',100,:entry,95,'CLOSED',:close_price,:close_date,'SIMULATED')
            """), {
                "code": f"00000{i}",
                "entry": today - timedelta(days=last_days_ago + i + 5),
                "close_price": 100 + pnl,
                "close_date": close_date,
            })


def _stock_intent():
    return {
        "代码": "600519", "名称": "测试", "strategy_type": "tv_dual_strict",
        "trade_eligible": True, "trade_bucket": "TRADE",
        "pa_entry_price": 10.0, "pa_stop_price": 9.1, "pa_target_price": 12.0,
        "suggested_position_pct": 5,
    }


def test_loss_streak_breaker_blocks_intent_issuance():
    engine = _engine()
    _insert_closed(engine, [-8, -5, -3], last_days_ago=0)
    info = get_paper_loss_streak(engine)
    assert info["streak"] == 3
    reason = _loss_streak_breaker_reason(engine, datetime.now())
    assert reason and "连亏3笔熔断" in reason
    created = create_bark_execution_intents([_stock_intent()], engine)
    assert created == []
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM execution_intents")).scalar() == 0


def test_loss_streak_breaker_recovers_after_cooldown():
    engine = _engine()
    _insert_closed(engine, [-8, -5, -3], last_days_ago=3)  # 最后一笔平仓在 3 天前
    assert _loss_streak_breaker_reason(engine, datetime.now()) is None
    created = create_bark_execution_intents([_stock_intent()], engine)
    assert len(created) == 1


def test_loss_streak_broken_by_win():
    engine = _engine()
    _insert_closed(engine, [2, -8, -5])  # 最近一笔是盈利 → streak=0
    assert get_paper_loss_streak(engine)["streak"] == 0
    assert _loss_streak_breaker_reason(engine, datetime.now()) is None


def test_loss_streak_fail_open_on_empty_table():
    engine = _engine()
    assert _loss_streak_breaker_reason(engine, datetime.now()) is None


# ── 5. 周线MACD顶背离 ──

def _index_daily(closes):
    dates = pd.bdate_range("2023-01-02", periods=len(closes))
    return pd.Series(closes, index=dates)


def test_weekly_macd_divergence_detected_on_weakening_second_high():
    rng = np.random.default_rng(1)
    # 第一段强势冲高(10→18)，回调到16，第二段磨出新高18.5但动能明显衰减
    leg1 = np.linspace(10, 18, 130)
    pullback = np.linspace(18, 16.2, 40)
    leg2 = np.linspace(16.2, 18.6, 220) + rng.normal(0, 0.05, 220)
    series = _index_daily(np.concatenate([leg1, pullback, leg2]))
    assert detect_weekly_macd_top_divergence(series) is True


def test_weekly_macd_divergence_absent_in_steady_uptrend():
    closes = np.linspace(10, 16, 400)  # 匀速上行，无背离
    assert detect_weekly_macd_top_divergence(_index_daily(closes)) is False


def test_weekly_macd_divergence_fails_open_on_insufficient_data():
    assert detect_weekly_macd_top_divergence(_index_daily(np.linspace(10, 12, 80))) is False
    assert detect_weekly_macd_top_divergence(None) is False


# ── 6. 季节性提示 ──

def test_seasonality_note():
    assert "强月" in get_seasonality_note(10)
    assert "最弱" in get_seasonality_note(6)
    assert get_seasonality_note(5) is None


# ── 7. 跟进失败软约束 + 图表提示 + 个股背离 shadow 字段 ──

def test_follow_through_failed_adds_caution():
    baseline = _run([_stock()])["position_plan"]["initial_position_pct"]
    result = _run([_stock(pa_follow_through_state="FAILED")])
    assert any("该涨不涨" in c for c in result["trade_cautions"])
    # 走既有软约束通道，仓位减半
    assert result["position_plan"]["initial_position_pct"] == round(baseline * 0.5, 1)


def test_analyze_exposes_weekly_macd_divergence_shadow_field():
    df = _frame(160, lambda i: 10 + i * 0.05, 2.5)
    result = analyze_price_action(df)
    assert isinstance(result["pa_weekly_macd_divergence"], bool)


def test_chart_hints_mapping():
    from core.price_action import build_chart_hints

    hints = build_chart_hints({
        "pa_trend_phase": "衰竭段",
        "pa_trend_phase_action": "不追高，优先保护利润",
        "pa_mtf_relation": "逆大势反弹·不追",
        "pa_amp_collapse_warn": True,
        "pa_follow_through_state": "FAILED",
        "pa_weekly_macd_divergence": True,
    }, {
        "status": "DEFENSIVE",
        "desc": "减仓观望：市场进入震荡/分化期",
        "weekly_macd_divergence": True,
        "seasonality_note": "历史强月（仅供参考）",
    })
    levels = {h["level"] for h in hints}
    assert "danger" in levels and "warning" in levels and "info" in levels
    texts = [h["text"] for h in hints]
    assert any("衰竭段" in t for t in texts)
    assert any("逆大势反弹" in t for t in texts)
    assert any("阴跌" in t for t in texts)
    assert any("该涨不涨" in t for t in texts)
    assert any("个股周线MACD顶背离" in t for t in texts)
    assert any("双指数周线MACD顶背离" in t for t in texts)
    assert any("减仓观望" in t for t in texts)
    assert any("季节提示" in t for t in texts)


def test_chart_hints_minimal_for_clean_stock():
    from core.price_action import build_chart_hints

    hints = build_chart_hints({
        "pa_trend_phase": "二次入场",
        "pa_trend_phase_action": "只在触发价有效站上后执行",
        "pa_mtf_relation": "三周期共振多头",
    }, None)
    assert all(h["level"] == "info" for h in hints)
    assert len(hints) == 2
    # 空输入不产生提示
    assert build_chart_hints(None, None) == []


def test_to_daily_close_series_compatibility():
    from core.market_regime import to_daily_close_series

    df = pd.DataFrame({"date": pd.bdate_range("2025-01-01", periods=10), "close": np.linspace(10, 11, 10)})
    series = to_daily_close_series(df)
    assert series is not None and len(series) == 10
    assert to_daily_close_series(pd.DataFrame({"x": [1, 2]})) is None
    assert to_daily_close_series(None) is None


def test_chart_hints_surface_trendline_break():
    from core.price_action import build_chart_hints

    hints = build_chart_hints({
        "pa_trend_phase": "震荡观察",
        "pa_mtr_state": "TRENDLINE_BREAK",
    }, None)
    assert any("趋势线破位" in h["text"] for h in hints)
    assert all(h["level"] in {"info", "warning", "danger"} for h in hints)


# ── 8. 执行策略投影（未来可能走势 + 关键价位）──

def test_trade_projection_builds_levels_and_scenarios():
    from core.price_action import build_trade_projection

    projection = build_trade_projection({
        "pa_entry_price": 21.0,
        "pa_stop_price": 19.5,
        "pa_target_price": 24.0,
        "pa_pullback_support_price": 20.2,
        "pa_trade_setup": "强势回踩确认",
        "pa_trend_phase_action": "顺势观察回踩入场",
    }, last_close=20.55)
    assert projection is not None
    labels = [level["label"] for level in projection["key_levels"]]
    assert labels == ["回踩买入区", "突破触发价", "失效止损", "第一目标"]
    names = [s["name"] for s in projection["scenarios"]]
    assert "回踩再上攻" in names and "直接上攻" in names and "破位失效" in names
    assert "非行情预测" in projection["note"]


def test_trade_projection_without_upside_room_only_invalidations():
    from core.price_action import build_trade_projection

    # 目标价低于现价（已涨过）→ 不画上行情景，仅失效路径
    projection = build_trade_projection({
        "pa_entry_price": 21.0, "pa_stop_price": 19.5, "pa_target_price": 20.0,
    }, last_close=22.0)
    assert projection is not None
    assert [s["name"] for s in projection["scenarios"]] == ["破位失效"]


def test_trade_projection_fails_open_without_entry_or_stop():
    from core.price_action import build_trade_projection

    assert build_trade_projection({"pa_stop_price": 19.5}, last_close=20.0) is None
    assert build_trade_projection({"pa_entry_price": 21.0}, last_close=20.0) is None
    assert build_trade_projection(None, last_close=20.0) is None
