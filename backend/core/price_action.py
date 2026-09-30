"""
Al Brooks-inspired price action structure recognition.

The engine intentionally keeps the first version conservative: it identifies
context, the latest signal bar, a few high-confidence patterns, and risk
levels without making a direct trading decision.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from core.eight_rules import detect_eight_rules
from core.timeframe_context import build_completed_timeframe_context, classify_mtf_relation
from core.price_action_advanced import (
    breakout_follow_through,
    classify_gap,
    major_trend_reversal,
    second_entry_state,
    structure_state,
    support_resistance_zones,
)
from core.risk_constants import (
    PA_VOLUME_BREAKOUT_BUFFER_PCT,
    PA_VOLUME_BREAKOUT_MIN_BODY_RATIO,
    PA_VOLUME_BREAKOUT_MIN_CLOSE_POSITION,
    PA_VOLUME_BREAKOUT_MIN_VOLUME_RATIO,
    PA_LONG_LOWER_WICK_MIN_REBOUND_PCT,
    PA_VOLUME_PULLBACK_CONFIRM_MAX_SESSIONS,
    PA_VOLUME_PULLBACK_CONFIRM_SCORE_DELTA,
    PA_VOLUME_PULLBACK_FORMING_SCORE_DELTA,
    PA_VOLUME_PULLBACK_INVALID_SCORE_DELTA,
    PA_VOLUME_PULLBACK_MAX_AVG_VOLUME_RATIO,
    PA_VOLUME_PULLBACK_MAX_BREAKOUT_VOLUME_RATIO,
    PA_VOLUME_PULLBACK_MAX_RHYTHM_RATIO,
    AMP_COLLAPSE_DROP_RATIO,
    AMP_COLLAPSE_NEAR_HIGH_PCT,
    AMP_SPIKE_RATIO,
    AMP_SPIKE_BASE_MAX_PCT,
    PA_VOLUME_PULLBACK_MAX_SESSIONS,
    PA_VOLUME_PULLBACK_RESISTANCE_LOOKBACK,
    PA_VOLUME_PULLBACK_SUPPORT_TOLERANCE_PCT,
    PA_VOLUME_PULLBACK_WEAK_SCORE_DELTA,
)

PRICE_ACTION_VERSION = "price-action-v9"
TARGET_MODEL_VERSION = "structure-target-v2"
SCORE_MODEL_VERSION = "pa-three-score-v5-market-structure"


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


def _count_pullback_legs(work: pd.DataFrame, direction: str, lookback: int = 12) -> int:
    """Backward-compatible attempt count for H1/H2 and L1/L2."""
    return int(second_entry_state(work, direction, lookback).get("attempts") or 0)


def _volume_series(work: pd.DataFrame) -> pd.Series:
    for col in ("成交量", "vol", "volume"):
        if col in work.columns:
            return pd.to_numeric(work[col], errors="coerce").fillna(0)
    return pd.Series([0] * len(work), index=work.index, dtype=float)


def aggregate_weekly_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily OHLCV into weekly bars dated by each week's last session."""
    if frame is None or frame.empty or "日期" not in frame.columns:
        return pd.DataFrame(columns=[] if frame is None else frame.columns)
    work = frame.copy()
    work["日期"] = pd.to_datetime(work["日期"], errors="coerce")
    work = work.dropna(subset=["日期"])
    aggregation = {
        "日期": "last",
        "开盘": "first",
        "最高": "max",
        "最低": "min",
        "收盘": "last",
    }
    aggregation.update({
        column: "sum" for column in ("成交量", "成交额", "换手率")
        if column in work.columns
    })
    work["_week"] = work["日期"].dt.to_period("W-FRI")
    return (
        work.groupby("_week", sort=True)
        .agg(aggregation)
        .reset_index(drop=True)
        .dropna(subset=["开盘", "最高", "最低", "收盘"])
        .reset_index(drop=True)
    )


def _mtr_first_pullback_rebreak(frame: pd.DataFrame, confirmed_index: int, current_index: int) -> Dict[str, float] | None:
    """Confirm the first post-bull-MTR pullback only on its closing breakout bar."""
    if not 2 <= current_index - confirmed_index <= PA_VOLUME_PULLBACK_MAX_SESSIONS:
        return None
    confirmed = frame.iloc[confirmed_index]
    current = frame.iloc[current_index]
    previous = frame.iloc[confirmed_index + 1:current_index]
    volumes = _volume_series(frame)
    confirmed_volume = _safe_float(volumes.iloc[confirmed_index])
    if confirmed_volume <= 0 or _safe_float(current["收盘"]) <= _safe_float(current["开盘"]):
        return None
    down_indices = [
        index for index in range(confirmed_index + 1, current_index)
        if _safe_float(frame["收盘"].iloc[index]) < _safe_float(frame["收盘"].iloc[index - 1])
    ]
    if not down_indices or any(
        _safe_float(volumes.iloc[index]) > confirmed_volume * PA_VOLUME_PULLBACK_MAX_AVG_VOLUME_RATIO
        for index in down_indices
    ):
        return None
    pullback = frame.iloc[down_indices[0]:current_index]
    support = _safe_float(confirmed["最低"])
    if _safe_float(previous["最低"].min()) < support or _safe_float(current["最低"]) < support:
        return None
    trigger = max(_safe_float(pullback["最高"].max()), _safe_float(confirmed["收盘"]))
    if _safe_float(current["收盘"]) <= trigger or _safe_float(volumes.iloc[current_index]) < _safe_float(volumes.iloc[down_indices].mean()):
        return None
    return {"trigger_price": round(trigger, 2), "pullback_low": round(_safe_float(pullback["最低"].min()), 2)}


def _empty_volume_pullback(reason: str = "近期没有识别到有效放量突破。") -> Dict[str, Any]:
    return {
        "status": "NONE",
        "label": "无缩量回踩结构",
        "score_delta": 0,
        "breakout_date": None,
        "breakout_price": None,
        "support_price": None,
        "box_mid_price": None,
        "depth": "NONE",
        "depth_label": "不适用",
        "breakout_volume_ratio": None,
        "pullback_volume_ratio": None,
        "pullback_avg_volume_ratio": None,
        "rhythm_ratio": None,
        "pullback_sessions": 0,
        "confirmation": "NONE",
        "confirmation_label": "等待右侧确认",
        "confirmation_date": None,
        "stop_price": None,
        "checks": [],
        "reason": reason,
    }


def _detect_breakout_volume_pullback(work: pd.DataFrame) -> Dict[str, Any]:
    """Detect a point-in-time volume pullback anchored to a real prior breakout."""
    required = {"开盘", "最高", "最低", "收盘"}
    minimum = PA_VOLUME_PULLBACK_RESISTANCE_LOOKBACK + 1
    if work is None or len(work) < minimum or not required.issubset(work.columns):
        return _empty_volume_pullback("K线样本不足，无法识别突破回踩。")

    frame = work.copy().reset_index(drop=True)
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["__volume"] = _volume_series(frame)
    frame = frame.dropna(subset=list(required)).reset_index(drop=True)
    if len(frame) < minimum or _safe_float(frame["__volume"].max()) <= 0:
        return _empty_volume_pullback("量能数据不足，无法验证缩量回踩。")

    start = max(
        PA_VOLUME_PULLBACK_RESISTANCE_LOOKBACK,
        len(frame) - PA_VOLUME_PULLBACK_MAX_SESSIONS - 1,
    )
    breakout_index = None
    breakout_context: Dict[str, float] = {}
    for index in range(start, len(frame)):
        prior = frame.iloc[index - PA_VOLUME_PULLBACK_RESISTANCE_LOOKBACK:index]
        bar = frame.iloc[index]
        resistance = _safe_float(prior["最高"].max())
        box_low = _safe_float(prior["最低"].min())
        avg_volume = _safe_float(prior["__volume"].mean())
        bar_range = max(_safe_float(bar["最高"] - bar["最低"]), 0.01)
        body_ratio = max(0.0, _safe_float(bar["收盘"] - bar["开盘"])) / bar_range
        close_position = _safe_float((bar["收盘"] - bar["最低"]) / bar_range)
        volume_ratio = _safe_float(bar["__volume"]) / max(avg_volume, 1.0)
        closes_above = _safe_float(bar["收盘"]) >= resistance * (1 + PA_VOLUME_BREAKOUT_BUFFER_PCT)
        if (
            closes_above
            and bar["收盘"] > bar["开盘"]
            and body_ratio >= PA_VOLUME_BREAKOUT_MIN_BODY_RATIO
            and close_position >= PA_VOLUME_BREAKOUT_MIN_CLOSE_POSITION
            and volume_ratio >= PA_VOLUME_BREAKOUT_MIN_VOLUME_RATIO
        ):
            breakout_index = index
            breakout_context = {
                "resistance": resistance,
                "box_low": box_low,
                "avg_volume": avg_volume,
                "volume_ratio": volume_ratio,
                "body": max(_safe_float(bar["收盘"] - bar["开盘"]), 0.01),
            }

    if breakout_index is None:
        return _empty_volume_pullback()

    breakout = frame.iloc[breakout_index]
    post = frame.iloc[breakout_index + 1:]
    resistance = breakout_context["resistance"]
    box_mid = (resistance + breakout_context["box_low"]) / 2
    breakout_date = (
        pd.to_datetime(breakout.get("日期"), errors="coerce").strftime("%Y-%m-%d")
        if "日期" in frame.columns and pd.notna(pd.to_datetime(breakout.get("日期"), errors="coerce"))
        else str(breakout_index)
    )
    base = {
        "breakout_date": breakout_date,
        "breakout_price": round(_safe_float(breakout["收盘"]), 2),
        "support_price": round(resistance, 2),
        "box_mid_price": round(box_mid, 2),
        "breakout_volume_ratio": round(breakout_context["volume_ratio"], 2),
    }
    if post.empty:
        return {
            **_empty_volume_pullback("放量突破已成立，等待回踩验证。"),
            **base,
            "status": "BREAKOUT",
            "label": "放量突破",
            "checks": [{"key": "breakout", "label": "真实放量突破", "passed": True}],
        }

    quality_post = post.head(PA_VOLUME_PULLBACK_CONFIRM_MAX_SESSIONS)
    bearish = quality_post[quality_post["收盘"] < quality_post["开盘"]]
    has_pullback = bool(
        not bearish.empty
        or _safe_float(post["最低"].min()) < _safe_float(breakout["收盘"]) * 0.99
    )
    if not has_pullback:
        return {
            **_empty_volume_pullback("突破后仍在延续，尚未出现可评估的回踩。"),
            **base,
            "status": "WAITING_PULLBACK",
            "label": "等待缩量回踩",
            "checks": [{"key": "breakout", "label": "真实放量突破", "passed": True}],
        }

    # ponytail: freeze quality at the five-bar confirmation window; a later
    # quiet bar must not retroactively turn a weak pullback into a valid one.
    pullback_bars = bearish if not bearish.empty else quality_post
    pullback_volume = _safe_float(pullback_bars["__volume"].mean())
    breakout_volume = _safe_float(breakout["__volume"])
    pullback_volume_ratio = pullback_volume / max(breakout_volume, 1.0)
    pullback_avg_volume_ratio = pullback_volume / max(breakout_context["avg_volume"], 1.0)
    shrinking = (
        pullback_volume_ratio <= PA_VOLUME_PULLBACK_MAX_BREAKOUT_VOLUME_RATIO
        and pullback_avg_volume_ratio <= PA_VOLUME_PULLBACK_MAX_AVG_VOLUME_RATIO
    )

    previous_close = _safe_float(frame["收盘"].iloc[breakout_index - 1], resistance)
    breakout_speed = max(
        (_safe_float(breakout["收盘"]) - previous_close) / max(previous_close, 0.01),
        PA_VOLUME_BREAKOUT_BUFFER_PCT,
    )
    pullback_depth_pct = max(
        0.0,
        (_safe_float(breakout["收盘"]) - _safe_float(quality_post["收盘"].min()))
        / max(_safe_float(breakout["收盘"]), 0.01),
    )
    pullback_speed = pullback_depth_pct / max(len(quality_post), 1)
    rhythm_ratio = pullback_speed / max(breakout_speed, 0.001)
    largest_bear_body = _safe_float((bearish["开盘"] - bearish["收盘"]).max()) if not bearish.empty else 0.0
    rhythm_slow = (
        rhythm_ratio <= PA_VOLUME_PULLBACK_MAX_RHYTHM_RATIO
        and largest_bear_body <= breakout_context["body"]
    )

    pullback_low = _safe_float(post["最低"].min())
    pullback_close = _safe_float(post["收盘"].min())
    if pullback_low >= resistance:
        depth, depth_label = "STRONG", "强势回踩"
    elif pullback_low >= resistance * (1 - PA_VOLUME_PULLBACK_SUPPORT_TOLERANCE_PCT):
        depth, depth_label = "STANDARD", "标准回踩"
    elif pullback_close > box_mid:
        depth, depth_label = "TOLERANCE", "容忍极限"
    else:
        depth, depth_label = "INVALID", "跌回箱体深处"

    confirmation = "NONE"
    confirmation_label = "等待右侧确认"
    confirmation_date = None
    pullback_seen = False
    confirmation_window = post.head(PA_VOLUME_PULLBACK_CONFIRM_MAX_SESSIONS)
    for post_offset, (row_index, row) in enumerate(confirmation_window.iterrows(), start=1):
        previous = frame.iloc[row_index - 1]
        pullback_seen = bool(
            pullback_seen
            or row["收盘"] < row["开盘"]
            or _safe_float(row["最低"]) < _safe_float(breakout["收盘"]) * 0.99
        )
        if not pullback_seen:
            continue

        row_range = max(_safe_float(row["最高"] - row["最低"]), 0.01)
        row_body = abs(_safe_float(row["收盘"] - row["开盘"]))
        lower_wick = min(_safe_float(row["开盘"]), _safe_float(row["收盘"])) - _safe_float(row["最低"])
        close_position = _safe_float((row["收盘"] - row["最低"]) / row_range)
        bullish_engulfing = bool(
            row["收盘"] > row["开盘"]
            and previous["收盘"] < previous["开盘"]
            and row["开盘"] <= previous["收盘"]
            and row["收盘"] >= previous["开盘"]
        )
        bullish_bar = bool(row["收盘"] > row["开盘"])
        previous_bearish = bool(previous["收盘"] < previous["开盘"])
        previous_body_mid = _safe_float(previous["收盘"] + (previous["开盘"] - previous["收盘"]) * 0.5)
        half_recovery = bool(
            bullish_bar
            and previous_bearish
            and _safe_float(row["收盘"]) >= previous_body_mid
        )
        intraday_rebound_pct = (
            (_safe_float(row["收盘"]) / max(_safe_float(row["最低"]), 0.01) - 1) * 100
        )
        long_lower_wick_rebound = bool(
            bullish_bar
            and lower_wick >= max(row_body * 1.5, row_range * 0.3)
            and intraday_rebound_pct >= PA_LONG_LOWER_WICK_MIN_REBOUND_PCT
        )
        previous_two = frame.iloc[row_index - 2] if row_index >= 2 else None
        two_day_rebound = False
        if previous_two is not None:
            prior_drop = _safe_float(previous_two["收盘"] - previous["收盘"])
            two_day_rebound = bool(
                bullish_bar
                and prior_drop > 0
                and _safe_float(row["收盘"] - previous["收盘"]) >= prior_drop * 0.5
            )
        reversal_bar = bool(
            close_position >= 0.55
            and (
                (bullish_bar and row["收盘"] >= previous["收盘"])
                or bullish_engulfing
                or lower_wick >= max(row_body * 1.5, row_range * 0.3)
            )
        )
        history = frame.iloc[:row_index + 1]
        ma10 = _safe_float(history["收盘"].tail(10).mean())
        ma20 = _safe_float(history["收盘"].tail(20).mean())
        moving_average_support = bool(
            row["收盘"] >= previous["收盘"]
            and any(
                _safe_float(row["最低"]) <= average * 1.01
                and _safe_float(row["收盘"]) >= average
                for average in (ma10, ma20)
                if average > 0
            )
        )
        earlier_post = post.iloc[:post_offset - 1]
        rebreak = bool(
            not earlier_post.empty
            and row["收盘"] > row["开盘"]
            and _safe_float(row["收盘"]) > _safe_float(earlier_post["最高"].max())
        )
        if rebreak:
            confirmation, confirmation_label = "REBREAK", "突破回踩小高点"
        elif bullish_engulfing:
            confirmation, confirmation_label = "BULLISH_ENGULFING", "阳包阴"
        elif long_lower_wick_rebound:
            confirmation, confirmation_label = "LONG_LOWER_WICK", "长下影反弹超过4%"
        elif half_recovery:
            confirmation, confirmation_label = "HALF_RECOVERY", "阳线收复前阴一半"
        elif two_day_rebound:
            confirmation, confirmation_label = "TWO_DAY_REBOUND", "两日反弹收复跌幅一半"
        elif reversal_bar:
            confirmation, confirmation_label = "REVERSAL_BAR", "反转K线企稳"
        elif moving_average_support:
            confirmation, confirmation_label = "MA_SUPPORT", "MA10/MA20共振支撑"
        else:
            continue
        confirmation_date = (
            pd.to_datetime(row.get("日期"), errors="coerce").strftime("%Y-%m-%d")
            if "日期" in frame.columns and pd.notna(pd.to_datetime(row.get("日期"), errors="coerce"))
            else str(row_index)
        )
        break

    stabilised = confirmation != "NONE"
    timed_out = len(post) > PA_VOLUME_PULLBACK_CONFIRM_MAX_SESSIONS and not stabilised
    post_ranges = (post["最高"] - post["最低"]).clip(lower=0.01)
    bearish_body_ratio = (post["开盘"] - post["收盘"]).clip(lower=0) / post_ranges
    volume_breakdown = bool((
        (post["收盘"] < post["开盘"])
        & (bearish_body_ratio >= PA_VOLUME_BREAKOUT_MIN_BODY_RATIO)
        & (
            post["__volume"]
            >= max(breakout_context["avg_volume"] * 1.3, breakout_volume * 0.9)
        )
    ).any())
    invalidated = depth == "INVALID" or volume_breakdown or timed_out

    checks = [
        {"key": "breakout", "label": "真实放量突破", "passed": True},
        {"key": "volume", "label": "回踩成交量收缩", "passed": shrinking},
        {"key": "rhythm", "label": "下跌节奏慢于突破", "passed": rhythm_slow},
        {"key": "depth", "label": "未跌回箱体深处", "passed": depth != "INVALID"},
        {"key": "selling_pressure", "label": "未出现放量长阴", "passed": not volume_breakdown},
        {"key": "time", "label": "5根K线内完成确认", "passed": not timed_out},
        {"key": "stabilisation", "label": confirmation_label, "passed": stabilised},
    ]
    if invalidated:
        status, label, score_delta = "INVALIDATED", "缩量回踩失效", PA_VOLUME_PULLBACK_INVALID_SCORE_DELTA
        if depth == "INVALID":
            reason = "回踩跌回前期箱体下半区。"
        elif volume_breakdown:
            reason = "回踩出现放量长阴，抛压未收敛。"
        else:
            reason = "回踩超过5根K线仍未确认，时间过滤失效。"
    elif shrinking and rhythm_slow and stabilised:
        status, label, score_delta = "CONFIRMED", "缩量回踩企稳", PA_VOLUME_PULLBACK_CONFIRM_SCORE_DELTA
        reason = f"放量突破后回踩缩量、节奏放缓，并出现{confirmation_label}。"
    elif shrinking and rhythm_slow:
        status, label, score_delta = "PULLBACK", "缩量回踩中", PA_VOLUME_PULLBACK_FORMING_SCORE_DELTA
        reason = "量价回踩结构有效，但尚未出现明确企稳K线。"
    else:
        status, label, score_delta = "UNQUALIFIED", "回踩质量不足", PA_VOLUME_PULLBACK_WEAK_SCORE_DELTA
        reason = "回踩量能或下跌节奏不符合有效缩量回踩标准。"
    return {
        **base,
        "status": status,
        "label": label,
        "score_delta": score_delta,
        "depth": depth,
        "depth_label": depth_label,
        "pullback_volume_ratio": round(pullback_volume_ratio, 2),
        "pullback_avg_volume_ratio": round(pullback_avg_volume_ratio, 2),
        "rhythm_ratio": round(rhythm_ratio, 2),
        "pullback_sessions": len(post),
        "confirmation": confirmation,
        "confirmation_label": confirmation_label,
        "confirmation_date": confirmation_date,
        "stop_price": round(
            min(pullback_low, resistance * (1 - PA_VOLUME_PULLBACK_SUPPORT_TOLERANCE_PCT))
            * (1 - PA_VOLUME_BREAKOUT_BUFFER_PCT),
            2,
        ),
        "checks": checks,
        "reason": reason,
    }


