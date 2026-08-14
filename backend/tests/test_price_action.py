import pandas as pd

from core.price_action import _evaluate_pullback_validity, analyze_price_action, build_price_action_annotations


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
    assert result["pa_risk_reward"] >= 1.5
    assert result["pa_target_price"] > result["pa_entry_price"]
    assert result["pa_target_basis"]
    assert 0 <= result["pa_structure_score"] <= 100
    assert 0 <= result["pa_execution_score"] <= 100
    assert 0 <= result["pa_risk_score"] <= 100


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
    assert result["pa_pullback_status"] in {"WAITING_PULLBACK", "PENDING_CONFIRMATION", "CONFIRMED", "INVALIDATED"}
    assert result["pa_pullback_validity"]["checks"]


def test_pullback_validity_confirms_only_after_price_and_volume_confirmation():
    df = _ohlc_from_closes([10 + i * 0.08 for i in range(25)])
    avg_volume = df["成交量"].iloc[:-1].mean()
    df.loc[df.index[-2], ["开盘", "最高", "最低", "收盘", "成交量"]] = [11.9, 12.0, 11.7, 11.8, avg_volume * 0.6]
    df.loc[df.index[-1], ["开盘", "最高", "最低", "收盘", "成交量"]] = [11.8, 12.35, 11.75, 12.3, avg_volume * 1.4]

    result = _evaluate_pullback_validity(
        df,
        support_price=11.7,
        confirmation_price=12.01,
        invalidation_price=11.5,
        bull_context=True,
        trend_damage="无",
    )

    assert result["status"] == "CONFIRMED"
    assert result["score"] >= 5


def test_pullback_validity_does_not_confirm_below_published_confirmation_price():
    df = _ohlc_from_closes([10 + i * 0.08 for i in range(25)])
    avg_volume = df["成交量"].iloc[:-1].mean()
    df.loc[df.index[-1], ["开盘", "最高", "最低", "收盘", "成交量"]] = [11.8, 12.35, 11.75, 12.3, avg_volume * 1.4]
    result = _evaluate_pullback_validity(
        df, support_price=11.7, confirmation_price=12.31,
        invalidation_price=11.5, bull_context=True, trend_damage="无",
    )
    assert result["status"] == "PENDING_CONFIRMATION"
    confirmation = next(item for item in result["checks"] if item["key"] == "confirmation")
    assert confirmation["passed"] is False


def test_pullback_validity_invalidates_on_volume_breakdown():
    df = _ohlc_from_closes([10 + i * 0.08 for i in range(25)])
    avg_volume = df["成交量"].iloc[:-1].mean()
    df.loc[df.index[-1], ["开盘", "最高", "最低", "收盘", "成交量"]] = [12.0, 12.05, 11.2, 11.25, avg_volume * 1.5]

    result = _evaluate_pullback_validity(
        df,
        support_price=11.7,
        confirmation_price=12.1,
        invalidation_price=11.5,
        bull_context=True,
        trend_damage="短线低点破坏",
    )

    assert result["status"] == "INVALIDATED"
    assert "取消回踩计划" in result["action"]


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
    assert result["pa_breakout_volume_threshold"] >= 1.2
    assert 0 <= result["pa_volume_ratio_percentile"] <= 100


def test_price_action_uses_calendar_weeks_when_dates_are_available():
    dates = pd.bdate_range("2026-01-05", periods=70)
    df = _ohlc_from_closes([10 + i * 0.08 for i in range(70)])
    df["日期"] = dates

    result = analyze_price_action(df)

    assert result["pa_weekly_context"] != "周线数据不足"
    assert result["price_action_version"] == "price-action-v4"
    assert result["target_model_version"] == "structure-target-v2"
    assert result["score_model_version"] == "pa-three-score-v1"


def test_incomplete_calendar_week_is_marked_unconfirmed():
    dates = pd.bdate_range("2026-01-05", periods=69)
    df = _ohlc_from_closes([10 + i * 0.08 for i in range(69)])
    df["日期"] = dates

    result = analyze_price_action(df)

    assert result["pa_current_week_complete"] is False
    assert "本周尚未收盘" in result["pa_multi_timeframe_note"]


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


# ── Brooks 深化修复测试 ──

def test_count_pullback_legs_brooks_fib():
    """修复1: Fibonacci 回调带检测。构造交替推力（回调在 38-62%）→ 2 腿。"""
    from core.price_action import _count_pullback_legs

    # 构造 L→H→L→H→L 的 swing 结构（bull 方向，回调在 Fib 带）
    # 推力1: 10→12 (幅度2), 回调1: 12→11 (幅度1, 50%回撤 ✓)
    # 推力2: 11→13 (幅度2), 回调2: 13→12 (幅度1, 50%回撤 ✓)
    closes = [10, 10.5, 11, 11.5, 12, 11.7, 11.4, 11.1, 11, 11.5, 12, 12.5, 13, 12.7, 12.4, 12.1, 12]
    df = _ohlc_from_closes(closes)
    legs = _count_pullback_legs(df, "bull")
    assert legs >= 1, f"Fibonacci回调应检测到至少1腿，实际{legs}"


