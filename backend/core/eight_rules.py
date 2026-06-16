"""Quantified versions of the common eight volume-price trading rules."""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


RULE_META = {
    "HIGH_VOLUME_STALL": ("高位放量滞涨", "RISK", -12, "REDUCE"),
    "HIGH_VOLUME_BEAR": ("高位放量大阴", "RISK", -15, "EXIT_REVIEW"),
    "HIGH_VOLUME_BULL": ("高位放量大阳", "BULLISH", 5, "HOLD"),
    "POST_LIMIT_SMALL_BULL": ("涨停后温和小阳", "BULLISH", 3, "WATCH"),
    "HIGH_TIGHT_CONSOLIDATION": ("高位横盘不回落", "BULLISH", 4, "HOLD"),
    "VOLUME_BREAKOUT_RESISTANCE": ("放量突破压力位", "BULLISH", 8, "WATCH"),
    "VOLUME_BREAKDOWN": ("放量下跌破位", "RISK", -15, "EXIT_REVIEW"),
    "SUPPORTED_PULLBACK": ("回调支撑不破", "BULLISH", 6, "WATCH"),
}

RULE_CODES = {
    "HIGH_VOLUME_STALL": "8.1",
    "HIGH_VOLUME_BEAR": "8.2",
    "HIGH_VOLUME_BULL": "8.3",
    "POST_LIMIT_SMALL_BULL": "8.4",
    "HIGH_TIGHT_CONSOLIDATION": "8.5",
    "VOLUME_BREAKOUT_RESISTANCE": "8.6",
    "VOLUME_BREAKDOWN": "8.7",
    "SUPPORTED_PULLBACK": "8.8",
}


