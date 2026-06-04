import pandas as pd

from core.price_action import analyze_price_action, build_price_action_annotations


def _ohlc_from_closes(closes):
    rows = []
    for i, close in enumerate(closes):
        open_price = closes[i - 1] if i > 0 else close * 0.99
        high = max(open_price, close) * 1.01
        low = min(open_price, close) * 0.99
        rows.append({
            "日期": f"2026-05-{(i % 28) + 1:02d}",
            "开盘": round(open_price, 2),
            "最高": round(high, 2),
            "最低": round(low, 2),
            "收盘": round(close, 2),
            "成交量": 1000000 + i * 1000,
        })
    return pd.DataFrame(rows)


def test_price_action_detects_bull_context_and_outputs_risk_levels():
    closes = [10 + i * 0.12 for i in range(45)] + [15.5, 15.1, 15.7]
    df = _ohlc_from_closes(closes)
    df.loc[df.index[-1], "开盘"] = 15.0
    df.loc[df.index[-1], "收盘"] = 16.2
    df.loc[df.index[-1], "最高"] = 16.3
    df.loc[df.index[-1], "最低"] = 14.9

    result = analyze_price_action(df)

    assert result["price_action_score"] > 0
    assert result["price_action_regime"] in {"多头趋势", "向上突破"}
    assert result["pa_entry_price"] > result["pa_stop_price"]
    assert result["price_action_summary"]
    assert result["pa_market_cycle"]
    assert result["pa_range_location"] in {"区间上沿", "区间中部", "区间下沿"}
    assert result["pa_trade_plan"]["action"] in {"READY", "WATCH"}
    assert result["pa_trade_plan"]["entry_condition"]
    assert result["pa_trade_plan"]["invalidation"]


def test_price_action_handles_short_data():
    result = analyze_price_action(_ohlc_from_closes([10, 10.1, 10.2]))

    assert result["price_action_score"] == 0
    assert result["price_action_regime"] == "数据不足"
    assert result["pa_trade_plan"]["action"] == "AVOID"


def test_price_action_trade_plan_avoids_range_failed_breakout():
    closes = [10, 10.2, 10.1, 10.3, 10.15, 10.35, 10.2, 10.4] * 4
    df = _ohlc_from_closes(closes)
    df.loc[df.index[-2], "最高"] = 11.2
    df.loc[df.index[-2], "收盘"] = 10.9
    df.loc[df.index[-1], "开盘"] = 10.85
    df.loc[df.index[-1], "最高"] = 10.95
    df.loc[df.index[-1], "最低"] = 10.1
    df.loc[df.index[-1], "收盘"] = 10.25

    result = analyze_price_action(df)

    if result["price_action_pattern"] == "交易区间假突破":
        assert result["pa_trade_plan"]["action"] == "AVOID"
        assert result["pa_trade_plan"]["avoid_reasons"]


def test_price_action_detects_h2_two_legged_pullback():
    closes = [10 + i * 0.18 for i in range(35)]
    closes += [16.3, 15.9, 16.1, 15.7, 16.25]
    df = _ohlc_from_closes(closes)
    df.loc[df.index[-1], "开盘"] = 15.8
    df.loc[df.index[-1], "收盘"] = 16.35
    df.loc[df.index[-1], "最高"] = 16.45
    df.loc[df.index[-1], "最低"] = 15.75

    result = analyze_price_action(df)

    assert result["pa_pullback_structure"] == "双腿回调"
    assert result["pa_pullback_legs"] >= 2
    assert "H2" in result["pa_tags"] or result["price_action_pattern"] == "H2二次入场"
    assert result["pa_h2_quality"] in {"强", "中", "弱"}
    assert result["pa_entry_quality_score"] > 0
    assert result["pa_position_strategy"]
    assert result["pa_trade_plan"]["action"] in {"READY", "WATCH"}


def test_price_action_detects_bear_l2_as_avoid_context():
    closes = [20 - i * 0.18 for i in range(35)]
    closes += [13.7, 14.1, 13.9, 14.2, 13.6]
    df = _ohlc_from_closes(closes)
    df.loc[df.index[-1], "开盘"] = 14.05
    df.loc[df.index[-1], "收盘"] = 13.45
    df.loc[df.index[-1], "最高"] = 14.1
    df.loc[df.index[-1], "最低"] = 13.35

    result = analyze_price_action(df)

    assert result["pa_pullback_structure"] == "双腿回调"
    assert result["pa_pullback_legs"] >= 2
    assert "L2" in result["pa_tags"] or result["price_action_pattern"] == "L2二次做空信号"
    assert result["pa_failure_risk"] >= 45
    assert result["pa_trade_plan"]["action"] == "AVOID"


