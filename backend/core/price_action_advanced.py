"""Point-in-time price-action structures shared by scans and charts."""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from core.risk_constants import (
    PA_CLIMAX_EXTENSION_ATR,
    PA_CLIMAX_RANGE_MULTIPLIER,
    PA_FOLLOW_THROUGH_FAILED_SCORE_DELTA,
    PA_FOLLOW_THROUGH_FAIL_ATR,
    PA_FOLLOW_THROUGH_STRONG_ATR,
    PA_FOLLOW_THROUGH_STRONG_SCORE_DELTA,
    PA_FOLLOW_THROUGH_WEAK_SCORE_DELTA,
    PA_MTR_PRIOR_MOVE_ATR,
    PA_MTR_PRIOR_MOVE_PCT,
    PA_MTR_RETEST_ATR,
    PA_SECOND_ENTRY_NOISE_PCT,
    PA_SR_ZONE_ATR,
    PA_SR_ZONE_PRICE_PCT,
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def second_entry_state(frame: pd.DataFrame, direction: str, lookback: int = 14) -> Dict[str, Any]:
    """Count Brooks H1/H2 or L1/L2 attempts; retracement depth is quality, not eligibility."""
    empty = {
        "direction": direction.upper(), "state": "NONE", "attempts": 0,
        "triggered": False, "trigger_price": None, "invalidation_price": None,
        "retracement_pct": None, "retracement_quality": "UNKNOWN",
    }
    if frame is None or len(frame) < 4 or direction not in {"bull", "bear"}:
        return empty

    bars = frame.tail(lookback + 2).reset_index(drop=True).copy()
    for column in ("开盘", "最高", "最低", "收盘"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars = bars.dropna(subset=["最高", "最低", "收盘"]).reset_index(drop=True)
    if len(bars) < 4:
        return empty

    prior = bars.iloc[:-1]
    last = bars.iloc[-1]
    tick = max(0.01, _number(last["收盘"]) * PA_SECOND_ENTRY_NOISE_PCT)
    if direction == "bull":
        anchor = int(prior["最高"].idxmax())
        anchor_price = _number(prior.loc[anchor, "最高"])
        pre_anchor_low = _number(bars.loc[:anchor, "最低"].min())
        impulse = max(anchor_price - pre_anchor_low, tick)
        depth = max(0.0, anchor_price - _number(bars.loc[anchor + 1:, "最低"].min()))
        reset = False
        attempts = 0
        for index in range(anchor + 1, len(bars)):
            current, previous = bars.iloc[index], bars.iloc[index - 1]
            if _number(current["最高"]) <= _number(previous["最高"]) + tick * 0.25:
                reset = True
            elif reset and _number(current["最高"]) > _number(previous["最高"]) + tick * 0.25:
                attempts += 1
                reset = False
        triggered = attempts > 0 and _number(last["最高"]) > _number(bars.iloc[-2]["最高"]) + tick * 0.25
        trigger_price = _number(bars.iloc[-2]["最高"]) + tick
        invalidation = _number(last["最低"]) - tick
    else:
        anchor = int(prior["最低"].idxmin())
        anchor_price = _number(prior.loc[anchor, "最低"])
        pre_anchor_high = _number(bars.loc[:anchor, "最高"].max())
        impulse = max(pre_anchor_high - anchor_price, tick)
        depth = max(0.0, _number(bars.loc[anchor + 1:, "最高"].max()) - anchor_price)
        reset = False
        attempts = 0
        for index in range(anchor + 1, len(bars)):
            current, previous = bars.iloc[index], bars.iloc[index - 1]
            if _number(current["最低"]) >= _number(previous["最低"]) - tick * 0.25:
                reset = True
            elif reset and _number(current["最低"]) < _number(previous["最低"]) - tick * 0.25:
                attempts += 1
                reset = False
        triggered = attempts > 0 and _number(last["最低"]) < _number(bars.iloc[-2]["最低"]) - tick * 0.25
        trigger_price = _number(bars.iloc[-2]["最低"]) - tick
        invalidation = _number(last["最高"]) + tick

    retracement_pct = depth / impulse * 100
    if retracement_pct < 20:
        retracement_quality = "SHALLOW"
    elif retracement_pct <= 62:
        retracement_quality = "BALANCED"
    else:
        retracement_quality = "DEEP"
    prefix = "H" if direction == "bull" else "L"
    attempt_label = min(attempts, 3)
    if attempts >= 1:
        state = f"{prefix}{attempt_label}_TRIGGERED" if triggered else f"{prefix}{attempt_label}_ARMED"
    else:
        state = "NONE"
    return {
        "direction": direction.upper(), "state": state, "attempts": min(attempts, 3),
        "triggered": triggered, "trigger_price": round(trigger_price, 2),
        "invalidation_price": round(invalidation, 2),
        "retracement_pct": round(retracement_pct, 1),
        "retracement_quality": retracement_quality,
    }


def classify_gap(frame: pd.DataFrame) -> Dict[str, Any]:
    """Classify full, opening-only and body gaps without using future bars."""
    empty = {
        "type": "NONE", "label": "无缺口", "direction": "NONE", "filled": False,
        "fill_pct": 0.0, "opening_behavior": "无缺口", "lower_edge": None,
        "upper_edge": None, "risk": 0,
    }
    if frame is None or len(frame) < 2:
        return empty
    previous, current = frame.iloc[-2], frame.iloc[-1]
    po, ph, pl, pc = (_number(previous[key]) for key in ("开盘", "最高", "最低", "收盘"))
    co, ch, cl, cc = (_number(current[key]) for key in ("开盘", "最高", "最低", "收盘"))
    previous_body_high, previous_body_low = max(po, pc), min(po, pc)
    current_body_high, current_body_low = max(co, cc), min(co, cc)

    gap_type, direction, lower, upper = "NONE", "NONE", None, None
    if cl > ph:
        gap_type, direction, lower, upper = "FULL_UP", "UP", ph, cl
    elif ch < pl:
        gap_type, direction, lower, upper = "FULL_DOWN", "DOWN", ch, pl
    elif co > ph:
        gap_type, direction, lower, upper = "OPENING_UP", "UP", ph, co
    elif co < pl:
        gap_type, direction, lower, upper = "OPENING_DOWN", "DOWN", co, pl
    elif current_body_low > previous_body_high:
        gap_type, direction, lower, upper = "BODY_UP", "UP", previous_body_high, current_body_low
    elif current_body_high < previous_body_low:
        gap_type, direction, lower, upper = "BODY_DOWN", "DOWN", current_body_high, previous_body_low
    if gap_type == "NONE":
        return empty

    width = max(_number(upper) - _number(lower), 0.01)
    if direction == "UP":
        fill_pct = max(0.0, min(100.0, (_number(upper) - cl) / width * 100))
        filled = cl <= _number(lower)
        continuation = cc > co and cc >= cl + (ch - cl) * 0.65
    else:
        fill_pct = max(0.0, min(100.0, (ch - _number(lower)) / width * 100))
        filled = ch >= _number(upper)
        continuation = cc < co and cc <= cl + (ch - cl) * 0.35
    behavior = "缺口延续" if continuation and not filled else "缺口回补" if filled else "缺口保留待确认"
    labels = {
        "FULL_UP": "向上完全缺口", "FULL_DOWN": "向下完全缺口",
        "OPENING_UP": "向上开盘缺口", "OPENING_DOWN": "向下开盘缺口",
        "BODY_UP": "向上实体缺口", "BODY_DOWN": "向下实体缺口",
    }
    risk = 20 if direction == "UP" and continuation else 75 if direction == "UP" and filled else 80 if direction == "DOWN" and continuation else 40
    return {
        "type": gap_type, "label": labels[gap_type], "direction": direction,
        "filled": filled, "fill_pct": round(fill_pct, 1), "opening_behavior": behavior,
        "lower_edge": round(_number(lower), 2), "upper_edge": round(_number(upper), 2), "risk": risk,
    }


def breakout_follow_through(frame: pd.DataFrame, lookback: int = 20) -> Dict[str, Any]:
    """Evaluate the first two completed bars after a price breakout."""
    empty = {"state": "NONE", "direction": "NONE", "breakout_index": None, "level": None, "bars_after": 0, "score_delta": 0}
    if frame is None or len(frame) < lookback + 1:
        return empty
    bars = frame.reset_index(drop=True)
    ranges = pd.to_numeric(bars["最高"], errors="coerce") - pd.to_numeric(bars["最低"], errors="coerce")
    atr = max(_number(ranges.tail(20).mean()), 0.01)
    found = None
    for index in range(max(lookback, len(bars) - 3), len(bars)):
        history = bars.iloc[index - lookback:index]
        upper = _number(history["最高"].max())
        lower = _number(history["最低"].min())
        row = bars.iloc[index]
        if _number(row["收盘"]) > upper and _number(row["收盘"]) > _number(row["开盘"]):
            found = (index, "UP", upper)
        elif _number(row["收盘"]) < lower and _number(row["收盘"]) < _number(row["开盘"]):
            found = (index, "DOWN", lower)
        if found:
            break
    if not found:
        return empty
    index, direction, level = found
    after = bars.iloc[index + 1:min(len(bars), index + 3)]
    bars_after = len(after)
    if bars_after == 0:
        state, delta = "WAITING", 0
    else:
        closes = pd.to_numeric(after["收盘"], errors="coerce")
        last_close = _number(closes.iloc[-1])
        failed = last_close < level - atr * PA_FOLLOW_THROUGH_FAIL_ATR if direction == "UP" else last_close > level + atr * PA_FOLLOW_THROUGH_FAIL_ATR
        extension = last_close - level if direction == "UP" else level - last_close
        aligned = int((after["收盘"] > after["开盘"]).sum()) if direction == "UP" else int((after["收盘"] < after["开盘"]).sum())
        if failed:
            state, delta = "FAILED", PA_FOLLOW_THROUGH_FAILED_SCORE_DELTA
        elif extension >= atr * PA_FOLLOW_THROUGH_STRONG_ATR and aligned >= 1:
            state, delta = "STRONG", PA_FOLLOW_THROUGH_STRONG_SCORE_DELTA
        else:
            state, delta = "WEAK", PA_FOLLOW_THROUGH_WEAK_SCORE_DELTA
    return {
        "state": state, "direction": direction, "breakout_index": index,
        "level": round(level, 2), "bars_after": bars_after, "score_delta": delta,
    }


def support_resistance_zones(frame: pd.DataFrame, atr: float) -> Dict[str, Any]:
    """Cluster visible swing, prior-period and moving-average levels into zones."""
    if frame is None or len(frame) < 10:
        return {"zones": [], "nearest_support": None, "nearest_resistance": None, "confluence_grade": "NONE"}
    bars = frame.reset_index(drop=True)
    close = _number(bars.iloc[-1]["收盘"])
    tolerance = max(atr * PA_SR_ZONE_ATR, close * PA_SR_ZONE_PRICE_PCT)
    candidates: List[Dict[str, Any]] = []

    def add(value: Any, source: str) -> None:
        number = _number(value)
        if number > 0:
            candidates.append({"value": number, "source": source})

    prior = bars.iloc[:-1]
    add(prior.iloc[-1]["最高"], "前一日高点")
    add(prior.iloc[-1]["最低"], "前一日低点")
    add(prior.tail(5)["最高"].max(), "前周高点")
    add(prior.tail(5)["最低"].min(), "前周低点")
    add(prior.tail(20)["最高"].max(), "20日高点")
    add(prior.tail(20)["最低"].min(), "20日低点")
    for column, label in (("EMA20", "EMA20"), ("EMA60", "EMA60")):
        if column in bars.columns:
            add(bars.iloc[-1][column], label)
    for index in range(max(1, len(bars) - 35), len(bars) - 1):
        if _number(bars.iloc[index]["最低"]) < _number(bars.iloc[index - 1]["最低"]) and _number(bars.iloc[index]["最低"]) < _number(bars.iloc[index + 1]["最低"]):
            add(bars.iloc[index]["最低"], "摆动低点")
        if _number(bars.iloc[index]["最高"]) > _number(bars.iloc[index - 1]["最高"]) and _number(bars.iloc[index]["最高"]) > _number(bars.iloc[index + 1]["最高"]):
            add(bars.iloc[index]["最高"], "摆动高点")

    zones: List[Dict[str, Any]] = []
    for item in sorted(candidates, key=lambda candidate: candidate["value"]):
        if zones and abs(item["value"] - zones[-1]["center"]) <= tolerance:
            zone = zones[-1]
            zone["values"].append(item["value"])
            zone["sources"].append(item["source"])
            zone["center"] = sum(zone["values"]) / len(zone["values"])
        else:
            zones.append({"center": item["value"], "values": [item["value"]], "sources": [item["source"]]})
    normalized = []
    for zone in zones:
        sources = list(dict.fromkeys(zone["sources"]))
        center = zone["center"]
        normalized.append({
            "type": "SUPPORT" if center <= close else "RESISTANCE", "center": round(center, 2),
            "lower": round(center - tolerance, 2), "upper": round(center + tolerance, 2),
            "strength": min(5, len(sources)), "sources": sources,
        })
    supports = [zone for zone in normalized if zone["type"] == "SUPPORT"]
    resistances = [zone for zone in normalized if zone["type"] == "RESISTANCE"]
    nearest_support = max(supports, key=lambda zone: zone["center"], default=None)
    nearest_resistance = min(resistances, key=lambda zone: zone["center"], default=None)
    strength = (nearest_support or {}).get("strength", 0)
    grade = "STRONG" if strength >= 3 else "MEDIUM" if strength == 2 else "WEAK" if strength == 1 else "NONE"
    return {"zones": normalized, "nearest_support": nearest_support, "nearest_resistance": nearest_resistance, "confluence_grade": grade}


def major_trend_reversal(frame: pd.DataFrame, atr: float) -> Dict[str, Any]:
    """Detect a conservative trend break -> extreme retest -> reversal sequence."""
    empty = {"state": "NONE", "direction": "NONE", "trendline_break": False, "retest": False, "confirmed": False}
    if frame is None or len(frame) < 30:
        return empty
    bars = frame.reset_index(drop=True).copy()
    closes = pd.to_numeric(bars["收盘"], errors="coerce")
    ema20 = closes.ewm(span=20, adjust=False).mean()
    prior = bars.iloc[-30:-6]
    recent = bars.iloc[-6:]
    prior_move = _number(prior["收盘"].iloc[-1]) - _number(prior["收盘"].iloc[0])
    threshold = max(atr * PA_MTR_PRIOR_MOVE_ATR, _number(closes.iloc[-1]) * PA_MTR_PRIOR_MOVE_PCT)
    if prior_move >= threshold:
        direction = "BEAR_REVERSAL"
        trendline_break = bool((closes.iloc[-6:] < ema20.iloc[-6:]).any())
        old_extreme = _number(prior["最高"].max())
        retest = abs(_number(recent["最高"].max()) - old_extreme) <= atr * PA_MTR_RETEST_ATR
        confirmed = trendline_break and retest and _number(closes.iloc[-1]) < _number(recent["最低"].iloc[-2])
    elif prior_move <= -threshold:
        direction = "BULL_REVERSAL"
        trendline_break = bool((closes.iloc[-6:] > ema20.iloc[-6:]).any())
        old_extreme = _number(prior["最低"].min())
        retest = abs(_number(recent["最低"].min()) - old_extreme) <= atr * PA_MTR_RETEST_ATR
        confirmed = trendline_break and retest and _number(closes.iloc[-1]) > _number(recent["最高"].iloc[-2])
    else:
        return empty
    state = "CONFIRMED" if confirmed else "RETEST" if trendline_break and retest else "TRENDLINE_BREAK" if trendline_break else "NONE"
    return {"state": state, "direction": direction, "trendline_break": trendline_break, "retest": retest, "confirmed": confirmed}


def structure_state(frame: pd.DataFrame, regime: str, follow_through: Dict[str, Any], atr: float) -> Dict[str, str]:
    if frame is None or len(frame) < 20:
        return {"state": "INSUFFICIENT", "label": "数据不足", "action": "等待更多K线"}
    bars = frame.tail(20)
    close = _number(bars.iloc[-1]["收盘"])
    ema20 = _number(bars.iloc[-1].get("EMA20"), close)
    overlap = sum(
        _number(bars.iloc[index]["最高"]) >= _number(bars.iloc[index - 1]["最低"])
        and _number(bars.iloc[index]["最低"]) <= _number(bars.iloc[index - 1]["最高"])
        for index in range(1, len(bars))
    ) / max(len(bars) - 1, 1)
    extension = abs(close - ema20) / max(atr, 0.01)
    recent_ranges = pd.to_numeric(bars["最高"], errors="coerce") - pd.to_numeric(bars["最低"], errors="coerce")
    climax = extension >= PA_CLIMAX_EXTENSION_ATR and _number(recent_ranges.iloc[-1]) >= _number(recent_ranges.iloc[:-1].median()) * PA_CLIMAX_RANGE_MULTIPLIER
    if climax:
        return {"state": "CLIMAX", "label": "高潮段", "action": "不追价，保护已有利润"}
    if follow_through.get("state") == "WAITING":
        return {"state": "BREAKOUT_MODE", "label": "突破待跟进", "action": "等待后续1至2根K线确认"}
    if regime == "交易区间" or overlap >= 0.72:
        return {"state": "TRADING_RANGE", "label": "交易区间", "action": "区间中部不交易，只看边界"}
    if regime in {"多头趋势", "空头趋势"} and overlap <= 0.42:
        return {"state": "TIGHT_CHANNEL", "label": "紧密通道", "action": "顺势持有，等待首次像样回调"}
    if regime in {"多头趋势", "空头趋势"}:
        return {"state": "BROAD_CHANNEL", "label": "宽通道", "action": "降低追价，优先在通道边缘处理"}
    return {"state": "TRANSITION", "label": "结构转换", "action": "等待突破跟进或反转确认"}