def _value(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _signal(rule_id: str, confidence: int, trigger: float, invalidation: float, note: str) -> Dict[str, Any]:
    label, direction, score_delta, action = RULE_META[rule_id]
    return {
        "rule_id": rule_id,
        "rule_code": RULE_CODES[rule_id],
        "label": label,
        "direction": direction,
        "confidence": int(max(0, min(100, confidence))),
        "score_delta": score_delta,
        "action": action,
        "trigger_price": round(trigger, 2) if trigger > 0 else None,
        "invalidation_price": round(invalidation, 2) if invalidation > 0 else None,
        "confirmation_required": direction == "BULLISH",
        "note": note,
    }


def detect_eight_rules(df: pd.DataFrame) -> Dict[str, Any]:
    """Detect latest-bar rule candidates without turning them into direct orders."""
    empty = {"signals": [], "primary": None, "score_delta": 0, "risk_delta": 0}
    if df is None or df.empty or len(df) < 22:
        return empty

    work = df.copy().reset_index(drop=True)
    for col in ("开盘", "最高", "最低", "收盘"):
        work[col] = pd.to_numeric(work[col], errors="coerce")
    volume_col = next((col for col in ("成交量", "vol", "volume") if col in work.columns), None)
    if volume_col is None:
        return empty
    work[volume_col] = pd.to_numeric(work[volume_col], errors="coerce")
    work = work.dropna(subset=["开盘", "最高", "最低", "收盘", volume_col]).reset_index(drop=True)
    if len(work) < 22:
        return empty

    close = work["收盘"]
    last, prev = work.iloc[-1], work.iloc[-2]
    avg_vol = _value(work[volume_col].iloc[-21:-1].mean())
    volume_ratio = _value(last[volume_col]) / max(avg_vol, 1.0)
    prev_volume_ratio = _value(prev[volume_col]) / max(_value(work[volume_col].iloc[-22:-2].mean()), 1.0)
    prev_close = max(_value(prev["收盘"]), 0.01)
    pct = (_value(last["收盘"]) - prev_close) / prev_close * 100
    prev_prev_close = max(_value(work["收盘"].iloc[-3]), 0.01)
    prev_pct = (_value(prev["收盘"]) - prev_prev_close) / prev_prev_close * 100
    bar_range = max(_value(last["最高"]) - _value(last["最低"]), 0.01)
    body_pct = (_value(last["收盘"]) - _value(last["开盘"])) / max(_value(last["开盘"]), 0.01) * 100
    close_position = (_value(last["收盘"]) - _value(last["最低"])) / bar_range
    upper_shadow_ratio = (_value(last["最高"]) - max(_value(last["开盘"]), _value(last["收盘"]))) / bar_range
    prior_high_20 = _value(work["最高"].iloc[-21:-1].max())
    prior_low_20 = _value(work["最低"].iloc[-21:-1].min())
    high_60 = _value(work["最高"].tail(60).max())
    low_60 = _value(work["最低"].tail(60).min())
    high_position = (_value(last["收盘"]) - low_60) / max(high_60 - low_60, 0.01)
    ema20 = _value(last.get("EMA20"), _value(close.ewm(span=20, adjust=False).mean().iloc[-1]))
    support = max(ema20, prior_high_20 if _value(prev["收盘"]) > prior_high_20 else 0)
    signals: List[Dict[str, Any]] = []

    if high_position >= 0.82 and volume_ratio >= 1.8 and abs(pct) <= 1.5 and upper_shadow_ratio >= 0.3:
        signals.append(_signal("HIGH_VOLUME_STALL", 82, _value(last["最高"]), _value(last["最低"]), "高位放量但收盘推进有限，需防抛压。"))
    if high_position >= 0.78 and volume_ratio >= 1.8 and pct <= -3 and close_position <= 0.3:
        signals.append(_signal("HIGH_VOLUME_BEAR", 90, _value(last["最高"]), _value(last["最低"]), "高位放量长阴且弱收盘，优先保护利润。"))
    if high_position >= 0.75 and volume_ratio >= 1.6 and body_pct >= 3 and close_position >= 0.75 and upper_shadow_ratio <= 0.2:
        signals.append(_signal("HIGH_VOLUME_BULL", 70, _value(last["最高"]), _value(last["最低"]), "强阳高位收盘，但仍需下一交易日确认延续。"))
    if prev_pct >= 9.5 and 0.3 <= pct <= 3 and _value(last[volume_col]) <= _value(prev[volume_col]) and _value(last["收盘"]) >= (_value(prev["开盘"]) + _value(prev["收盘"])) / 2:
        signals.append(_signal("POST_LIMIT_SMALL_BULL", 62, _value(last["最高"]), _value(prev["最低"]), "涨停后温和整理，突破小阳高点后才确认。"))

    recent_6 = work.tail(6)
    drawdown_6 = (_value(recent_6["收盘"].max()) - _value(recent_6["收盘"].min())) / max(_value(recent_6["收盘"].max()), 0.01) * 100
    if high_position >= 0.78 and drawdown_6 <= 5 and _value(last["收盘"]) >= ema20 and volume_ratio <= 1.1:
        signals.append(_signal("HIGH_TIGHT_CONSOLIDATION", 65, _value(recent_6["最高"].max()), min(ema20, _value(recent_6["最低"].min())), "高位窄幅整理且未破趋势线，等待突破确认。"))
    if _value(last["收盘"]) >= prior_high_20 * 1.01 and volume_ratio >= 1.5 and close_position >= 0.72:
        signals.append(_signal("VOLUME_BREAKOUT_RESISTANCE", 80, _value(last["最高"]), prior_high_20, "放量突破近20日压力，回踩不破突破位才算有效。"))
    if volume_ratio >= 1.5 and pct <= -2.5 and (_value(last["收盘"]) < prior_low_20 or _value(last["收盘"]) < ema20) and close_position <= 0.4:
        signals.append(_signal("VOLUME_BREAKDOWN", 88, ema20, _value(last["最低"]), "放量下跌并跌破关键支撑，需执行风控复核。"))

    touched_support = _value(last["最低"]) <= support * 1.01 and _value(last["收盘"]) >= support
    recovered = _value(last["收盘"]) > _value(last["开盘"]) and close_position >= 0.58
    if support > 0 and touched_support and recovered and volume_ratio <= 1.05 and prev_volume_ratio <= 1.2:
        signals.append(_signal("SUPPORTED_PULLBACK", 76, _value(last["最高"]), support * 0.985, "回调触及支撑后收回，突破反转K高点再确认。"))

    signals.sort(key=lambda item: (item["direction"] != "RISK", -item["confidence"]))
    primary = signals[0] if signals else None
    score_delta = sum(int(item["score_delta"]) for item in signals)
    risk_delta = sum(max(0, -int(item["score_delta"])) for item in signals if item["direction"] == "RISK")
    return {
        "signals": signals[:4],
        "primary": primary,
        "score_delta": max(-15, min(8, score_delta)),
        "risk_delta": min(25, risk_delta),
    }
