"""
Al Brooks-inspired price action structure recognition.

The engine intentionally keeps the first version conservative: it identifies
context, the latest signal bar, a few high-confidence patterns, and risk
levels without making a direct trading decision.
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _local_extrema(values: pd.Series, mode: str) -> List[int]:
    points: List[int] = []
    arr = values.reset_index(drop=True)
    if len(arr) < 3:
        return points
    for i in range(1, len(arr) - 1):
        if mode == "high" and arr.iloc[i] > arr.iloc[i - 1] and arr.iloc[i] > arr.iloc[i + 1]:
            points.append(i)
        if mode == "low" and arr.iloc[i] < arr.iloc[i - 1] and arr.iloc[i] < arr.iloc[i + 1]:
            points.append(i)
    return points


def analyze_price_action(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Analyze recent OHLCV bars and return a normalized price action summary.

    Expected columns: 日期, 开盘, 最高, 最低, 收盘. EMA20/EMA60 are optional but
    improve trend context when present.
    """
    empty = {
        "price_action_score": 0,
        "price_action_regime": "数据不足",
        "price_action_signal": "暂无",
        "price_action_entry_quality": "观望",
        "price_action_summary": "K线样本不足，暂不识别价格行为结构。",
        "price_action_risks": [],
        "pa_entry_price": None,
        "pa_stop_price": None,
        "pa_target_price": None,
        "pa_risk_reward": 0,
        "pa_tags": [],
    }
    if df is None or df.empty or len(df) < 20:
        return empty

    work = df.copy().reset_index(drop=True)
    for col in ["开盘", "最高", "最低", "收盘"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["开盘", "最高", "最低", "收盘"]).reset_index(drop=True)
    if len(work) < 20:
        return empty

    if "EMA20" not in work.columns:
        work["EMA20"] = work["收盘"].ewm(span=20, adjust=False).mean()
    if "EMA60" not in work.columns:
        work["EMA60"] = work["收盘"].ewm(span=60, adjust=False).mean()

    recent = work.tail(20).copy()
    last = work.iloc[-1]
    prev = work.iloc[-2]

    bar_range = (work["最高"] - work["最低"]).replace(0, np.nan)
    body = (work["收盘"] - work["开盘"]).abs()
    body_ratio = body / bar_range
    upper_shadow = work["最高"] - work[["开盘", "收盘"]].max(axis=1)
    lower_shadow = work[["开盘", "收盘"]].min(axis=1) - work["最低"]

    last_range = max(_safe_float(last["最高"] - last["最低"]), 0.01)
    last_body_ratio = _safe_float(body_ratio.iloc[-1])
    last_close_position = _safe_float((last["收盘"] - last["最低"]) / last_range, 0.5)
    atr20 = _safe_float(bar_range.tail(20).mean(), 0.01)

    prior_high_20 = _safe_float(work["最高"].iloc[-21:-1].max()) if len(work) >= 21 else _safe_float(recent["最高"].max())
    prior_low_20 = _safe_float(work["最低"].iloc[-21:-1].min()) if len(work) >= 21 else _safe_float(recent["最低"].min())
    recent_width_pct = (recent["最高"].max() - recent["最低"].min()) / max(_safe_float(last["收盘"]), 0.01)

    overlap_count = 0
    for i in range(1, len(recent)):
        if recent["最高"].iloc[i] >= recent["最低"].iloc[i - 1] and recent["最低"].iloc[i] <= recent["最高"].iloc[i - 1]:
            overlap_count += 1
    overlap_ratio = overlap_count / max(len(recent) - 1, 1)

    ema20_now = _safe_float(last["EMA20"])
    ema60_now = _safe_float(last["EMA60"])
    ema20_prev = _safe_float(work["EMA20"].iloc[-6]) if len(work) >= 6 else ema20_now
    bull_trend_bars = int(((recent["收盘"] > recent["开盘"]) & (body_ratio.tail(20) >= 0.55)).sum())
    bear_trend_bars = int(((recent["收盘"] < recent["开盘"]) & (body_ratio.tail(20) >= 0.55)).sum())

    regime = "交易区间"
    regime_score = 10
    if last["收盘"] > ema20_now > ema60_now and ema20_now > ema20_prev and bull_trend_bars >= 6:
        regime = "多头趋势"
        regime_score = 24
    elif last["收盘"] < ema20_now < ema60_now and ema20_now < ema20_prev and bear_trend_bars >= 6:
        regime = "空头趋势"
        regime_score = 8
    elif last["收盘"] > prior_high_20:
        regime = "向上突破"
        regime_score = 22
    elif last["收盘"] < prior_low_20:
        regime = "向下破位"
        regime_score = 5
    elif overlap_ratio >= 0.58 or recent_width_pct <= 0.16:
        regime = "交易区间"
        regime_score = 14

    signal = "普通K线"
    signal_score = 8
    tags: List[str] = []
    risks: List[str] = []

    is_bull_trend_bar = last["收盘"] > last["开盘"] and last_body_ratio >= 0.55 and last_close_position >= 0.7
    is_bear_trend_bar = last["收盘"] < last["开盘"] and last_body_ratio >= 0.55 and last_close_position <= 0.3
    is_bull_reversal = (
        last["收盘"] > last["开盘"] and
        lower_shadow.iloc[-1] >= body.iloc[-1] * 1.2 and
        last_close_position >= 0.62
    )
    is_bear_reversal = (
        last["收盘"] < last["开盘"] and
        upper_shadow.iloc[-1] >= body.iloc[-1] * 1.2 and
        last_close_position <= 0.38
    )
    is_inside_bar = last["最高"] <= prev["最高"] and last["最低"] >= prev["最低"]
    is_outside_bar = last["最高"] >= prev["最高"] and last["最低"] <= prev["最低"]

    if is_bull_trend_bar:
        signal = "强多头趋势K"
        signal_score = 22
        tags.append("趋势K")
    elif is_bull_reversal:
        signal = "多头反转K"
        signal_score = 18
        tags.append("反转K")
    elif is_bear_trend_bar:
        signal = "强空头趋势K"
        signal_score = 4
        risks.append("末根K线为空头趋势K，短线主动性偏弱。")
    elif is_bear_reversal:
        signal = "空头反转K"
        signal_score = 5
        risks.append("末根K线出现上方抛压，追高质量下降。")
    elif is_inside_bar:
        signal = "内包K"
        signal_score = 12
        tags.append("等待突破")
    elif is_outside_bar:
        signal = "外包K"
        signal_score = 14
        tags.append("波动扩大")

    pattern = "无明确形态"
    pattern_score = 0
    near_ema20 = abs(_safe_float(last["收盘"]) - ema20_now) <= atr20 * 1.2
    broke_recent_high = last["收盘"] > prior_high_20 and is_bull_trend_bar

    last_10 = work.tail(10)
    had_breakout = bool((last_10["最高"].shift(1) > work["最高"].rolling(20).max().shift(2).tail(10)).fillna(False).any())
    pullback_then_bull = (
        regime in {"多头趋势", "向上突破"} and
        near_ema20 and
        is_bull_reversal and
        work["收盘"].iloc[-2] < work["收盘"].iloc[-3]
    )
    if broke_recent_high:
        pattern = "突破前高"
        pattern_score = 18
        tags.append("突破")
    elif had_breakout and near_ema20 and last["收盘"] > last["开盘"]:
        pattern = "突破回踩"
        pattern_score = 20
        tags.append("回踩确认")
    elif pullback_then_bull:
        pattern = "H2二次入场"
        pattern_score = 18
        tags.append("H2")

    high_points = _local_extrema(work["最高"].tail(35), "high")
    low_points = _local_extrema(work["最低"].tail(35), "low")
    if len(high_points) >= 3:
        highs = work["最高"].tail(35).reset_index(drop=True).iloc[high_points[-3:]]
        if highs.is_monotonic_increasing and is_bear_reversal:
            pattern = "楔形三推过冲"
            pattern_score = max(pattern_score, 10)
            risks.append("三推后出现抛压，需防突破失败。")
            tags.append("楔形")
    if len(low_points) >= 3:
        lows = work["最低"].tail(35).reset_index(drop=True).iloc[low_points[-3:]]
        if lows.is_monotonic_decreasing and is_bull_reversal:
            pattern = "楔形三推反转"
            pattern_score = max(pattern_score, 17)
            tags.append("楔形反转")

    returned_to_range = prev["最高"] > prior_high_20 and last["收盘"] < prior_high_20
    if regime == "交易区间" and returned_to_range:
        pattern = "交易区间假突破"
        pattern_score = 6
        risks.append("突破后回到区间内，先按假突破处理。")
        tags.append("假突破")

    entry_price = round(_safe_float(last["最高"]) + 0.01, 2)
    stop_anchor = min(_safe_float(last["最低"]), _safe_float(recent["最低"].tail(5).min()))
    stop_price = round(stop_anchor, 2)
    risk = max(entry_price - stop_price, 0.01)
    target_price = round(entry_price + risk * 2, 2)
    resistance = _safe_float(recent["最高"].max())
    space = max(resistance - entry_price, 0)
    rr = round(space / risk, 2) if risk > 0 else 0

    if risk / max(entry_price, 0.01) > 0.1:
        risks.append("信号K止损距离超过10%，仓位需下调。")
    if upper_shadow.iloc[-1] > body.iloc[-1] * 1.5:
        risks.append("上影线偏长，存在上方供给。")

    location_score = 10
    if regime in {"多头趋势", "向上突破"} and near_ema20:
        location_score = 18
    elif regime == "交易区间" and last["收盘"] > recent["最低"].min() + (recent["最高"].max() - recent["最低"].min()) * 0.65:
        location_score = 8
        risks.append("位于交易区间上半部，追价优势不足。")
    elif regime == "交易区间":
        location_score = 14

    score = max(0, min(100, regime_score + signal_score + pattern_score + location_score))
    if not risks and score >= 60:
        risks.append("结构较清晰，但仍需等待次日价格确认。")

    if score >= 72:
        quality = "高质量"
    elif score >= 55:
        quality = "可观察"
    elif score >= 40:
        quality = "低把握"
    else:
        quality = "观望"

    summary = f"{regime} / {pattern} / {signal}"
    return {
        "price_action_score": int(round(score)),
        "price_action_regime": regime,
        "price_action_signal": signal,
        "price_action_pattern": pattern,
        "price_action_entry_quality": quality,
        "price_action_summary": summary,
        "price_action_risks": risks[:4],
        "pa_entry_price": entry_price,
        "pa_stop_price": stop_price,
        "pa_target_price": target_price,
        "pa_risk_reward": rr,
        "pa_tags": list(dict.fromkeys(tags))[:5],
    }