def test_price_action_classifies_range_failed_breakout_type_and_trap_risk():
    closes = [10, 10.2, 10.05, 10.25, 10.1, 10.3, 10.15, 10.28] * 4
    df = _ohlc_from_closes(closes)
    df.loc[df.index[-2], "开盘"] = 10.25
    df.loc[df.index[-2], "最高"] = 11.3
    df.loc[df.index[-2], "最低"] = 10.2
    df.loc[df.index[-2], "收盘"] = 10.95
    df.loc[df.index[-1], "开盘"] = 10.9
    df.loc[df.index[-1], "最高"] = 10.95
    df.loc[df.index[-1], "最低"] = 10.05
    df.loc[df.index[-1], "收盘"] = 10.2

    result = analyze_price_action(df)

    assert result["price_action_pattern"] == "交易区间假突破"
    assert result["pa_failed_breakout_type"] == "上沿突破失败"
    assert result["pa_range_rule"] == "上沿突破失败"
    assert result["pa_trap_risk"] >= result["pa_failure_risk"]
    assert result["pa_trade_plan"]["action"] == "AVOID"


def test_price_action_detects_micro_channel_context():
    closes = [10 + i * 0.18 for i in range(45)]
    df = _ohlc_from_closes(closes)

    result = analyze_price_action(df)

    assert result["pa_micro_channel"] == "多头微型通道"
    assert result["pa_always_in_strength"] >= 50
    assert result["pa_channel_state"] == "多头微型通道延续"
    assert result["pa_position_strategy"] in {"区间上沿不追价", "顺势观察回踩入场", "强趋势持有等待首次回调"}


def test_price_action_detects_trend_damage_after_bull_context():
    closes = [10 + i * 0.18 for i in range(35)] + [16.0, 15.8, 15.5, 15.1, 14.7]
    df = _ohlc_from_closes(closes)
    df.loc[df.index[-1], "开盘"] = 15.1
    df.loc[df.index[-1], "收盘"] = 14.4
    df.loc[df.index[-1], "最高"] = 15.2
    df.loc[df.index[-1], "最低"] = 14.3

    result = analyze_price_action(df)

    assert result["pa_trend_damage"] in {"跌破EMA20", "跌破EMA60", "短线低点破坏"}
    assert result["pa_position_strategy"] == "减仓或等待二次确认"
    assert any("趋势结构" in reason for reason in result["pa_trade_plan"]["avoid_reasons"])


def test_price_action_outputs_multitimeframe_volume_range_phase_and_summary():
    closes = [10 + i * 0.1 for i in range(70)] + [17.2, 16.8, 17.4]
    df = _ohlc_from_closes(closes)
    df.loc[df.index[-1], "开盘"] = 16.9
    df.loc[df.index[-1], "收盘"] = 17.8
    df.loc[df.index[-1], "最高"] = 17.9
    df.loc[df.index[-1], "最低"] = 16.85
    df.loc[df.index[-1], "成交量"] = df["成交量"].tail(20).mean() * 2

    result = analyze_price_action(df)

    assert result["pa_weekly_context"] != "周线数据不足"
    assert isinstance(result["pa_multi_timeframe_score"], int)
    assert result["pa_multi_timeframe_note"]
    assert result["pa_volume_pattern"] in {"放量突破", "缩量回调后放量反包", "量能中性", "缩量上攻"}
    assert isinstance(result["pa_volume_confirmed"], bool)
    assert result["pa_range_width_quality"]
    assert isinstance(result["pa_range_center_risk"], int)
    assert isinstance(result["pa_range_failed_breakout_count"], int)
    assert result["pa_trend_phase"]
    assert result["pa_trend_phase_action"]
    assert result["pa_decision_summary"]


def test_price_action_detects_gap_failure_and_failed_second_entry():
    closes = [10 + i * 0.08 for i in range(45)] + [13.8, 13.4, 13.7, 13.3, 13.9, 13.1]
    df = _ohlc_from_closes(closes)
    signal_idx = df.index[-2]
    last_idx = df.index[-1]
    df.loc[signal_idx, "开盘"] = 13.35
    df.loc[signal_idx, "收盘"] = 13.95
    df.loc[signal_idx, "最高"] = 14.05
    df.loc[signal_idx, "最低"] = 13.3
    df.loc[last_idx, "开盘"] = 14.4
    df.loc[last_idx, "最高"] = 14.5
    df.loc[last_idx, "最低"] = 12.9
    df.loc[last_idx, "收盘"] = 13.0

    result = analyze_price_action(df)

    assert result["pa_gap_type"] == "高开低走缺口失败"
    assert result["pa_gap_risk"] >= 70
    assert result["pa_failed_second_entry"] in {"失败H2", None}
    if result["pa_failed_second_entry"]:
        assert result["pa_second_entry_risk"] >= 70
    assert "缺口" in result["pa_decision_summary"]


def test_price_action_annotations_include_summary_and_lines():
    closes = [10 + i * 0.08 for i in range(60)]
    df = _ohlc_from_closes(closes)

    annotations = build_price_action_annotations(df)

    assert annotations["summary"]["price_action_score"] > 0
    assert isinstance(annotations["markers"], list)
    assert any(line["kind"] in {"entry", "stop"} for line in annotations["lines"])