def _trend_path_quality(work: pd.DataFrame, lookback: int = 20) -> Dict[str, Any]:
    """Measure whether a recent trend developed gradually or through a few jumps.

    The information-discreteness measure follows the sign-frequency proxy used
    in the frog-in-the-pan momentum literature.  All inputs end at the current
    bar, so the result is safe for point-in-time replay.
    """
    if work is None or "收盘" not in work.columns:
        return {
            "quality": "INSUFFICIENT_DATA",
            "information_discreteness": 0.0,
            "efficiency": 0.0,
            "top_day_contribution": 0.0,
            "net_return": 0.0,
            "nonlinear_strength": 0.0,
            "score_delta": 0,
        }

    closes = pd.to_numeric(work["收盘"], errors="coerce").dropna().tail(lookback + 1)
    if len(closes) < max(10, lookback // 2 + 1):
        return {
            "quality": "INSUFFICIENT_DATA",
            "information_discreteness": 0.0,
            "efficiency": 0.0,
            "top_day_contribution": 0.0,
            "net_return": 0.0,
            "nonlinear_strength": 0.0,
            "score_delta": 0,
        }

    returns = closes.pct_change().dropna()
    net_return = _safe_float(closes.iloc[-1] / max(closes.iloc[0], 0.01) - 1)
    direction = 1.0 if net_return > 0 else -1.0 if net_return < 0 else 0.0
    positive_share = _safe_float((returns > 0).mean())
    negative_share = _safe_float((returns < 0).mean())
    information_discreteness = direction * (negative_share - positive_share)

    absolute_path = _safe_float(closes.diff().abs().sum())
    efficiency = abs(_safe_float(closes.iloc[-1] - closes.iloc[0])) / max(absolute_path, 0.01)
    positive_returns = returns[returns > 0].sort_values(ascending=False)
    positive_sum = _safe_float(positive_returns.sum())
    top_day_contribution = (
        _safe_float(positive_returns.head(2).sum()) / positive_sum
        if positive_sum > 0 else 0.0
    )

    realized_path_vol = _safe_float(returns.std(ddof=0)) * np.sqrt(len(returns))
    raw_strength = net_return / max(realized_path_vol, 0.01)
    nonlinear_strength = float(np.tanh(raw_strength))

    quality = "MIXED_PATH"
    score_delta = 0
    if net_return <= 0:
        quality = "NON_POSITIVE_TREND"
    elif net_return < 0.03:
        quality = "WEAK_TREND"
    elif information_discreteness >= 0.10 or top_day_contribution >= 0.65:
        quality = "DISCRETE_JUMP"
        score_delta = -10
    elif information_discreteness <= -0.20 and efficiency >= 0.35 and top_day_contribution <= 0.45:
        quality = "SMOOTH_TREND"
        score_delta = 3

    return {
        "quality": quality,
        "information_discreteness": round(information_discreteness, 3),
        "efficiency": round(max(0.0, min(1.0, efficiency)), 3),
        "top_day_contribution": round(max(0.0, min(1.0, top_day_contribution)), 3),
        "net_return": round(net_return, 4),
        "nonlinear_strength": round(max(-1.0, min(1.0, nonlinear_strength)), 3),
        "score_delta": score_delta,
    }


def _evaluate_pullback_validity(
    work: pd.DataFrame,
    support_price: float,
    confirmation_price: float,
    invalidation_price: float,
    bull_context: bool,
    trend_damage: str,
) -> Dict[str, Any]:
    """Classify a bullish pullback using structure, volume, close strength, and confirmation."""
    last = work.iloc[-1]
    volumes = _volume_series(work)
    avg_volume_20 = _safe_float(volumes.iloc[-21:-1].mean()) if len(volumes) >= 21 else _safe_float(volumes.iloc[:-1].mean())
    last_volume = _safe_float(volumes.iloc[-1])
    recent = work.tail(6).copy()
    recent_volumes = volumes.tail(6)
    down_mask = recent["收盘"] < recent["开盘"]
    pullback_volume = _safe_float(recent_volumes[down_mask].mean()) if down_mask.any() else 0.0

    close = _safe_float(last["收盘"])
    low = _safe_float(last["最低"])
    high = _safe_float(last["最高"])
    open_price = _safe_float(last["开盘"])
    bar_range = max(high - low, 0.01)
    close_position = (close - low) / bar_range
    structure_intact = invalidation_price <= 0 or close > invalidation_price
    support_held = support_price <= 0 or close >= support_price or low >= support_price * 0.985
    pullback_shrinking = pullback_volume <= 0 or avg_volume_20 <= 0 or pullback_volume <= avg_volume_20 * 0.8
    close_strength = close > open_price and close_position >= 0.6
    price_confirmed = close >= confirmation_price and close > open_price
    volume_confirmed = avg_volume_20 > 0 and last_volume >= avg_volume_20 * 1.15
    trend_intact = bull_context and trend_damage not in {"跌破EMA20", "跌破EMA60", "短线低点破坏"}
    volume_breakdown = avg_volume_20 > 0 and last_volume >= avg_volume_20 * 1.3 and close < open_price and close_position <= 0.35

    checks = [
        {"key": "structure", "label": "结构未破", "passed": bool(structure_intact and support_held)},
        {"key": "volume", "label": "回踩缩量", "passed": bool(pullback_shrinking)},
        {"key": "close", "label": "收盘转强", "passed": bool(close_strength)},
        {"key": "confirmation", "label": "突破回踩K高点", "passed": bool(price_confirmed)},
        {"key": "confirm_volume", "label": "确认K放量", "passed": bool(volume_confirmed)},
        {"key": "trend", "label": "趋势保持", "passed": bool(trend_intact)},
    ]
    score = sum(1 for item in checks if item["passed"])

    if not structure_intact or volume_breakdown or trend_damage in {"跌破EMA60", "短线低点破坏"}:
        status = "INVALIDATED"
        label = "结构失效"
        action = f"取消回踩计划；跌破 {invalidation_price:.2f} 或放量破位不得买入。"
    elif price_confirmed and volume_confirmed and structure_intact and support_held and pullback_shrinking and close_strength and trend_intact:
        status = "CONFIRMED"
        label = "回踩确认"
        action = f"已价量确认；不高开追价时，可在 {confirmation_price:.2f} 上方小仓复核。"
    elif structure_intact and support_held and pullback_shrinking and (close_strength or trend_intact):
        status = "PENDING_CONFIRMATION"
        label = "回踩待确认"
        action = f"结构暂时有效；等待放量站上 {confirmation_price:.2f}，未确认前不买入。"
    else:
        status = "WAITING_PULLBACK"
        label = "等待回踩"
        action = f"尚未形成有效回踩；关注支撑 {support_price:.2f}，跌破 {invalidation_price:.2f} 取消计划。"

    return {
        "status": status,
        "label": label,
        "score": score,
        "checks": checks,
        "support_price": round(support_price, 2) if support_price > 0 else None,
        "confirmation_price": round(confirmation_price, 2) if confirmation_price > 0 else None,
        "invalidation_price": round(invalidation_price, 2) if invalidation_price > 0 else None,
        "pullback_volume_ratio": round(pullback_volume / avg_volume_20, 2) if pullback_volume > 0 and avg_volume_20 > 0 else None,
        "confirmation_volume_ratio": round(last_volume / avg_volume_20, 2) if last_volume > 0 and avg_volume_20 > 0 else None,
        "action": action,
    }


def _weekly_context(work: pd.DataFrame) -> Dict[str, Any]:
    if len(work) < 25:
        return {
            "context": "周线数据不足", "permission": "WAIT", "score": 0,
            "note": "日线样本不足，暂不做多周期确认。",
        }

    weekly = pd.DataFrame()
    current_week_complete = True
    if "日期" in work.columns:
        dated = work.copy()
        dated["日期"] = pd.to_datetime(dated["日期"], errors="coerce")
        dated = dated.dropna(subset=["日期"]).set_index("日期")
        if not dated.empty:
            weekly = dated.resample("W-FRI").agg({
                "开盘": "first",
                "最高": "max",
                "最低": "min",
                "收盘": "last",
            }).dropna().rename(columns={"开盘": "open", "最高": "high", "最低": "low", "收盘": "close"}).tail(14)
            last_date = dated.index[-1].date()
            today = datetime.now().date()
            current_week_complete = bool(
                last_date.weekday() == 4
                and (last_date < today or (last_date == today and datetime.now().time() >= time(15, 0)))
            )
            if not current_week_complete and not weekly.empty:
                weekly = weekly.iloc[:-1]
    if len(weekly) < 5:
        chunks = []
        for start in range(max(0, len(work) - 60), len(work), 5):
            part = work.iloc[start:start + 5]
            if len(part) < 3:
                continue
            chunks.append({
                "open": _safe_float(part["开盘"].iloc[0]),
                "high": _safe_float(part["最高"].max()),
                "low": _safe_float(part["最低"].min()),
                "close": _safe_float(part["收盘"].iloc[-1]),
            })
        fallback = pd.DataFrame(chunks)
        if not current_week_complete and not fallback.empty:
            fallback = fallback.iloc[:-1]
        if len(fallback) >= len(weekly):
            weekly = fallback
    if len(weekly) < 5:
        return {
            "context": "周线数据不足", "permission": "WAIT", "score": 0,
            "note": "周线合成样本不足，暂不做多周期确认。",
            "current_week_complete": current_week_complete,
        }

    weekly["EMA5"] = weekly["close"].ewm(span=5, adjust=False).mean()
    weekly["EMA10"] = weekly["close"].ewm(span=10, adjust=False).mean()
    last_w = weekly.iloc[-1]
    prev_w = weekly.iloc[-2]
    prior_high = _safe_float(weekly["high"].iloc[:-1].tail(8).max())
    prior_low = _safe_float(weekly["low"].iloc[:-1].tail(8).min())
    width_pct = (_safe_float(weekly["high"].tail(8).max()) - _safe_float(weekly["low"].tail(8).min())) / max(_safe_float(last_w["close"]), 0.01)

    if last_w["close"] > prior_high:
        result = {"context": "周线向上突破", "score": 20, "note": "周线突破近端压力，日线突破更容易延续。"}
    elif last_w["close"] < prior_low:
        result = {"context": "周线向下破位", "score": -25, "note": "周线跌破近端支撑，日线反弹先按弱修复处理。"}
    elif last_w["close"] > last_w["EMA5"] > last_w["EMA10"] and last_w["EMA5"] >= prev_w["EMA5"]:
        result = {"context": "周线多头", "score": 25, "note": "周线收盘位于均线之上，日线多头信号获得确认。"}
    elif last_w["close"] < last_w["EMA5"] < last_w["EMA10"] and last_w["EMA5"] <= prev_w["EMA5"]:
        result = {"context": "周线空头", "score": -25, "note": "周线结构偏空，日线做多信号需要降级。"}
    elif width_pct <= 0.18:
        result = {"context": "周线交易区间", "score": -5, "note": "周线仍在交易区间，日线信号需要看位置。"}
    else:
        result = {"context": "周线中性", "score": 0, "note": "周线方向未形成明确确认。"}
    if result["context"] in {"周线多头", "周线向上突破"}:
        result["permission"] = "ALLOW_LONG"
    elif result["context"] in {"周线空头", "周线向下破位", "周线数据不足"}:
        result["permission"] = "WAIT"
    else:
        result["permission"] = "REDUCE_SIZE"
    result["current_week_complete"] = current_week_complete
    if not current_week_complete:
        result["note"] = f"{result['note']} 本周尚未收盘，仅展示观察，不参与周线方向确认。"
    return result


def build_price_action_trade_plan(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a price-action summary into an execution-oriented trade plan.

    The plan is deliberately conservative for A-shares: it separates setups
    from orders and calls out T+1/overnight risk instead of implying intraday
    stop execution is always possible.
    """
    score = int(summary.get("price_action_score") or 0)
    regime = str(summary.get("price_action_regime") or "")
    signal = str(summary.get("price_action_signal") or "")
    pattern = str(summary.get("price_action_pattern") or "")
    quality = str(summary.get("price_action_entry_quality") or "观望")
    entry_price = _safe_float(summary.get("pa_entry_price"))
    stop_price = _safe_float(summary.get("pa_stop_price"))
    close_guard_price = _safe_float(summary.get("pa_close_guard_price"))
    hard_stop_price = _safe_float(summary.get("pa_hard_stop_price") or stop_price)
    invalidation_basis = str(summary.get("pa_invalidation_basis") or "信号K结构防线")
    target_price = _safe_float(summary.get("pa_target_price"))
    risk_reward = _safe_float(summary.get("pa_risk_reward"))
    trap_risk = int(summary.get("pa_trap_risk") or 0)
    execution_score = int(summary.get("pa_execution_score") or score)
    risk_score = int(summary.get("pa_risk_score") or max(0, 100 - trap_risk))
    h2_quality = str(summary.get("pa_h2_quality") or "不适用")
    trend_damage = str(summary.get("pa_trend_damage") or "无")
    mtf_score = int(summary.get("pa_multi_timeframe_score") or 0)
    volume_risk = str(summary.get("pa_volume_risk") or "无")
    pullback_validity = summary.get("pa_pullback_validity") or {}
    volume_pullback = summary.get("pa_volume_pullback") or {}
    volume_pullback_status = str(summary.get("pa_volume_pullback_status") or "NONE")
    risks = list(summary.get("price_action_risks") or [])

    setup_name = pattern if pattern and pattern != "无明确形态" else signal
    invalidation = "暂无明确结构失效位。"
    if close_guard_price > 0 and hard_stop_price > 0:
        invalidation = (
            f"收盘跌破 {close_guard_price:.2f}（{invalidation_basis}）先降级；"
            f"盘中触及 {hard_stop_price:.2f} 视为硬失效。"
        )
    elif stop_price > 0:
        invalidation = f"盘中触及 {stop_price:.2f}，视为结构硬失效。"

    risk_pct = 0.0
    if entry_price > 0 and stop_price > 0 and entry_price > stop_price:
        risk_pct = round((entry_price - stop_price) / entry_price * 100, 2)

    action = "WAIT"
    action_label = "等待确认"
    entry_condition = "等待下一根K线确认方向，不提前预判。"
    avoid_reasons: List[str] = []

    bearish_context = regime in {"空头趋势", "向下破位"} or "空头" in signal
    if score < 40 or bearish_context:
        action = "AVOID"
        action_label = "暂不参与"
        avoid_reasons.append("价格行为结构偏弱，先排除主动买入计划。")
    elif regime == "交易区间":
        action = "WATCH"
        action_label = "只观察不追价"
        entry_condition = "交易区间内只接受下半部反转确认，区间上半部不追突破。"
        if "假突破" in pattern:
            action = "AVOID"
            action_label = "假突破回避"
            avoid_reasons.append("突破后回到区间内，按 Brooks 假突破处理。")
        elif score >= 65:
            entry_condition = "若放量突破区间上沿并收在高位，再按突破确认观察。"
    elif "H2" in pattern or "回踩" in pattern:
        action = "READY" if score >= 55 else "WATCH"
        action_label = "回踩二次入场"
        entry_condition = f"回踩不破结构位后，突破信号K高点 {entry_price:.2f} 再触发。"
    elif "突破" in pattern or regime == "向上突破":
        action = "READY" if score >= 60 else "WATCH"
        action_label = "突破确认"
        entry_condition = f"只在价格有效站上 {entry_price:.2f} 且收盘保持强势时执行。"
    elif score >= 72 and "多头" in signal:
        action = "READY"
        action_label = "强趋势K确认"
        entry_condition = f"次日不大幅高开追价，突破 {entry_price:.2f} 后再按计划执行。"

    if risk_pct >= 10:
        avoid_reasons.append("结构止损距离超过10%，不适合满仓或重仓。")
    elif risk_pct >= 8:
        avoid_reasons.append("结构止损距离偏大，仓位需下调。")
    if risk_reward and risk_reward < 1.5:
        avoid_reasons.append("上方空间相对止损距离不足，风险收益比偏低。")
    if trap_risk >= 75:
        avoid_reasons.append("多头陷阱风险偏高，突破计划需要降级。")
        if action == "READY":
            action = "WATCH"
            action_label = "陷阱风险待确认"
    if execution_score < 50 and action == "READY":
        action = "WATCH"
        action_label = "执行条件待确认"
        avoid_reasons.append("结构存在，但当前执行分不足，等待价量确认。")
    if risk_score < 35:
        action = "AVOID"
        action_label = "风险收益不匹配"
        avoid_reasons.append("价格行为风险分过低，暂不建立主动买入计划。")
    if "H2" in pattern and h2_quality == "弱":
        avoid_reasons.append("H2质量偏弱，需等待更强信号K或回踩确认。")
    if trend_damage in {"跌破EMA60", "跌破EMA20", "短线低点破坏"}:
        avoid_reasons.append("趋势结构出现破坏，入场计划需等待修复。")
        if action == "READY":
            action = "WATCH"
            action_label = "趋势修复待确认"
    if mtf_score <= -20:
        avoid_reasons.append("多周期结构不支持日线做多，计划需降级。")
        if action == "READY":
            action = "WATCH"
            action_label = "周线确认不足"
    if volume_risk not in {"无", "", "量能中性"}:
        avoid_reasons.append(volume_risk)
    if pullback_validity.get("status") == "INVALIDATED":
        action = "AVOID"
        action_label = "回踩结构失效"
        avoid_reasons.append(str(pullback_validity.get("action") or "回踩结构已经失效。"))
    elif (
        pullback_validity.get("status") == "PENDING_CONFIRMATION"
        and action == "READY"
        and ("H2" in pattern or "回踩" in pattern)
    ):
        action = "WATCH"
        action_label = "回踩待确认"
        entry_condition = str(pullback_validity.get("action") or entry_condition)
    elif pullback_validity.get("status") == "CONFIRMED":
        entry_condition = str(pullback_validity.get("action") or entry_condition)
    if volume_pullback_status == "INVALIDATED":
        action = "AVOID"
        action_label = "突破回踩失效"
        avoid_reasons.append(str(volume_pullback.get("reason") or "突破回踩结构已经失效。"))
    elif volume_pullback_status == "PULLBACK" and action == "READY":
        action = "WATCH"
        action_label = "等待回踩右侧确认"
        entry_condition = "等待5根K线内出现反转K线、均线支撑或突破回踩小高点。"
    elif volume_pullback_status == "CONFIRMED":
        confirmation_label = str(volume_pullback.get("confirmation_label") or "右侧企稳")
        entry_condition = f"已出现{confirmation_label}；仍需按原策略入场线触发，并执行结构止损。"

    follow_through_state = str(summary.get("pa_follow_through_state") or "NONE")
    mtr_state = str(summary.get("pa_mtr_state") or "NONE")
    mtr_direction = str(summary.get("pa_mtr_direction") or "NONE")
    if follow_through_state == "FAILED":
        action = "AVOID"
        action_label = "突破跟进失败"
        avoid_reasons.append("突破后重新收回关键价位，停止追价并等待新结构。")
    elif follow_through_state == "WAITING" and action == "READY":
        action = "WATCH"
        action_label = "等待突破跟进"
        avoid_reasons.append("突破刚发生，至少观察后续1至2根K线能否延续。")
    if mtr_state == "CONFIRMED" and mtr_direction == "BEAR_REVERSAL":
        action = "AVOID"
        action_label = "主要趋势反转"
        avoid_reasons.append("顶部主要趋势反转已确认，原多头计划失效。")

    checklist = [
        "14:30后确认K线形态仍保持强势",
        "板块内有至少2只以上个股同步走强",
        "未出现长上影或放量回落",
    ]
    if summary.get("pa_sr_confluence_grade") in {"MEDIUM", "STRONG"}:
        checklist.append("入场与共振支撑区距离合理，跌破该区按结构失效处理")
    management = [
        invalidation,
        "A股T+1下，当日入场后无法日内止损，隔夜风险需提前计入仓位。",
    ]
    if target_price > 0:
        management.append(f"第一测算目标 {target_price:.2f}，到达前优先观察收盘强弱。")

    return {
        "action": action,
        "action_label": action_label,
        "setup": setup_name or "暂无明确结构",
        "quality": quality,
        "entry_condition": entry_condition,
        "invalidation": invalidation,
        "risk_pct": risk_pct,
        "risk_reward": risk_reward,
        "close_guard_price": close_guard_price or None,
        "hard_stop_price": hard_stop_price or None,
        "invalidation_basis": invalidation_basis,
        "execution_score": execution_score,
        "risk_score": risk_score,
        "position_hint": "轻仓/观察" if risk_pct >= 8 or action != "READY" else "标准仓位候选",
        "checklist": checklist,
        "management": management,
        "avoid_reasons": list(dict.fromkeys(avoid_reasons + risks))[:5],
        "pullback_validity": pullback_validity,
    }


def _amplitude_metrics(work: pd.DataFrame) -> Dict[str, Any]:
    """20/60 日均振幅(%)与两条波动率预警（《交易之路》波动率规则）。

    - 阴跌预警：amp20 骤降(<=0.6*amp60)且价格仍处高位 → 强势股资金退潮信号；
    - 变盘观察：amp20 骤增(>=1.8*amp60)且基数低 → 长期低波动股突然乱蹿。
    """
    out: Dict[str, Any] = {
        "amp20": None,
        "amp60": None,
        "pa_amp_collapse_warn": False,
        "pa_amp_spike_warn": False,
    }
    try:
        if work is None or len(work) < 20:
            return out
        if not {"最高", "最低", "收盘"}.issubset(work.columns):
            return out
        high = pd.to_numeric(work["最高"], errors="coerce")
        low = pd.to_numeric(work["最低"], errors="coerce")
        close = pd.to_numeric(work["收盘"], errors="coerce")
        amp = ((high - low) / close.where(close > 0) * 100).replace([np.inf, -np.inf], np.nan).dropna()
        if amp.empty:
            return out
        amp20 = round(float(amp.tail(20).mean()), 2)
        out["amp20"] = amp20
        if len(amp) < 60:
            return out
        amp60 = round(float(amp.tail(60).mean()), 2)
        out["amp60"] = amp60
        recent_high = float(high.tail(60).max())
        last_close = float(close.iloc[-1]) if pd.notna(close.iloc[-1]) else 0.0
        if (
            amp60 > 0
            and amp20 <= amp60 * AMP_COLLAPSE_DROP_RATIO
            and recent_high > 0
            and last_close >= recent_high * AMP_COLLAPSE_NEAR_HIGH_PCT
        ):
            out["pa_amp_collapse_warn"] = True
        if amp20 >= amp60 * AMP_SPIKE_RATIO and amp60 <= AMP_SPIKE_BASE_MAX_PCT:
            out["pa_amp_spike_warn"] = True
        return out
    except Exception:
        return out


def _daily_trend_state(work: pd.DataFrame) -> str:
    """日线（中周期）趋势状态：UP / DOWN / MIXED / UNAVAILABLE。"""
    try:
        close = pd.to_numeric(work["收盘"], errors="coerce").dropna()
        if len(close) < 60:
            return "UNAVAILABLE"
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema60 = close.ewm(span=60, adjust=False).mean()
        c, e20, e60 = float(close.iloc[-1]), float(ema20.iloc[-1]), float(ema60.iloc[-1])
        if c > e20 and e20 >= e60:
            return "UP"
        if c < e20 and e20 <= e60:
            return "DOWN"
        return "MIXED"
    except Exception:
        return "UNAVAILABLE"


def _weekly_macd_divergence_flag(work: pd.DataFrame) -> bool:
    """个股周线MACD顶背离（复用指数检测器，shadow 字段，任何异常 fail-open）。"""
    try:
        from core.market_regime import detect_weekly_macd_top_divergence
        closes = pd.Series(
            pd.to_numeric(work["收盘"], errors="coerce").values,
            index=pd.to_datetime(work["日期"], errors="coerce"),
        ).dropna()
        closes = closes[~closes.index.isna()]
        return detect_weekly_macd_top_divergence(closes)
    except Exception:
        return False


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
        "pa_market_cycle": "数据不足",
        "pa_range_location": "未知",
        "pa_entry_price": None,
        "pa_stop_price": None,
        "pa_close_guard_price": None,
        "pa_hard_stop_price": None,
        "pa_invalidation_basis": "数据不足",
        "pa_invalidation_rule": "数据不足，暂不生成失效规则。",
        "pa_target_price": None,
        "pa_risk_reward": 0,
        "pa_actual_space_rr": 0,
        "pa_target_basis": "数据不足",
        "pa_structure_score": 0,
        "pa_execution_score": 0,
        "pa_risk_score": 0,
        "pa_tags": [],
        "pa_pullback_legs": 0,
        "pa_pullback_structure": "数据不足",
        "pa_breakout_quality": "无突破",
        "pa_failure_risk": 0,
        "pa_entry_quality_score": 0,
        "pa_h2_quality": "不适用",
        "pa_h2_state": "NONE",
        "pa_l2_state": "NONE",
        "pa_second_entry_retracement_quality": "UNKNOWN",
        "pa_follow_through_state": "NONE",
        "pa_follow_through": {},
        "pa_range_rule": "数据不足",
        "pa_failed_breakout_type": None,
        "pa_trap_risk": 0,
        "pa_micro_channel": "无",
        "pa_always_in_strength": 0,
        "pa_trend_damage": "无",
        "pa_channel_state": "无明显通道",
        "pa_position_strategy": "等待更多K线",
        "pa_weekly_context": "周线数据不足",
        "pa_weekly_permission": "WAIT",
        "pa_multi_timeframe_score": 0,
        "pa_multi_timeframe_note": "日线样本不足，暂不做多周期确认。",
        "pa_current_week_complete": False,
        "pa_monthly_trend": "月线数据不足",
        "pa_monthly_state": "UNAVAILABLE",
        "pa_monthly_as_of": None,
        "pa_weekly_position": "周线位置数据不足",
        "pa_weekly_position_state": "UNAVAILABLE",
        "pa_weekly_position_as_of": None,
        "pa_weekly_pattern_signals": [],
        "pa_swing_entry_route": "WAIT",
        "pa_timeframe_shadow_only": True,
        "price_action_version": PRICE_ACTION_VERSION,
        "target_model_version": TARGET_MODEL_VERSION,
        "score_model_version": SCORE_MODEL_VERSION,
        "pa_volume_pattern": "量能不足",
        "pa_volume_confirmed": False,
        "pa_volume_pullback": _empty_volume_pullback("K线样本不足，无法识别突破回踩。"),
        "pa_volume_pullback_status": "NONE",
        "pa_volume_pullback_label": "无缩量回踩结构",
        "pa_volume_pullback_score_delta": 0,
        "pa_volume_pullback_breakout_date": None,
        "pa_volume_pullback_support_price": None,
        "pa_volume_pullback_confirmation_label": "等待右侧确认",
        "pa_volume_pullback_confirmation_date": None,
        "pa_volume_pullback_stop_price": None,
        "pa_volume_ratio": 0,
        "pa_volume_ratio_percentile": 0,
        "pa_breakout_volume_threshold": 0,
        "pa_confirmation_volume_threshold": 0,
        "pa_volume_risk": "量能数据不足",
        "pa_failed_second_entry": None,
        "pa_second_entry_risk": 0,
        "pa_gap_type": "无缺口",
        "pa_gap_type_v2": "NONE",
        "pa_gap_fill_pct": 0,
        "pa_opening_behavior": "无缺口",
        "pa_gap_edges": None,
        "pa_gap_risk": 0,
        "pa_range_width_quality": "未知",
        "pa_range_center_risk": 0,
        "pa_range_failed_breakout_count": 0,
        "pa_trend_phase": "数据不足",
        "pa_trend_phase_action": "等待更多K线",
        "amp20": None,
        "amp60": None,
        "pa_amp_collapse_warn": False,
        "pa_amp_spike_warn": False,
        "pa_daily_state": "UNAVAILABLE",
        "pa_mtf_relation": "数据不足",
        "pa_weekly_macd_divergence": False,
        "pa_structure_state": "INSUFFICIENT",
        "pa_structure_state_label": "数据不足",
        "pa_structure_state_action": "等待更多K线",
        "pa_mtr_state": "NONE",
        "pa_mtr_direction": "NONE",
        "pa_support_resistance_zones": [],
        "pa_nearest_support_zone": None,
        "pa_nearest_resistance_zone": None,
        "pa_sr_confluence_grade": "NONE",
        "pa_mtf_state": "UNAVAILABLE",
        "pa_mtf_intraday": {},
        "pa_decision_summary": "K线样本不足，暂不识别价格行为结构。",
        "pa_trend_path_quality": "INSUFFICIENT_DATA",
        "pa_information_discreteness": 0,
        "pa_trend_efficiency": 0,
        "pa_top_day_contribution": 0,
        "pa_trend_net_return": 0,
        "pa_nonlinear_trend_strength": 0,
        "pa_trend_extension_atr": 0,
        "pa_extreme_trend": False,
        "pa_path_research_score_delta": 0,
        "pa_eight_rules": [],
        "pa_eight_rule_primary": None,
        "pa_eight_rule_score_delta": 0,
        "pa_eight_rule_risk_delta": 0,
        "pa_trade_plan": build_price_action_trade_plan({
            "price_action_score": 0,
            "price_action_regime": "数据不足",
            "price_action_signal": "暂无",
            "price_action_pattern": "无明确形态",
            "price_action_entry_quality": "观望",
            "price_action_risks": [],
        }),
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
    volumes = _volume_series(work)
    volume_pullback = _detect_breakout_volume_pullback(work)

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
    last_upper_shadow_pct = (
        _safe_float(upper_shadow.iloc[-1]) / max(_safe_float(last["收盘"]), 0.01) * 100
    )
    atr20 = _safe_float(bar_range.tail(20).mean(), 0.01)
    avg_volume_20 = (
        _safe_float(volumes.iloc[-21:-1].mean())
        if len(volumes) >= 21 else _safe_float(volumes.iloc[:-1].mean())
    )
    last_volume = _safe_float(volumes.iloc[-1])
    prev_volume = _safe_float(volumes.iloc[-2])
    volume_ratio = last_volume / max(avg_volume_20, 1.0) if avg_volume_20 > 0 else 0.0
    historical_volume_ratio = (
        volumes / volumes.rolling(20).mean().shift(1).replace(0, np.nan)
    ).replace([np.inf, -np.inf], np.nan).dropna().tail(60)
    volume_ratio_percentile = (
        int(round(float((historical_volume_ratio <= volume_ratio).mean()) * 100))
        if not historical_volume_ratio.empty else 50
    )
    breakout_volume_threshold = max(
        1.2,
        min(1.8, _safe_float(historical_volume_ratio.quantile(0.75), 1.4)),
    )
    confirmation_volume_threshold = max(
        1.05,
        min(1.4, _safe_float(historical_volume_ratio.quantile(0.6), 1.15)),
    )

    prior_high_20 = _safe_float(work["最高"].iloc[-21:-1].max()) if len(work) >= 21 else _safe_float(recent["最高"].max())
    prior_low_20 = _safe_float(work["最低"].iloc[-21:-1].min()) if len(work) >= 21 else _safe_float(recent["最低"].min())
    prior_high_before_prev = _safe_float(work["最高"].iloc[-22:-2].max()) if len(work) >= 22 else prior_high_20
    prior_low_before_prev = _safe_float(work["最低"].iloc[-22:-2].min()) if len(work) >= 22 else prior_low_20
    recent_width_pct = (recent["最高"].max() - recent["最低"].min()) / max(_safe_float(last["收盘"]), 0.01)
    recent_high = _safe_float(recent["最高"].max())
    recent_low = _safe_float(recent["最低"].min())
    range_height = max(recent_high - recent_low, 0.01)
    range_position = (_safe_float(last["收盘"]) - recent_low) / range_height
    if range_position >= 0.67:
        range_location = "区间上沿"
    elif range_position <= 0.33:
        range_location = "区间下沿"
    else:
        range_location = "区间中部"

    overlap_count = 0
    for i in range(1, len(recent)):
        if recent["最高"].iloc[i] >= recent["最低"].iloc[i - 1] and recent["最低"].iloc[i] <= recent["最高"].iloc[i - 1]:
            overlap_count += 1
    overlap_ratio = overlap_count / max(len(recent) - 1, 1)

    ema20_now = _safe_float(last["EMA20"])
    ema60_now = _safe_float(last["EMA60"])
    ema20_prev = _safe_float(work["EMA20"].iloc[-6]) if len(work) >= 6 else ema20_now
    trend_path = _trend_path_quality(work)
    trend_extension_atr = max(
        0.0,
        (_safe_float(last["收盘"]) - ema20_now)
        / max(atr20, _safe_float(last["收盘"]) * 0.005, 0.01),
    )
    extreme_trend = bool(
        trend_path["net_return"] >= 0.12
        and trend_path["nonlinear_strength"] >= 0.75
        and trend_extension_atr >= 2.5
    )
    path_research_score_delta = int(trend_path["score_delta"])
    if extreme_trend:
        path_research_score_delta = min(path_research_score_delta, -5)
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

    market_cycle = regime
    if regime == "多头趋势" and bull_trend_bars >= 9 and overlap_ratio < 0.45:
        market_cycle = "Always In Long"
    elif regime == "空头趋势" and bear_trend_bars >= 9 and overlap_ratio < 0.45:
        market_cycle = "Always In Short"
    elif regime == "交易区间":
        market_cycle = f"交易区间-{range_location}"

    weekly = _weekly_context(work)
    weekly_context = weekly["context"]
    mtf_score = int(weekly["score"])
    mtf_note = str(weekly["note"])
    current_week_complete = bool(weekly.get("current_week_complete", True))
    if weekly_context in {"周线多头", "周线向上突破"} and regime in {"多头趋势", "向上突破"}:
        mtf_score += 15
        mtf_note = f"{mtf_note} 日线与周线方向共振。"
    elif weekly_context in {"周线空头", "周线向下破位"} and regime in {"多头趋势", "向上突破"}:
        mtf_score -= 15
        mtf_note = f"{mtf_note} 日线多头与周线方向冲突。"
    elif weekly_context == "周线交易区间" and range_location == "区间上沿":
        mtf_score -= 10
        mtf_note = f"{mtf_note} 且日线位于上沿，追价降权。"
    mtf_score = int(max(-50, min(50, mtf_score)))

    signal = "普通K线"
    signal_score = 8
    tags: List[str] = []
    risks: List[str] = []
    # 修复4: Brooks Doji（十字星）分类。body < 15% of range = 十字星。
    # Brooks: 不用 doji 做信号棒（缺乏方向性）。
    is_doji = last_body_ratio < 0.15 and (last["最高"] - last["最低"]) > 0
    # 修复4: Doji 十字星降级（Brooks: 弱K，不用作信号棒）
    if is_doji:
        signal = "十字星（弱K）"
        signal_score = 4
        risks.append("信号棒为十字星，Brooks不建议用作入场信号")

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
    is_micro_double_bottom = abs(_safe_float(last["最低"]) - _safe_float(prev["最低"])) <= atr20 * 0.35 and last["收盘"] > last["开盘"]
    is_micro_double_top = abs(_safe_float(last["最高"]) - _safe_float(prev["最高"])) <= atr20 * 0.35 and last["收盘"] < last["开盘"]
    breaks_prev_high = last["最高"] > prev["最高"] and last["收盘"] > prev["收盘"]
    breaks_prev_low = last["最低"] < prev["最低"] and last["收盘"] < prev["收盘"]
    bull_second_entry = second_entry_state(work, "bull")
    bear_second_entry = second_entry_state(work, "bear")
    bull_pullback_legs = int(bull_second_entry.get("attempts") or 0)
    bear_pullback_legs = int(bear_second_entry.get("attempts") or 0)
    bull_context = regime in {"多头趋势", "向上突破"} or (last["收盘"] > ema20_now > ema60_now and ema20_now >= ema20_prev)
    bear_context = regime in {"空头趋势", "向下破位"} or (last["收盘"] < ema20_now < ema60_now and ema20_now <= ema20_prev)
    ema20_last_prev = _safe_float(work["EMA20"].iloc[-2])
    ema60_last_prev = _safe_float(work["EMA60"].iloc[-2])
    prior_bull_context = prev["收盘"] > ema20_last_prev > ema60_last_prev
    prior_bear_context = prev["收盘"] < ema20_last_prev < ema60_last_prev

    last_5 = work.tail(5)
    rising_lows = bool(last_5["最低"].is_monotonic_increasing)
    falling_highs = bool(last_5["最高"].is_monotonic_decreasing)
    bull_closes_5 = int((last_5["收盘"] > last_5["开盘"]).sum())
    bear_closes_5 = int((last_5["收盘"] < last_5["开盘"]).sum())
    micro_channel = "无"
    if rising_lows and bull_closes_5 >= 3:
        micro_channel = "多头微型通道"
        tags.append("微型通道")
    elif falling_highs and bear_closes_5 >= 3:
        micro_channel = "空头微型通道"
        tags.append("微型通道")

    always_in_strength = 0
    if bull_context:
        always_in_strength = 35
        if last["收盘"] > ema20_now > ema60_now:
            always_in_strength += 20
        if bull_trend_bars >= 8:
            always_in_strength += 20
        if overlap_ratio < 0.45:
            always_in_strength += 15
        if micro_channel == "多头微型通道":
            always_in_strength += 10
    elif bear_context:
        always_in_strength = 20
        if last["收盘"] < ema20_now < ema60_now:
            always_in_strength += 20
        if bear_trend_bars >= 8:
            always_in_strength += 20
        if overlap_ratio < 0.45:
            always_in_strength += 10
        if micro_channel == "空头微型通道":
            always_in_strength += 10
    always_in_strength = int(max(0, min(100, always_in_strength)))

    trend_damage = "无"
    if bull_context or prior_bull_context:
        if last["收盘"] < ema60_now:
            trend_damage = "跌破EMA60"
        elif last["收盘"] < ema20_now:
            trend_damage = "跌破EMA20"
        elif breaks_prev_low and last["收盘"] < prev["最低"]:
            trend_damage = "短线低点破坏"
    elif bear_context or prior_bear_context:
        if last["收盘"] > ema60_now:
            trend_damage = "站回EMA60"
        elif last["收盘"] > ema20_now:
            trend_damage = "站回EMA20"
        elif breaks_prev_high and last["收盘"] > prev["最高"]:
            trend_damage = "短线高点修复"

    if is_doji:
        pass
    elif is_bull_trend_bar:
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
    elif is_micro_double_bottom:
        signal = "微型双底反转K"
        signal_score = 16
        tags.append("微型双底")
    elif is_micro_double_top:
        signal = "微型双顶压力K"
        signal_score = 6
        risks.append("近两根K线形成微型双顶，上方供给需确认。")

    gap = classify_gap(work)
    gap_type = str(gap["label"])
    gap_risk = int(gap["risk"])
    if gap["type"] != "NONE":
        tags.append("缺口延续" if gap["opening_behavior"] == "缺口延续" else "缺口待确认")
    if gap["direction"] == "UP" and gap["filled"] and last["收盘"] < last["开盘"]:
        gap_type = "高开低走缺口失败"
        risks.append("向上缺口已回补且收弱，需防开盘陷阱。")
        tags.append("缺口失败")
    elif gap["direction"] == "DOWN" and gap["opening_behavior"] == "缺口延续":
        risks.append("向下缺口延续，短线风险升高。")
        tags.append("跳空破位")

    pattern = "无明确形态"
    pattern_score = 0
    near_ema20 = abs(_safe_float(last["收盘"]) - ema20_now) <= atr20 * 1.2
    broke_recent_high = last["收盘"] > prior_high_20 and is_bull_trend_bar
    broke_recent_low = last["收盘"] < prior_low_20 and is_bear_trend_bar
    follow_through = breakout_follow_through(work)

    last_10 = work.tail(10)
    had_breakout = bool((last_10["最高"].shift(1) > work["最高"].rolling(20).max().shift(2).tail(10)).fillna(False).any())
    pullback_then_bull = (
        regime in {"多头趋势", "向上突破"} and
        near_ema20 and
        is_bull_reversal and
        work["收盘"].iloc[-2] < work["收盘"].iloc[-3]
    )
    h1_entry = bull_context and bull_second_entry["state"] == "H1_TRIGGERED" and last["收盘"] > last["开盘"]
    h2_entry = bull_context and bull_second_entry["state"] == "H2_TRIGGERED" and last["收盘"] > last["开盘"]
    l1_entry = bear_context and bear_second_entry["state"] == "L1_TRIGGERED" and last["收盘"] < last["开盘"]
    l2_entry = bear_context and bear_second_entry["state"] == "L2_TRIGGERED" and last["收盘"] < last["开盘"]

    failed_second_entry = None
    second_entry_risk = 0
    prior_bull_second_try = (
        prev["收盘"] > prev["开盘"] and
        prev["最高"] > work["最高"].iloc[-3] and
        second_entry_state(work.iloc[:-1], "bull")["state"] == "H2_TRIGGERED"
    )
    prior_bear_second_try = (
        prev["收盘"] < prev["开盘"] and
        prev["最低"] < work["最低"].iloc[-3] and
        second_entry_state(work.iloc[:-1], "bear")["state"] == "L2_TRIGGERED"
    )
    if prior_bull_second_try and last["收盘"] < prev["最低"]:
        failed_second_entry = "失败H2"
        second_entry_risk = 80
        risks.append("H2触发后跌破信号K低点，二次入场失败。")
        tags.append("失败H2")
    elif prior_bear_second_try and last["收盘"] > prev["最高"]:
        failed_second_entry = "失败L2"
        second_entry_risk = 35
        tags.append("失败L2")

    if h2_entry:
        pattern = "H2二次入场"
        pattern_score = 18
        tags.append("H2")
    elif h1_entry:
        pattern = "H1首次入场"
        pattern_score = 14
        tags.append("H1")
    elif l2_entry:
        pattern = "L2二次做空信号"
        pattern_score = 2
        risks.append("出现 L2 空头二次入场，做多计划应回避。")
        tags.append("L2")
    elif l1_entry:
        pattern = "L1首次做空信号"
        pattern_score = 3
        risks.append("出现 L1 空头入场，短线结构偏弱。")
        tags.append("L1")
    elif broke_recent_high:
        pattern = "突破前高"
        pattern_score = 18
        tags.append("突破")
    elif broke_recent_low:
        pattern = "跌破前低"
        pattern_score = 2
        risks.append("空头破位结构，不适合主动做多。")
        tags.append("破位")
    elif (had_breakout or pullback_then_bull) and near_ema20 and last["收盘"] > last["开盘"]:
        pattern = "突破回踩"
        pattern_score = 20
        tags.append("回踩确认")

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

    returned_to_range = prev["最高"] > prior_high_before_prev and last["收盘"] < prior_high_before_prev
    downside_returned_to_range = prev["最低"] < prior_low_before_prev and last["收盘"] > prior_low_before_prev
    failed_breakout_type = None
    if regime == "交易区间" and returned_to_range:
        pattern = "交易区间假突破"
        pattern_score = 6
        risks.append("突破后回到区间内，先按假突破处理。")
        tags.append("假突破")
        failed_breakout_type = "上沿突破失败"
    elif regime == "交易区间" and downside_returned_to_range:
        failed_breakout_type = "下沿假破反弹"
        tags.append("假破反弹")

    range_rule = "趋势环境不适用"
    if regime == "交易区间":
        if failed_breakout_type == "上沿突破失败":
            range_rule = "上沿突破失败"
        elif failed_breakout_type == "下沿假破反弹":
            range_rule = "下沿假破反弹"
        elif range_location == "区间上沿":
            range_rule = "上沿不追突破"
        elif range_location == "区间下沿":
            range_rule = "下沿只看反转确认"
        else:
            range_rule = "中部无交易优势"

    pullback_legs = bull_pullback_legs if bull_context else bear_pullback_legs
    if pullback_legs == 2:
        pullback_structure = "双腿回调"
    elif pullback_legs > 2:
        pullback_structure = "复杂多腿回调"
    elif pullback_legs == 1:
        pullback_structure = "单腿回调"
    else:
        pullback_structure = "无清晰回调"

    breakout_quality = "无突破"
    if broke_recent_high or broke_recent_low:
        if last_body_ratio >= 0.6 and (last_close_position >= 0.72 or last_close_position <= 0.28):
            breakout_quality = "强突破"
        elif last_body_ratio >= 0.45:
            breakout_quality = "普通突破"
        else:
            breakout_quality = "弱突破"
    elif follow_through["state"] == "FAILED" or (had_breakout and last["收盘"] <= prior_high_20):
        failed_breakout_type = failed_breakout_type or "突破后无延续"

    failed_breakout_count = 0
    for idx in range(max(21, len(work) - 20), len(work)):
        prev_high = _safe_float(work["最高"].iloc[max(0, idx - 20):idx].max())
        if work["最高"].iloc[idx] > prev_high and work["收盘"].iloc[idx] < prev_high:
            failed_breakout_count += 1
    range_width_quality = "趋势环境不适用"
    range_center_risk = 0
    if regime == "交易区间":
        if recent_width_pct >= 0.22:
            range_width_quality = "区间过宽"
        elif recent_width_pct >= 0.10:
            range_width_quality = "区间可交易"
        else:
            range_width_quality = "区间过窄"
        range_center_risk = int(max(0, min(100, (1 - abs(range_position - 0.5) * 2) * 70)))

    volume_pattern = "量能中性"
    volume_confirmed = False
    volume_risk = "无"
    if avg_volume_20 <= 0:
        volume_pattern = "量能不足"
        volume_risk = "量能数据不足"
    elif (broke_recent_high or regime == "向上突破") and volume_ratio >= breakout_volume_threshold and last["收盘"] > last["开盘"]:
        volume_pattern = "放量突破"
        volume_confirmed = True
    elif (h2_entry or pullback_then_bull) and prev_volume < avg_volume_20 and volume_ratio >= confirmation_volume_threshold:
        volume_pattern = "缩量回调后放量反包"
        volume_confirmed = True
    elif failed_breakout_type in {"上沿突破失败", "突破后无延续"} and volume_ratio >= 1.3:
        volume_pattern = "放量失败突破"
        volume_risk = "放量突破失败，陷阱风险升高。"
    elif last["收盘"] < prior_low_20 and volume_ratio >= 1.3:
        volume_pattern = "放量破位"
        volume_risk = "放量跌破支撑，短线需防继续下探。"
    elif volume_ratio < 0.75 and (broke_recent_high or h2_entry):
        volume_pattern = "缩量上攻"
        volume_risk = "上攻量能不足，突破延续性需要确认。"

    volume_pullback_status = str(volume_pullback.get("status") or "NONE")
    if volume_pullback_status == "CONFIRMED":
        volume_pattern = "放量突破后缩量回踩企稳"
        pattern = "突破缩量回踩"
        pattern_score = max(pattern_score, 20)
        tags.extend(["缩量回踩", "回踩企稳"])
    elif volume_pullback_status == "PULLBACK":
        volume_pattern = "放量突破后缩量回踩"
        if pattern in {"无明确形态", "突破回踩"}:
            pattern = "突破缩量回踩"
        pattern_score = max(pattern_score, 16)
        tags.append("缩量回踩")
    elif volume_pullback_status == "INVALIDATED":
        volume_risk = str(volume_pullback.get("reason") or "缩量回踩结构失效。")

    failure_risk = 20
    if regime == "交易区间" and range_location == "区间上沿":
        failure_risk += 25
    if returned_to_range:
        failure_risk += 35
    if upper_shadow.iloc[-1] > body.iloc[-1] * 1.5:
        failure_risk += 20
    if breakout_quality == "弱突破":
        failure_risk += 15
    if regime in {"空头趋势", "向下破位"}:
        failure_risk += 25
    if gap_risk >= 70:
        failure_risk += 15
    if second_entry_risk >= 70:
        failure_risk += 20
    if volume_risk not in {"无", "量能数据不足"}:
        failure_risk += 10
    if volume_pullback_status == "INVALIDATED":
        failure_risk += 10
    if follow_through["state"] == "FAILED":
        failure_risk += 20
    elif follow_through["state"] == "WEAK":
        failure_risk += 8
    failure_risk = int(max(0, min(100, failure_risk)))

    entry_quality_score = 0
    h2_quality = "不适用"
    if h2_entry:
        entry_quality_score = 35
        if near_ema20:
            entry_quality_score += 20
        if is_bull_trend_bar or is_bull_reversal:
            entry_quality_score += 20
        if regime != "交易区间" or range_location != "区间上沿":
            entry_quality_score += 15
        if bull_pullback_legs == 2:
            entry_quality_score += 10
        if failed_breakout_type:
            entry_quality_score -= 20
        entry_quality_score = int(max(0, min(100, entry_quality_score)))
        if entry_quality_score >= 75:
            h2_quality = "强"
        elif entry_quality_score >= 55:
            h2_quality = "中"
        else:
            h2_quality = "弱"

    trap_risk = failure_risk
    if failed_breakout_type in {"上沿突破失败", "突破后无延续"}:
        trap_risk += 20
    if regime == "交易区间" and range_location == "区间上沿":
        trap_risk += 10
    if volume_pattern == "放量失败突破":
        trap_risk += 15
    if gap_type == "高开低走缺口失败":
        trap_risk += 15
    trap_risk = int(max(0, min(100, trap_risk)))

    entry_price = round(_safe_float(last["最高"]) + 0.01, 2)
    # 修复2: Brooks 止损 = 信号棒低点 - 1 tick（不再取 5 根最低 widening）。
    # 原逻辑 min(last_low, 5bar_low) 总是取更远者 → risk% 虚高 → 触发下游阻断。
    stop_price = round(_safe_float(last["最低"]) - 0.01, 2)
    raw_risk_pct = (entry_price - stop_price) / entry_price * 100 if entry_price > 0 else 0
    if entry_price > 0 and raw_risk_pct < 2.5:
        prior = work.iloc[:-1].tail(14)
        prior_close = work["收盘"].shift(1).iloc[:-1].tail(14)
        true_range = pd.concat([
            prior["最高"] - prior["最低"],
            (prior["最高"] - prior_close).abs(),
            (prior["最低"] - prior_close).abs(),
        ], axis=1).max(axis=1)
        typical_range = _safe_float(true_range.median(), entry_price * 0.025)
        stop_distance = min(max(entry_price * 0.025, typical_range), entry_price * 0.06)
        stop_price = round(entry_price - stop_distance, 2)
    # 兜底：若止损 >= 入场价（信号棒倒置等异常），用 -3% 紧急止损
    if stop_price >= entry_price and entry_price > 0:
        stop_price = round(entry_price * 0.97, 2)
    support_price = max(stop_price, round(ema20_now, 2)) if bull_context else stop_price
    if broke_recent_high or regime == "向上突破":
        close_guard_price = prior_high_20
        invalidation_basis = "突破位收盘防线"
    elif h2_entry or pullback_then_bull:
        close_guard_price = support_price
        invalidation_basis = "回踩支撑收盘防线"
    else:
        close_guard_price = stop_price
        invalidation_basis = "信号K结构防线"
    close_guard_price = round(min(max(close_guard_price, stop_price), entry_price - 0.01), 2)
    hard_stop_price = stop_price
    invalidation_rule = (
        f"收盘跌破{close_guard_price:.2f}先降级观察；"
        f"盘中触及{hard_stop_price:.2f}判定结构硬失效。"
    )
    pullback_validity = _evaluate_pullback_validity(
        work,
        support_price=support_price,
        confirmation_price=entry_price,
        invalidation_price=stop_price,
        bull_context=bull_context,
        trend_damage=trend_damage,
    )
    risk = max(entry_price - stop_price, 0.01)
    # 修复3: Brooks 测量移动目标。用最近推力（低点→高点距离）投影。
    # 原逻辑用 R 倍数（1.5R/2R/3R），不反映 Brooks 的不对称性（大腿1→大目标）。
    pa_measured_move = 0.0
    try:
        body_df = recent.iloc[:-1]
        sh = _local_extrema(body_df["最高"].astype(float), "high")
        sl = _local_extrema(body_df["最低"].astype(float), "low")
        if sh and sl:
            # 最近一个低点→高点的推力距离
            last_low_idx = max(i for i in sl if i < max(sh))
            following_high_idx = min(i for i in sh if i > last_low_idx)
            pa_measured_move = float(body_df["最高"].iloc[following_high_idx]) - float(body_df["最低"].iloc[last_low_idx])
    except Exception:
        pass

    if broke_recent_high or regime == "向上突破":
        # Brooks ME 优先，fallback 到 range_height/3R
        if pa_measured_move > 0:
            measured_target = entry_price + pa_measured_move
            target_basis = "测量移动(ME=腿1)"
        else:
            measured_target = entry_price + min(range_height, risk * 3)
            target_basis = "突破区间高度/3R"
    elif h2_entry or pullback_then_bull:
        measured_target = max(prior_high_20, entry_price + risk * 1.5)
        target_basis = "前高/1.5R"
    else:
        measured_target = entry_price + risk * 2
        target_basis = "结构风险2R"
    target_price = round(min(max(measured_target, entry_price + risk * 1.5), entry_price + risk * 3), 2)
    rr = round((target_price - entry_price) / risk, 2) if risk > 0 else 0
    actual_space_rr = round(max(prior_high_20 - entry_price, 0) / risk, 2) if risk > 0 else 0

    if risk / max(entry_price, 0.01) > 0.1:
        risks.append("信号K止损距离超过10%，仓位需下调。")
    if upper_shadow.iloc[-1] > body.iloc[-1] * 1.5:
        risks.append("上影线偏长，存在上方供给。")
    if failure_risk >= 70:
        risks.append("突破失败风险偏高，需等待收盘或次日确认。")
    if failed_breakout_type == "突破后无延续":
        risks.append("突破后缺少后续跟进，需防多头陷阱。")
    elif follow_through["state"] == "WAITING":
        risks.append("突破刚发生，等待后续1至2根K线确认跟进。")
    if volume_risk not in {"无", "量能数据不足"}:
        risks.append(volume_risk)
    if mtf_score <= -25:
        risks.append("周线与日线信号冲突，多周期确认不足。")
    if trend_damage in {"跌破EMA20", "短线低点破坏"}:
        risks.append("多头趋势出现首次破坏，追买需等待二次确认。")
    elif trend_damage == "跌破EMA60":
        risks.append("中期趋势结构被破坏，主动做多应降级。")
    channel_state = "无明显通道"
    if micro_channel == "多头微型通道":
        channel_state = "多头微型通道延续"
    elif micro_channel == "空头微型通道":
        channel_state = "空头微型通道延续"
    if last["最高"] > prior_high_20 + atr20 * 0.5 and upper_shadow.iloc[-1] > body.iloc[-1] * 1.2:
        channel_state = "上轨过冲回落"
        risks.append("通道上轨过冲后回落，短线有获利回吐风险。")
    elif last["最低"] < prior_low_20 - atr20 * 0.5 and lower_shadow.iloc[-1] > body.iloc[-1] * 1.2:
        channel_state = "下轨假破反弹"
        tags.append("通道假破")

    sr_context = support_resistance_zones(work, atr20)
    mtr = major_trend_reversal(work, atr20)
    structure = structure_state(work, regime, follow_through, atr20)
    if sr_context["confluence_grade"] == "STRONG":
        tags.append("支撑共振")
    if mtr["state"] == "CONFIRMED":
        if mtr["direction"] == "BEAR_REVERSAL":
            risks.append("主要趋势反转已确认，原多头结构应按失效处理。")
        else:
            tags.append("主要趋势反转")

    if mtr["state"] == "CONFIRMED" and mtr["direction"] == "BEAR_REVERSAL":
        position_strategy = "主要趋势反转确认，退出原多头计划"
    elif follow_through["state"] == "FAILED":
        position_strategy = "突破跟进失败，停止追价"
    elif trend_damage in {"跌破EMA60", "跌破EMA20", "短线低点破坏"}:
        position_strategy = "减仓或等待二次确认"
    elif regime == "交易区间" and range_location == "区间上沿":
        position_strategy = "区间上沿不追价"
    elif regime == "交易区间" and range_location == "区间下沿":
        position_strategy = "只等反转确认"
    elif always_in_strength >= 75 and micro_channel == "多头微型通道":
        position_strategy = "强趋势持有等待首次回调"
    elif always_in_strength >= 65 and bull_context:
        position_strategy = "顺势观察回踩入场"
    elif bear_context:
        position_strategy = "空头环境回避做多"
    else:
        position_strategy = "等待突破或回踩确认"

    trend_phase = "震荡观察"
    trend_phase_action = "等待区间边界信号"
    if mtr["state"] == "CONFIRMED":
        trend_phase = "主要趋势反转"
        trend_phase_action = "只按反转方向等待确认后的首次回踩"
    elif structure["state"] == "CLIMAX":
        trend_phase = "衰竭段"
        trend_phase_action = structure["action"]
    elif trend_damage in {"跌破EMA60", "跌破EMA20", "短线低点破坏"}:
        trend_phase = "趋势破坏"
        trend_phase_action = "减仓并等待修复"
    elif channel_state == "上轨过冲回落" or "楔形" in tags:
        trend_phase = "衰竭段"
        trend_phase_action = "不追高，优先保护利润"
    elif h2_entry:
        trend_phase = "二次入场"
        trend_phase_action = "只在触发价有效站上后执行"
    elif pullback_then_bull:
        trend_phase = "突破回踩"
        trend_phase_action = "等待回踩后的右侧确认"
    elif bull_context and bull_pullback_legs == 1:
        trend_phase = "首次回调"
        trend_phase_action = "等待H2或强反转K"
    elif broke_recent_high or regime == "向上突破":
        trend_phase = "初始突破"
        trend_phase_action = "观察突破质量和量能确认"
    elif always_in_strength >= 75:
        trend_phase = "加速段"
        trend_phase_action = "持有为主，等待首次像样回调"
    elif bull_context:
        trend_phase = "第一波拉升"
        trend_phase_action = "顺势观察回踩入场"
    elif bear_context:
        trend_phase = "空头趋势"
        trend_phase_action = "回避主动做多"

    location_score = 10
    if regime in {"多头趋势", "向上突破"} and near_ema20:
        location_score = 18
    elif regime == "交易区间" and last["收盘"] > recent["最低"].min() + (recent["最高"].max() - recent["最低"].min()) * 0.65:
        location_score = 8
        risks.append("位于交易区间上半部，追价优势不足。")
    elif regime == "交易区间":
        location_score = 16 if range_location == "区间下沿" and is_bull_reversal else 14

    eight_rules = detect_eight_rules(work)
    primary_rule = eight_rules.get("primary")
    if primary_rule:
        tags.append(primary_rule["label"])
        if primary_rule["direction"] == "RISK":
            risks.append(primary_rule["note"])
            failure_risk = min(100, failure_risk + int(eight_rules.get("risk_delta") or 0))
            trap_risk = min(100, trap_risk + int(eight_rules.get("risk_delta") or 0))

    structure_score = int(max(0, min(100, regime_score + signal_score + pattern_score + location_score + int(eight_rules.get("score_delta") or 0))))
    execution_score = 45
    execution_score += 15 if volume_confirmed else 0
    execution_score += max(-15, min(15, mtf_score * 0.3))
    execution_score += 15 if pullback_validity["status"] == "CONFIRMED" else 0
    execution_score -= 15 if pullback_validity["status"] == "INVALIDATED" else 0
    execution_score += int(volume_pullback.get("score_delta") or 0)
    execution_score += int(follow_through.get("score_delta") or 0)
    execution_score += 5 if h2_entry and sr_context["confluence_grade"] == "STRONG" else 0
    execution_score -= 12 if mtr["state"] == "CONFIRMED" and mtr["direction"] == "BEAR_REVERSAL" else 0
    execution_score -= 10 if risk / max(entry_price, 0.01) >= 0.1 else 0
    execution_score = int(max(0, min(100, execution_score)))
    risk_score = int(max(0, min(100, 100 - trap_risk * 0.55 - failure_risk * 0.35 - gap_risk * 0.1)))
    score = int(round(structure_score * 0.55 + execution_score * 0.25 + risk_score * 0.2))
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
    decision_parts = [summary]
    if weekly_context != "周线数据不足":
        decision_parts.append(f"{weekly_context}({mtf_score:+d})")
    if volume_pattern != "量能中性":
        decision_parts.append(volume_pattern)
    if volume_pullback_status not in {"NONE", "WAITING_PULLBACK"}:
        decision_parts.append(
            f"突破回踩：{volume_pullback.get('label')}({int(volume_pullback.get('score_delta') or 0):+d})"
        )
    if trend_phase:
        decision_parts.append(f"阶段：{trend_phase}")
    if failed_breakout_type:
        decision_parts.append(f"假突破：{failed_breakout_type}")
    if gap_type != "无缺口":
        decision_parts.append(f"缺口：{gap_type}")
    if follow_through["state"] != "NONE":
        decision_parts.append(f"突破跟进：{follow_through['state']}")
    if mtr["state"] != "NONE":
        decision_parts.append(f"主要趋势反转：{mtr['state']}")
    if structure["state"] not in {"INSUFFICIENT", "TRANSITION"}:
        decision_parts.append(f"结构：{structure['label']}")
    if trend_damage != "无":
        decision_parts.append(f"趋势破坏：{trend_damage}")
    if primary_rule:
        decision_parts.append(f"八诀：{primary_rule['label']}({primary_rule['confidence']}%)")
    if trap_risk >= 70:
        decision_parts.append(f"陷阱风险{trap_risk}%")
    decision_parts.append(position_strategy)
    decision_summary = "；".join(decision_parts)
    result = {
        "price_action_score": int(round(score)),
        "price_action_regime": regime,
        "price_action_signal": signal,
        "price_action_pattern": pattern,
        "price_action_entry_quality": quality,
        "price_action_summary": summary,
        "price_action_risks": risks[:4],
        "pa_market_cycle": market_cycle,
        "pa_range_location": range_location,
        "pa_entry_price": entry_price,
        "pa_stop_price": stop_price,
        "pa_close_guard_price": close_guard_price,
        "pa_hard_stop_price": hard_stop_price,
        "pa_invalidation_basis": invalidation_basis,
        "pa_invalidation_rule": invalidation_rule,
        "pa_target_price": target_price,
        "pa_risk_reward": rr,
        "pa_actual_space_rr": actual_space_rr,
        "pa_target_basis": target_basis,
        "pa_structure_score": structure_score,
        "pa_execution_score": execution_score,
        "pa_risk_score": risk_score,
        "pa_tags": list(dict.fromkeys(tags))[:5],
        "pa_pullback_legs": pullback_legs,
        "pa_pullback_structure": pullback_structure,
        "pa_pullback_validity": pullback_validity,
        "pa_pullback_status": pullback_validity["status"],
        "pa_pullback_status_label": pullback_validity["label"],
        "pa_pullback_support_price": pullback_validity["support_price"],
        "pa_pullback_confirmation_price": pullback_validity["confirmation_price"],
        "pa_pullback_invalidation_price": pullback_validity["invalidation_price"],
        "pa_pullback_action": pullback_validity["action"],
        "pa_breakout_quality": breakout_quality,
        "pa_failure_risk": failure_risk,
        "pa_entry_quality_score": entry_quality_score,
        "pa_h2_quality": h2_quality,
        "pa_h2_state": bull_second_entry["state"],
        "pa_l2_state": bear_second_entry["state"],
        "pa_second_entry_retracement_quality": (
            bull_second_entry["retracement_quality"] if bull_context
            else bear_second_entry["retracement_quality"]
        ),
        "pa_follow_through_state": follow_through["state"],
        "pa_follow_through": follow_through,
        "pa_range_rule": range_rule,
        "pa_failed_breakout_type": failed_breakout_type,
        "pa_trap_risk": trap_risk,
        "pa_micro_channel": micro_channel,
        "pa_always_in_strength": always_in_strength,
        "pa_trend_damage": trend_damage,
        "pa_channel_state": channel_state,
        "pa_position_strategy": position_strategy,
        "pa_weekly_context": weekly_context,
        "pa_weekly_permission": str(weekly.get("permission") or "WAIT"),
        "pa_multi_timeframe_score": mtf_score,
        "pa_multi_timeframe_note": mtf_note,
        "pa_current_week_complete": current_week_complete,
        "price_action_version": PRICE_ACTION_VERSION,
        "target_model_version": TARGET_MODEL_VERSION,
        "score_model_version": SCORE_MODEL_VERSION,
        "pa_volume_pattern": volume_pattern,
        "pa_volume_confirmed": volume_confirmed,
        "pa_volume_pullback": volume_pullback,
        "pa_volume_pullback_status": volume_pullback_status,
        "pa_volume_pullback_label": volume_pullback.get("label"),
        "pa_volume_pullback_score_delta": int(volume_pullback.get("score_delta") or 0),
        "pa_volume_pullback_breakout_date": volume_pullback.get("breakout_date"),
        "pa_volume_pullback_support_price": volume_pullback.get("support_price"),
        "pa_volume_pullback_confirmation_label": volume_pullback.get("confirmation_label"),
        "pa_volume_pullback_confirmation_date": volume_pullback.get("confirmation_date"),
        "pa_volume_pullback_stop_price": volume_pullback.get("stop_price"),
        "pa_volume_ratio": round(volume_ratio, 2),
        "pa_volume_ratio_percentile": volume_ratio_percentile,
        "pa_breakout_volume_threshold": round(breakout_volume_threshold, 2),
        "pa_confirmation_volume_threshold": round(confirmation_volume_threshold, 2),
        "pa_close_position": round(last_close_position, 2),
        "pa_upper_shadow_pct": round(last_upper_shadow_pct, 2),
        "pa_volume_risk": volume_risk,
        "pa_failed_second_entry": failed_second_entry,
        "pa_second_entry_risk": second_entry_risk,
        "pa_gap_type": gap_type,
        "pa_gap_type_v2": gap["type"],
        "pa_gap_fill_pct": gap["fill_pct"],
        "pa_opening_behavior": gap["opening_behavior"],
        "pa_gap_edges": (
            {"lower": gap["lower_edge"], "upper": gap["upper_edge"]}
            if gap["type"] != "NONE" else None
        ),
        "pa_gap_risk": gap_risk,
        "pa_range_width_quality": range_width_quality,
        "pa_range_center_risk": range_center_risk,
        "pa_range_failed_breakout_count": failed_breakout_count,
        "pa_trend_phase": trend_phase,
        "pa_trend_phase_action": trend_phase_action,
        "pa_structure_state": structure["state"],
        "pa_structure_state_label": structure["label"],
        "pa_structure_state_action": structure["action"],
        "pa_mtr_state": mtr["state"],
        "pa_mtr_direction": mtr["direction"],
        "pa_support_resistance_zones": sr_context["zones"],
        "pa_nearest_support_zone": sr_context["nearest_support"],
        "pa_nearest_resistance_zone": sr_context["nearest_resistance"],
        "pa_sr_confluence_grade": sr_context["confluence_grade"],
        "pa_mtf_state": "UNAVAILABLE",
        "pa_mtf_intraday": {},
        "pa_decision_summary": decision_summary,
        "pa_trend_path_quality": trend_path["quality"],
        "pa_information_discreteness": trend_path["information_discreteness"],
        "pa_trend_efficiency": trend_path["efficiency"],
        "pa_top_day_contribution": trend_path["top_day_contribution"],
        "pa_trend_net_return": trend_path["net_return"],
        "pa_nonlinear_trend_strength": trend_path["nonlinear_strength"],
        "pa_trend_extension_atr": round(trend_extension_atr, 2),
        "pa_extreme_trend": extreme_trend,
        "pa_path_research_score_delta": path_research_score_delta,
        "pa_eight_rules": eight_rules.get("signals", []),
        "pa_eight_rule_primary": primary_rule,
        "pa_eight_rule_score_delta": int(eight_rules.get("score_delta") or 0),
        "pa_eight_rule_risk_delta": int(eight_rules.get("risk_delta") or 0),
    }
    result["pa_trade_plan"] = build_price_action_trade_plan(result)
    result.update(build_completed_timeframe_context(work))
    # ── 波动率(振幅)指标与三周期关系（只增字段，《交易之路》规则）──
    result.update(_amplitude_metrics(work))
    result["pa_daily_state"] = _daily_trend_state(work)
    result["pa_mtf_relation"] = classify_mtf_relation(
        result.get("pa_monthly_state"),
        result.get("pa_weekly_position_state"),
        result["pa_daily_state"],
    )
    # 个股周线MACD顶背离（shadow 字段：只展示/记录，不进闸门，先积累样本验证命中率）
    result["pa_weekly_macd_divergence"] = _weekly_macd_divergence_flag(work)
    result["pa_swing_entry_route"] = {
        "放量突破": "BREAKOUT",
        "缩量回调后放量反包": "PULLBACK",
    }.get(result.get("pa_volume_pattern"), "WAIT")
    return result


# 道氏阶段时间轴去抖：阶段需连续保持 >= N 根K线才确认为一次切换。
# 道氏阶段是持续状态（数日~数周），逐bar独立判定的边界日会在相邻阶段间
# 反复翻转，产生大量单日噪音标记。末段豁免（当前实时阶段即时可见）；
# 合并仅限"间隔恰一个抖动段"的同名段，防止确认段被早期同名噪音段吞掉。
PHASE_TIMELINE_MIN_HOLD_BARS = 3


def _confirm_phase_segments(
    points: List[Dict[str, Any]], min_hold: int = PHASE_TIMELINE_MIN_HOLD_BARS
) -> List[Dict[str, Any]]:
    """把逐bar阶段序列折叠为确认的切换点序列（过滤单日抖动）。

    规则：
    - 中间段需连续保持 >= min_hold 根K线才确认；不足的视为抖动丢弃；
    - 末段豁免（最后一段是当前实时阶段，即便未满 min_hold 也保留供决策参考）；
    - 合并：仅当两个同名段在原序列中"恰好间隔一个被丢弃的抖动段"时合并
      （A(长) → B(1-2天噪音) → A(长) 视为同一阶段延续）；跨多个被丢弃短段的
      同名段是两次独立行情，不得合并——否则确认段会被早先的噪音段吞掉。
    输入 points: [{time, phase, action}, ...] 按时间升序。
    """
    if not points:
        return []
    segments: List[Dict[str, Any]] = []
    for point in points:
        if segments and segments[-1]["phase"] == point["phase"]:
            segments[-1]["bars"] += 1
        else:
            segments.append({**point, "bars": 1, "seg_idx": len(segments)})
    kept = [
        seg for seg in segments
        if seg["bars"] >= min_hold or seg["seg_idx"] == len(segments) - 1
    ]
    result: List[Dict[str, Any]] = []
    for seg in kept:
        item = {
            "time": seg["time"], "phase": seg["phase"], "action": seg["action"],
            "seg_idx": seg["seg_idx"],
        }
        if result and result[-1]["phase"] == seg["phase"]:
            if seg["seg_idx"] - result[-1]["seg_idx"] == 2:
                # 间隔恰为一个抖动段：同名阶段延续，锚点前移但保留最早时间
                result[-1]["seg_idx"] = seg["seg_idx"]
            else:
                # 跨多个被丢弃短段的同名段 = 两次独立热度脉冲：
                # 保留更晚的确认段（避免顶部/关键标注被早先的短噪音段顶掉）
                result[-1] = item
            continue
        result.append(item)
    return [{k: v for k, v in item.items() if k != "seg_idx"} for item in result]


# 道氏阶段 → 图表提示级别
_DOW_PHASE_HINT_LEVEL = {
    "衰竭段": "danger",
    "加速段": "warning",
    "趋势破坏": "danger",
    "主要趋势反转": "danger",
    "空头趋势": "danger",
    "震荡观察": "info",
    "第一波拉升": "info",
    "首次回调": "info",
    "二次入场": "info",
    "突破回踩": "info",
    "初始突破": "info",
}


def build_chart_hints(
    pa_summary: Optional[Dict[str, Any]],
    market: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """把个股 PA 摘要与大盘状态聚合为 K线图上的行情提示条（只增不改）。

    level: info(中性/多头阶段) / warning(过热、软约束) / danger(破坏、背离、防守)。
    纯函数，不触网络；调用方决定 market 是否传入（kline 端点不传以避免额外请求，
    full-analysis 复用 _compute_risk_assessment 里已取到的 regime）。
    """
    hints: List[Dict[str, str]] = []
    summary = pa_summary or {}
    phase = str(summary.get("pa_trend_phase") or "")
    if phase and phase != "数据不足":
        hints.append({
            "level": _DOW_PHASE_HINT_LEVEL.get(phase, "info"),
            "text": f"道氏阶段：{phase}——{summary.get('pa_trend_phase_action') or '观察'}",
        })
    mtf = str(summary.get("pa_mtf_relation") or "")
    if mtf and mtf != "数据不足":
        level = "warning" if mtf in ("逆大势反弹·不追", "大小同向向下·回避") else "info"
        hints.append({"level": level, "text": f"三周期关系：{mtf}"})
    if summary.get("pa_amp_collapse_warn"):
        hints.append({"level": "warning", "text": "强势股振幅骤降，谨防阴跌"})
    if summary.get("pa_amp_spike_warn"):
        hints.append({"level": "warning", "text": "低振幅骤增，变盘前兆观察"})
    if str(summary.get("pa_follow_through_state") or "").upper() == "FAILED":
        hints.append({"level": "warning", "text": "突破跟进失败（该涨不涨），防转弱"})
    # 道氏趋势线破位：mtr 已检出但未进入阶段标签（分类器归入震荡观察），
    # 这里补一条显式提示，避免下跌段在图上一片“震荡观察”。
    if str(summary.get("pa_mtr_state") or "").upper() == "TRENDLINE_BREAK":
        hints.append({"level": "warning", "text": "道氏趋势线破位，中期结构转弱"})
    if summary.get("pa_weekly_macd_divergence"):
        hints.append({"level": "danger", "text": "个股周线MACD顶背离，降低预期（shadow验证中）"})
    market = market or {}
    mstatus = str(market.get("status") or "")
    if market.get("weekly_macd_divergence"):
        hints.append({"level": "danger", "text": "双指数周线MACD顶背离：持有不追"})
    if mstatus == "CRITICAL":
        hints.append({"level": "danger", "text": f"大盘：{market.get('desc') or '空仓防守'}"})
    elif mstatus == "DEFENSIVE":
        hints.append({"level": "warning", "text": f"大盘：{market.get('desc') or '减仓观望'}"})
    season = market.get("seasonality_note")
    if season:
        hints.append({"level": "info", "text": f"季节提示：{season}"})
    return hints


def build_trade_projection(
    pa_summary: Optional[Dict[str, Any]],
    last_close: Optional[float] = None,
    position_plan: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """把当前道氏阶段 + PA 关键位编译成"未来可能走势与执行策略"的图表投影。

    输出（只增不改）：
      key_levels: [{label, price}] —— 回踩买入区 / 失效止损 / 第一目标
      scenarios:  [{name, offsets, values}] —— 相对最后一根K线的"未来第N个交易日"路径
      note:       免责口径（规则推演示意，非预测）
    无有效关键位（entry/stop 缺失）时返回 None（fail-open）。
    """
    summary = pa_summary or {}

    def _num(value: Any) -> Optional[float]:
        try:
            number = float(value)
            return number if number > 0 else None
        except (TypeError, ValueError):
            return None

    entry = _num(summary.get("pa_entry_price"))
    stop = _num(summary.get("pa_stop_price"))
    target = _num(summary.get("pa_target_price"))
    support = _num(summary.get("pa_pullback_support_price")) or _num(summary.get("pa_pullback_confirmation_price"))
    if not entry or not stop:
        return None

    key_levels: List[Dict[str, Any]] = []
    if support and abs(support - entry) > 1e-6 and support > stop + 1e-6:
        # 回踩买入区必须高于止损位才有意义（否则等于“买了就止损”）
        key_levels.append({"label": "回踩买入区", "price": support})
    key_levels.append({"label": "突破触发价", "price": entry})
    key_levels.append({"label": "失效止损", "price": stop})
    if target:
        key_levels.append({"label": "第一目标", "price": target})

    scenarios: List[Dict[str, Any]] = []
    close = _num(last_close)
    if close and target and target > close:
        # 情景A（主路径，与“顺势观察回踩入场”的行动一致）：回踩买入区 → 再上攻目标
        if support and stop < support < close:
            scenarios.append({"name": "回踩再上攻", "offsets": [4, 12], "values": [support, target]})
        # 情景B：不回踩直接上攻
        scenarios.append({"name": "直接上攻", "offsets": [7], "values": [target]})
    # 情景C：跌破失效位
    scenarios.append({"name": "破位失效", "offsets": [5], "values": [stop]})

    if not scenarios:
        return None
    note = (
        f"执行策略（{summary.get('pa_trade_setup') or '价格行为'}）："
        f"{summary.get('pa_trend_phase_action') or '按关键位执行'}；"
        "路径为规则推演示意，非行情预测"
    )
    return {
        "key_levels": key_levels,
        "scenarios": scenarios,
        "note": note,
        "phase": summary.get("pa_trend_phase"),
    }


def build_price_action_annotations(df: pd.DataFrame, lookback: int = 90) -> Dict[str, Any]:
    """
    Build chart-ready price action markers and trend/support lines.

    The result is intentionally lightweight so the frontend can render it with
    lightweight-charts without understanding the recognition internals.
    """
    if df is None or df.empty or len(df) < 20:
        return {"summary": analyze_price_action(df), "markers": [], "lines": [], "phase_timeline": []}

    work = df.copy().reset_index(drop=True)
    work["日期"] = pd.to_datetime(work["日期"], errors="coerce")
    for col in ["开盘", "最高", "最低", "收盘"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["日期", "开盘", "最高", "最低", "收盘"]).reset_index(drop=True)
    if len(work) < 20:
        return {"summary": analyze_price_action(work), "markers": [], "lines": [], "phase_timeline": []}

    summary = analyze_price_action(work)
    start_idx = max(20, len(work) - lookback)
    markers = []
    seen_dates = set()
    previous_volume_pullback_status = "NONE"
    previous_second_entry = None
    previous_follow_through = "NONE"
    previous_mtr = "NONE"
    bull_mtr_index = None
    bull_mtr_rebreak_marked = False
    raw_phase_points: List[Dict[str, Any]] = []

    for idx in range(start_idx, len(work)):
        sub = work.iloc[:idx + 1]
        pa = analyze_price_action(sub)
        signal = pa.get("price_action_signal")
        pattern = pa.get("price_action_pattern")
        score = int(pa.get("price_action_score") or 0)
        eight_rule = pa.get("pa_eight_rule_primary")
        time_str = work["日期"].iloc[idx].strftime("%Y-%m-%d")
        tags = pa.get("pa_tags") or []
        # 道氏趋势阶段序列：先逐bar收集原始阶段，循环结束后按最短持续期去抖折叠
        # （见 _confirm_phase_segments），避免边界日在相邻阶段间反复横跳。
        trend_phase = str(pa.get("pa_trend_phase") or "")
        if trend_phase and trend_phase != "数据不足":
            raw_phase_points.append({
                "time": time_str,
                "phase": trend_phase,
                "action": str(pa.get("pa_trend_phase_action") or ""),
            })
        second_entry = (
            "H2" if pattern == "H2二次入场" or "H2" in tags
            else "L2" if pattern == "L2二次做空信号" or "L2" in tags
            else None
        )
        if second_entry and second_entry != previous_second_entry:
            is_h2 = second_entry == "H2"
            markers.append({
                "time": time_str,
                "position": "belowBar" if is_h2 else "aboveBar",
                "color": "#059669" if is_h2 else "#dc2626",
                "shape": "arrowUp" if is_h2 else "arrowDown",
                "size": 1.1,
                "text": second_entry,
                "label": (
                    "H2：多头趋势中双腿回调后，向上突破前一根K线高点"
                    if is_h2 else
                    "L2：空头趋势中双腿反弹后，向下跌破前一根K线低点"
                ),
                "source": "price_action_second_entry",
            })
        previous_second_entry = second_entry
        follow_through_state = str(pa.get("pa_follow_through_state") or "NONE")
        if follow_through_state != previous_follow_through and follow_through_state in {"STRONG", "FAILED"}:
            is_strong = follow_through_state == "STRONG"
            markers.append({
                "time": time_str,
                "position": "belowBar" if is_strong else "aboveBar",
                "color": "#059669" if is_strong else "#dc2626",
                "shape": "square",
                "size": 0.8,
                "text": "跟进" if is_strong else "突破失败",
                "label": "突破后1至2根K线跟进强" if is_strong else "突破后重新收回关键价位",
                "source": "price_action_follow_through",
            })
        previous_follow_through = follow_through_state
        mtr_state = str(pa.get("pa_mtr_state") or "NONE")
        if mtr_state == "CONFIRMED" and mtr_state != previous_mtr:
            is_bull_mtr = pa.get("pa_mtr_direction") == "BULL_REVERSAL"
            if not is_bull_mtr:
                bull_mtr_index = None
            elif bull_mtr_index is None or idx - bull_mtr_index > PA_VOLUME_PULLBACK_MAX_SESSIONS:
                bull_mtr_index = idx
                bull_mtr_rebreak_marked = False
            markers.append({
                "time": time_str,
                "position": "belowBar" if is_bull_mtr else "aboveBar",
                "color": "#7c3aed",
                "shape": "arrowUp" if is_bull_mtr else "arrowDown",
                "size": 1.0,
                "text": "MTR",
                "label": "趋势线破坏、旧极值测试与反转触发均已出现",
                "source": "price_action_mtr",
            })
        if bull_mtr_index is not None and not bull_mtr_rebreak_marked:
            rebreak = _mtr_first_pullback_rebreak(work, bull_mtr_index, idx)
            if rebreak:
                markers.append({
                    "time": time_str,
                    "position": "belowBar",
                    "color": "#0f766e",
                    "shape": "arrowUp",
                    "text": "首次回踩突破·观察",
                    "label": f"向上反转确认后首次缩量回踩，收盘突破{rebreak['trigger_price']:.2f}；回踩低点{rebreak['pullback_low']:.2f}仅作结构参考，仍须按现有风控确认",
                    "source": "mtr_pullback_rebreak",
                    **rebreak,
                })
                bull_mtr_rebreak_marked = True
        previous_mtr = mtr_state
        volume_pullback_status = str(pa.get("pa_volume_pullback_status") or "NONE")
        if (
            volume_pullback_status != previous_volume_pullback_status
            and volume_pullback_status in {"BREAKOUT", "PULLBACK", "CONFIRMED", "INVALIDATED"}
            and time_str not in seen_dates
        ):
            marker_style = {
                "BREAKOUT": ("belowBar", "#dc2626", "放量突破"),
                "PULLBACK": ("belowBar", "#0f766e", "缩量回踩"),
                "CONFIRMED": ("belowBar", "#2563eb", "回踩企稳"),
                "INVALIDATED": ("aboveBar", "#dc2626", "回踩失效"),
            }[volume_pullback_status]
            markers.append({
                "time": time_str,
                "position": marker_style[0],
                "color": marker_style[1],
                "shape": "square",
                "size": 0.8,
                "text": marker_style[2],
                "label": (pa.get("pa_volume_pullback") or {}).get("reason"),
                "source": "volume_pullback",
            })
            seen_dates.add(time_str)
        previous_volume_pullback_status = volume_pullback_status
        if eight_rule and int(eight_rule.get("confidence") or 0) >= 75 and time_str not in seen_dates:
            is_risk = eight_rule.get("direction") == "RISK"
            markers.append({
                "time": time_str,
                "position": "inBar",
                "color": "#dc2626" if is_risk else "#059669",
                "shape": "square",
                "size": 0.7,
                "text": eight_rule.get("rule_code", "8"),
                "label": eight_rule.get("label"),
                "source": "eight_rule",
            })
            seen_dates.add(time_str)
        if second_entry:
            continue
        if score < 55 or signal in (None, "暂无", "普通K线"):
            continue

        if time_str in seen_dates:
            continue
        seen_dates.add(time_str)

        is_bull = any(word in str(signal) + str(pattern) for word in ["多头", "H2", "突破", "回踩", "反转"])
        markers.append({
            "time": time_str,
            "position": "belowBar" if is_bull else "aboveBar",
            "color": "#2563eb" if is_bull else "#dc2626",
            "shape": "circle",
            "text": f"PA {pattern if pattern and pattern != '无明确形态' else signal}",
        })

    recent = work.tail(min(len(work), 120)).reset_index(drop=True)
    high_points = _local_extrema(recent["最高"], "high")
    low_points = _local_extrema(recent["最低"], "low")
    lines = []

    def point(idx: int, col: str) -> Dict[str, Any]:
        return {
            "time": recent["日期"].iloc[idx].strftime("%Y-%m-%d"),
            "value": round(_safe_float(recent[col].iloc[idx]), 2),
        }

    def trendline(start: int, anchors: List[int], column: str, direction: str):
        start_value = _safe_float(recent[column].iloc[start])
        for end in reversed(anchors):
            if end <= start:
                continue
            end_value = _safe_float(recent[column].iloc[end])
            if (direction == "up" and end_value <= start_value) or (
                direction == "down" and end_value >= start_value
            ):
                continue
            slope = (end_value - start_value) / (end - start)
            between = range(start + 1, end)
            if direction == "up":
                crossed = any(
                    start_value + slope * (idx - start) > _safe_float(recent["最低"].iloc[idx])
                    for idx in between
                )
            else:
                crossed = any(
                    start_value + slope * (idx - start) < _safe_float(recent["最高"].iloc[idx])
                    for idx in between
                )
            if not crossed:
                breach_indices = []
                for idx in range(end + 1, len(recent)):
                    line_value = start_value + slope * (idx - start)
                    close = _safe_float(recent["收盘"].iloc[idx])
                    breached = close < line_value if direction == "up" else close > line_value
                    breach_indices.append(idx if breached else None)
                confirmed_break = next(
                    (
                        breach_indices[offset + 1]
                        for offset in range(len(breach_indices) - 1)
                        if breach_indices[offset] is not None and breach_indices[offset + 1] is not None
                    ),
                    None,
                )
                latest_close = _safe_float(recent["收盘"].iloc[-1])
                latest_line = start_value + slope * (len(recent) - 1 - start)
                testing_break = (
                    latest_close < latest_line if direction == "up" else latest_close > latest_line
                ) and confirmed_break is None
                return {
                    "end_idx": len(recent) - 1,
                    "end_value": latest_line,
                    "broken": confirmed_break is not None,
                    "break_idx": confirmed_break,
                    "testing_break": testing_break,
                }
        return None

    if low_points:
        start = int(recent["最低"].idxmin())
        peak = int(recent["最高"].idxmax())
        result = trendline(start, [idx for idx in low_points if idx < peak], "最低", "up")
        if result:
            broken = result["broken"]
            lines.append({
                "kind": "support",
                "label": "上升线已跌破·回抽压力参考" if broken else (
                    "上升线破位待确认" if result["testing_break"] else "道氏上升趋势线·有效支撑"
                ),
                "status": "broken" if broken else "testing_break" if result["testing_break"] else "active",
                "break_date": (
                    recent["日期"].iloc[result["break_idx"]].strftime("%Y-%m-%d")
                    if broken else None
                ),
                "color": "#b45309" if broken or result["testing_break"] else "#0d9488",
                "style": "dashed",
                "points": [point(start, "最低"), {
                    "time": recent["日期"].iloc[result["end_idx"]].strftime("%Y-%m-%d"),
                    "value": round(result["end_value"], 2),
                }],
            })

    if high_points:
        start = int(recent["最高"].idxmax())
        trough = int(recent["最低"].idxmin())
        result = trendline(start, [idx for idx in high_points if idx < trough], "最高", "down")
        if result:
            broken = result["broken"]
            lines.append({
                "kind": "resistance",
                "label": "下降线已突破·回踩支撑参考" if broken else (
                    "下降线突破待确认" if result["testing_break"] else "道氏下降趋势线·有效压力"
                ),
                "status": "broken" if broken else "testing_break" if result["testing_break"] else "active",
                "break_date": (
                    recent["日期"].iloc[result["break_idx"]].strftime("%Y-%m-%d")
                    if broken else None
                ),
                "color": "#0d9488" if broken else "#b45309" if result["testing_break"] else "#dc2626",
                "style": "dashed",
                "points": [point(start, "最高"), {
                    "time": recent["日期"].iloc[result["end_idx"]].strftime("%Y-%m-%d"),
                    "value": round(result["end_value"], 2),
                }],
            })

    if summary.get("pa_entry_price") and summary.get("pa_stop_price"):
        last_time = work["日期"].iloc[-1].strftime("%Y-%m-%d")
        first_time = work["日期"].iloc[max(0, len(work) - 25)].strftime("%Y-%m-%d")
        lines.append({
            "kind": "entry",
            "label": "PA入场触发",
            "color": "#2563eb",
            "style": "solid",
            "points": [
                {"time": first_time, "value": summary["pa_entry_price"]},
                {"time": last_time, "value": summary["pa_entry_price"]},
            ],
        })
        lines.append({
            "kind": "stop",
            "label": "PA失效位",
            "color": "#e11d48",
            "style": "dotted",
            "points": [
                {"time": first_time, "value": summary["pa_stop_price"]},
                {"time": last_time, "value": summary["pa_stop_price"]},
            ],
        })

    volume_pullback = summary.get("pa_volume_pullback") or {}
    if volume_pullback.get("support_price") and volume_pullback.get("breakout_date"):
        last_time = work["日期"].iloc[-1].strftime("%Y-%m-%d")
        lines.append({
            "kind": "pullback_support",
            "label": "突破回踩支撑",
            "color": "#0f766e",
            "style": "dashed",
            "points": [
                {"time": volume_pullback["breakout_date"], "value": volume_pullback["support_price"]},
                {"time": last_time, "value": volume_pullback["support_price"]},
            ],
        })
        if (
            volume_pullback.get("stop_price")
            and volume_pullback.get("status") != "INVALIDATED"
        ):
            lines.append({
                "kind": "pullback_stop",
                "label": "回踩失效参考",
                "color": "#e11d48",
                "style": "dotted",
                "points": [
                    {"time": volume_pullback["breakout_date"], "value": volume_pullback["stop_price"]},
                    {"time": last_time, "value": volume_pullback["stop_price"]},
                ],
            })

    first_time = work["日期"].iloc[max(0, len(work) - 25)].strftime("%Y-%m-%d")
    last_time = work["日期"].iloc[-1].strftime("%Y-%m-%d")
    for kind, label, color, zone in (
        ("support_zone", "共振支撑区", "#0f766e", summary.get("pa_nearest_support_zone")),
        ("resistance_zone", "共振压力区", "#b45309", summary.get("pa_nearest_resistance_zone")),
    ):
        if zone and zone.get("center"):
            lines.append({
                "kind": kind,
                "label": f"{label}（{zone.get('strength', 1)}项）",
                "color": color,
                "style": "dashed",
                "points": [
                    {"time": first_time, "value": zone["center"]},
                    {"time": last_time, "value": zone["center"]},
                ],
            })

    gap_edges = summary.get("pa_gap_edges") or {}
    for edge_name, edge_label in (("lower", "缺口下沿"), ("upper", "缺口上沿")):
        if gap_edges.get(edge_name):
            lines.append({
                "kind": "gap_edge",
                "label": edge_label,
                "color": "#9333ea",
                "style": "dotted",
                "points": [
                    {"time": work["日期"].iloc[-2].strftime("%Y-%m-%d"), "value": gap_edges[edge_name]},
                    {"time": last_time, "value": gap_edges[edge_name]},
                ],
            })

    eight_markers = [marker for marker in markers if marker.get("source") == "eight_rule"][-6:]
    other_markers = [marker for marker in markers if marker.get("source") != "eight_rule"][-18:]
    compact_markers = sorted(other_markers + eight_markers, key=lambda marker: marker["time"])
    return {"summary": summary, "markers": compact_markers, "lines": lines, "phase_timeline": _confirm_phase_segments(raw_phase_points, PHASE_TIMELINE_MIN_HOLD_BARS)}