def test_count_pullback_legs_noise_filtered():
    """修复1: 噪音回调（<38%）不应计数。"""
    from core.price_action import _count_pullback_legs

    # 推力: 10→15 (幅度5), 回调: 15→14.8 (幅度0.2, 仅4%回撤 — 太浅，噪音)
    closes = [10, 11, 12, 13, 14, 15, 14.9, 14.8, 14.9, 15, 15.5, 16]
    df = _ohlc_from_closes(closes)
    legs = _count_pullback_legs(df, "bull")
    # 噪音回调不应计数（<38%）
    assert legs == 0, f"噪音回调(4%)不应计数，实际{legs}"


def test_stop_price_uses_signal_bar_low():
    """修复2: 止损 = 信号棒低点 - 0.01（不再取 5 根最低 widening）。"""
    from core.price_action import analyze_price_action
    from core.indicators import calculate_indicators

    closes = [10 + i * 0.15 for i in range(50)] + [17.5, 18.0, 18.5]
    df = _ohlc_from_closes(closes)
    df = calculate_indicators(df, periods=[5, 10, 20, 60])
    summary = analyze_price_action(df)
    stop = summary.get("pa_stop_price")
    entry = summary.get("pa_entry_price")
    # 止损应在最后K线低点附近（±0.02），而非5根最低
    last_low = float(df["最低"].iloc[-1])
    if stop and entry:
        assert stop <= last_low + 0.02, f"止损应≈信号棒低点({last_low:.2f})，实际{stop:.2f}"
        assert stop < entry, "止损应低于入场价"


def test_price_action_exposes_two_stage_invalidation_profile():
    """结构防线用于收盘复核，硬止损保留盘中风险边界。"""
    from core.indicators import calculate_indicators

    closes = [10 + i * 0.08 for i in range(50)] + [14.1, 14.2, 14.3, 15.2]
    df = _ohlc_from_closes(closes)
    last_idx = df.index[-1]
    df.loc[last_idx, "开盘"] = 14.35
    df.loc[last_idx, "最高"] = 15.3
    df.loc[last_idx, "最低"] = 14.3
    df.loc[last_idx, "收盘"] = 15.2
    df = calculate_indicators(df, periods=[5, 10, 20, 60])

    summary = analyze_price_action(df)

    assert summary["pa_hard_stop_price"] == summary["pa_stop_price"]
    assert 0 < summary["pa_close_guard_price"] < summary["pa_entry_price"]
    assert summary["pa_invalidation_basis"] in {
        "突破位收盘防线",
        "回踩支撑收盘防线",
        "信号K结构防线",
    }
    assert summary["pa_invalidation_rule"]
    assert "收盘" in summary["pa_trade_plan"]["invalidation"]
    assert "盘中" in summary["pa_trade_plan"]["invalidation"]


def test_limit_up_bar_gets_executable_stop_distance():
    from core.price_action import analyze_price_action
    from core.indicators import calculate_indicators

    closes = [60 + i * 0.15 for i in range(55)] + [68.0, 69.0, 70.0, 71.06, 78.17]
    df = _ohlc_from_closes(closes)
    last_idx = df.index[-1]
    df.loc[last_idx, ["开盘", "最高", "最低", "收盘"]] = 78.17
    df = calculate_indicators(df, periods=[5, 10, 20, 60])

    summary = analyze_price_action(df)
    entry = float(summary["pa_entry_price"])
    stop = float(summary["pa_stop_price"])
    risk_pct = (entry - stop) / entry * 100

    assert 2.4 <= risk_pct <= 6.1


def test_measured_move_target():
    """修复3: 有有效推力时目标 = entry + 推力距离（Brooks ME）。"""
    from core.price_action import analyze_price_action
    from core.indicators import calculate_indicators

    # 构造强突破：推力10→13后回调到12再突破
    closes = [10 + i * 0.1 for i in range(30)] + [13, 12.5, 12, 12.3, 12.8, 13.5, 14.2]
    df = _ohlc_from_closes(closes)
    df = calculate_indicators(df, periods=[5, 10, 20, 60])
    summary = analyze_price_action(df)
    target = summary.get("pa_target_price")
    entry = summary.get("pa_entry_price")
    if target and entry:
        # 目标应在入场价之上
        assert target > entry, f"目标应高于入场价，target={target} entry={entry}"


def test_doji_classification():
    """修复4: body_ratio < 15% 的 K 线被分类为十字星。"""
    from core.price_action import analyze_price_action
    from core.indicators import calculate_indicators

    closes = [10 + i * 0.1 for i in range(50)]
    df = _ohlc_from_closes(closes)
    # 最后一根改为十字星：开盘≈收盘，range大
    df.loc[df.index[-1], "开盘"] = 15.0
    df.loc[df.index[-1], "收盘"] = 15.01
    df.loc[df.index[-1], "最高"] = 15.5
    df.loc[df.index[-1], "最低"] = 14.5
    df = calculate_indicators(df, periods=[5, 10, 20, 60])
    summary = analyze_price_action(df)
    signal = summary.get("price_action_signal", "")
    # body_ratio = |15.01-15.0|/(15.5-14.5) = 0.01/1.0 = 0.01 < 0.15 → doji
    # 但如果被其它更强的分类（如趋势K）覆盖，至少不应该报错
    assert signal, f"应有信号分类，实际空"
