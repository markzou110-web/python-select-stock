"""Intraday price-action context built from already available minute bars."""

from __future__ import annotations

from typing import Any, Dict, Iterable

import pandas as pd

from core.risk_constants import PA_INTRADAY_GAP_THRESHOLD_PCT


def _minute_frame(points: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(list(points or []))
    if frame.empty or "time" not in frame.columns:
        return pd.DataFrame()
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame.get(column, 0), errors="coerce")
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).sort_values("time")
    if frame.empty:
        return frame
    latest_date = frame["time"].dt.date.max()
    return frame[frame["time"].dt.date.eq(latest_date)].reset_index(drop=True)


def _resample_sessions(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    pieces = []
    interval_minutes = max(1, int(pd.Timedelta(rule).total_seconds() // 60))
    for start, end in (("09:30", "11:30"), ("13:00", "15:00")):
        session = frame.set_index("time").between_time(start, end, inclusive="both").reset_index()
        if session.empty:
            continue
        complete_groups = max(0, len(session) // interval_minutes - 1)
        session["_bucket"] = [min(index // interval_minutes, complete_groups) for index in range(len(session))]
        aggregated = session.groupby("_bucket", sort=True).agg({
            "time": "max", "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
        }).dropna(subset=["open", "high", "low", "close"]).set_index("time")
        pieces.append(aggregated)
    return pd.concat(pieces).sort_index() if pieces else pd.DataFrame()


def _timeframe_state(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame.empty or len(frame) < 3:
        return {"direction": "UNAVAILABLE", "state": "数据不足", "bars": int(len(frame))}
    closes = frame["close"].astype(float)
    fast = closes.ewm(span=3, adjust=False).mean()
    slow = closes.ewm(span=min(8, len(frame)), adjust=False).mean()
    if closes.iloc[-1] > fast.iloc[-1] > slow.iloc[-1] and fast.iloc[-1] > fast.iloc[-2]:
        direction, state = "BULL", "多头推进"
    elif closes.iloc[-1] < fast.iloc[-1] < slow.iloc[-1] and fast.iloc[-1] < fast.iloc[-2]:
        direction, state = "BEAR", "空头推进"
    else:
        direction, state = "NEUTRAL", "区间或转换"
    return {
        "direction": direction, "state": state, "bars": int(len(frame)),
        "last_close": round(float(closes.iloc[-1]), 3),
    }


def build_intraday_price_action_context(
    daily_summary: Dict[str, Any],
    minute_points: Iterable[Dict[str, Any]],
    previous_close: float | None = None,
) -> Dict[str, Any]:
    """Return daily/60m/5m alignment and opening-range behavior; never changes live eligibility."""
    minute = _minute_frame(minute_points)
    if minute.empty:
        return {
            "state": "UNAVAILABLE", "label": "分时数据不可用", "production_effect": False,
            "daily": {}, "60m": {}, "5m": {}, "opening": {},
        }
    five = _resample_sessions(minute, "5min")
    hourly = _resample_sessions(minute, "60min")
    regime = str(daily_summary.get("price_action_regime") or "")
    daily_direction = "BULL" if regime in {"多头趋势", "向上突破"} else "BEAR" if regime in {"空头趋势", "向下破位"} else "NEUTRAL"
    daily = {"direction": daily_direction, "state": regime or "未知"}
    state_60m = _timeframe_state(hourly)
    state_5m = _timeframe_state(five)
    available = [item["direction"] for item in (daily, state_60m, state_5m) if item["direction"] not in {"UNAVAILABLE", "NEUTRAL"}]
    if len(available) >= 2 and len(set(available)) == 1:
        state, label = "ALIGNED", "日线、60分钟与5分钟方向共振"
    elif daily_direction in {"BULL", "BEAR"} and state_60m["direction"] in {"BULL", "BEAR"} and daily_direction != state_60m["direction"]:
        state, label = "CONFLICT", "日线与60分钟方向冲突"
    else:
        state, label = "MIXED", "多周期尚未形成一致方向"

    first_time = minute["time"].iloc[0]
    opening_end = first_time + pd.Timedelta(minutes=30)
    opening = minute[minute["time"].le(opening_end)]
    open_price = float(minute["open"].iloc[0])
    current = float(minute["close"].iloc[-1])
    opening_high = float(opening["high"].max())
    opening_low = float(opening["low"].min())
    prior = float(previous_close or 0)
    gap_pct = (open_price / prior - 1) * 100 if prior > 0 else 0.0
    if gap_pct >= PA_INTRADAY_GAP_THRESHOLD_PCT and current < prior:
        behavior = "GAP_UP_FAILED"
        behavior_label = "高开回补并转弱"
    elif gap_pct >= PA_INTRADAY_GAP_THRESHOLD_PCT and current >= opening_high:
        behavior = "GAP_UP_HELD"
        behavior_label = "高开守住并突破开盘区间"
    elif gap_pct <= -PA_INTRADAY_GAP_THRESHOLD_PCT and current > prior:
        behavior = "GAP_DOWN_REVERSED"
        behavior_label = "低开回补并转强"
    elif current > opening_high:
        behavior = "OPENING_RANGE_BREAK_UP"
        behavior_label = "向上突破30分钟开盘区间"
    elif current < opening_low:
        behavior = "OPENING_RANGE_BREAK_DOWN"
        behavior_label = "向下跌破30分钟开盘区间"
    else:
        behavior = "OPENING_RANGE_INSIDE"
        behavior_label = "仍在30分钟开盘区间内"
    return {
        "state": state, "label": label, "production_effect": False,
        "daily": daily, "60m": state_60m, "5m": state_5m,
        "opening": {
            "state": behavior, "label": behavior_label, "gap_pct": round(gap_pct, 2),
            "range_high": round(opening_high, 3), "range_low": round(opening_low, 3),
            "minutes_observed": int(len(opening)),
        },
    }
