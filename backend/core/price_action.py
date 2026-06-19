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

from core.eight_rules import detect_eight_rules

PRICE_ACTION_VERSION = "price-action-v3"
TARGET_MODEL_VERSION = "structure-target-v2"
SCORE_MODEL_VERSION = "pa-three-score-v1"


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
    """Brooks 方法论的回调腿计数（修复原 close-to-close 简单计数法）。

    原逻辑用 close-diff 方向运行 + min_swing=半根K线，过度计数（任何1根下跌=1腿）。
    修复：用 swing-point（波段高低点）检测交替推力结构，回调需在 Fibonacci
    38.2%-61.8% 回调带内才算一有效"腿"。这是 Brooks H2/L2 买点的核心。

    对于 bull 方向：找"推力上→回调"的交替对，回调幅度在推力的 38.2%-61.8% 之间。
    """
    if len(work) < 6:
        return 0

    def fallback_count() -> int:
        recent_simple = work.tail(lookback + 1).reset_index(drop=True)
        closes = recent_simple["收盘"].astype(float)
        ranges = (recent_simple["最高"].astype(float) - recent_simple["最低"].astype(float)).replace(0, np.nan)
        min_swing = max(_safe_float(ranges.mean()) * 0.5, _safe_float(closes.iloc[-1]) * 0.005)
        moves = closes.diff().iloc[1:-1]
        if moves.empty or min_swing <= 0:
            return 0

        leg_count = 0
        leg_move = 0.0
        for move in moves:
            is_pullback = move < 0 if direction == "bull" else move > 0
            if is_pullback:
                leg_move += abs(float(move))
            elif leg_move > 0:
                if leg_move >= min_swing:
                    leg_count += 1
                leg_move = 0.0
        if leg_move >= min_swing:
            leg_count += 1
        return leg_count

    recent = work.tail(lookback + 2).reset_index(drop=True)
    # 排除最后一根（信号棒）
    body = recent.iloc[:-1]
    highs = body["最高"].astype(float)
    lows = body["最低"].astype(float)

    # 用 _local_extrema 找 swing points
    swing_highs = _local_extrema(highs, "high")
    swing_lows = _local_extrema(lows, "low")
    if len(swing_highs) < 1 or len(swing_lows) < 1:
        return fallback_count()

    if direction == "bull":
        # bull 回调：找 高点→低点 的回撤，回撤幅度在 前推力(低点→高点) 的 38.2%-61.8%
        # 合并并排序所有 swing points
        legs = 0
        # 从最近的 swing 开始回溯
        all_points = [(i, "H") for i in swing_highs] + [(i, "L") for i in swing_lows]
        all_points.sort(key=lambda x: x[0])
        # 找 交替的 H-L 对（推力高点→回调低点）
        for idx in range(len(all_points) - 2):
            p0 = all_points[idx]
            p1 = all_points[idx + 1]
            p2 = all_points[idx + 2] if idx + 2 < len(all_points) else None
            # 模式: L → H → L（推力上→回调）
            if p0[1] == "L" and p1[1] == "H" and p2 and p2[1] == "L":
                impulse = highs.iloc[p1[0]] - lows.iloc[p0[0]]  # 推力幅度
                pullback = highs.iloc[p1[0]] - lows.iloc[p2[0]]  # 回调幅度
                if impulse > 0:
                    retrace_pct = pullback / impulse
                    if 0.382 <= retrace_pct <= 0.618:
                        legs += 1
        return legs or fallback_count()
    else:
        # bear 回调：找 低点→高点 的反弹，幅度在前下跌推力的 38.2%-61.8%
        legs = 0
        all_points = [(i, "H") for i in swing_highs] + [(i, "L") for i in swing_lows]
        all_points.sort(key=lambda x: x[0])
        for idx in range(len(all_points) - 2):
            p0 = all_points[idx]
            p1 = all_points[idx + 1]
            p2 = all_points[idx + 2] if idx + 2 < len(all_points) else None
            # 模式: H → L → H（推力下→反弹）
            if p0[1] == "H" and p1[1] == "L" and p2 and p2[1] == "H":
                impulse = highs.iloc[p0[0]] - lows.iloc[p1[0]]
                pullback = highs.iloc[p2[0]] - lows.iloc[p1[0]]
                if impulse > 0:
                    retrace_pct = pullback / impulse
                    if 0.382 <= retrace_pct <= 0.618:
                        legs += 1
        return legs or fallback_count()


def _volume_series(work: pd.DataFrame) -> pd.Series:
    for col in ("成交量", "vol", "volume"):
        if col in work.columns:
            return pd.to_numeric(work[col], errors="coerce").fillna(0)
    return pd.Series([0] * len(work), index=work.index, dtype=float)


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
    prev = work.iloc[-2]
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
    prev_high = _safe_float(prev["最高"])
    bar_range = max(high - low, 0.01)
    close_position = (close - low) / bar_range
    structure_intact = invalidation_price <= 0 or close > invalidation_price
    support_held = support_price <= 0 or close >= support_price or low >= support_price * 0.985
    pullback_shrinking = pullback_volume <= 0 or avg_volume_20 <= 0 or pullback_volume <= avg_volume_20 * 0.8
    close_strength = close > open_price and close_position >= 0.6
    price_confirmed = close > prev_high and close > open_price
    volume_confirmed = avg_volume_20 > 0 and last_volume >= avg_volume_20 * 1.15
    trend_intact = bull_context and trend_damage not in {"跌破EMA20", "跌破EMA60", "短线低点破坏"}
    volume_breakdown = avg_volume_20 > 0 and last_volume >= avg_volume_20 * 1.3 and close < open_price and close_position <= 0.35

    checks = [
        {"key": "structure", "label": "结构未破", "passed": structure_intact and support_held},
        {"key": "volume", "label": "回踩缩量", "passed": pullback_shrinking},
        {"key": "close", "label": "收盘转强", "passed": close_strength},
        {"key": "confirmation", "label": "突破回踩K高点", "passed": price_confirmed},
        {"key": "confirm_volume", "label": "确认K放量", "passed": volume_confirmed},
        {"key": "trend", "label": "趋势保持", "passed": trend_intact},
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
        return {"context": "周线数据不足", "score": 0, "note": "日线样本不足，暂不做多周期确认。"}

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
            current_week_complete = bool(dated.index[-1].weekday() == 4)
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
        if len(fallback) >= len(weekly):
            weekly = fallback
    if len(weekly) < 5:
        return {"context": "周线数据不足", "score": 0, "note": "周线合成样本不足，暂不做多周期确认。", "current_week_complete": current_week_complete}

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
    result["current_week_complete"] = current_week_complete
    if not current_week_complete:
        result["score"] = int(round(result["score"] * 0.5))
        result["note"] = f"{result['note']} 本周尚未收盘，仅按观察信号计半权重。"
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
    risks = list(summary.get("price_action_risks") or [])

    setup_name = pattern if pattern and pattern != "无明确形态" else signal
    invalidation = "暂无明确结构失效位。"
    if stop_price > 0:
        invalidation = f"收盘或次日盘中有效跌破 {stop_price:.2f}，视为结构失效。"

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

    checklist = [
        "14:30后确认K线形态仍保持强势",
        "板块内有至少2只以上个股同步走强",
        "未出现长上影或放量回落",
    ]
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
        "execution_score": execution_score,
        "risk_score": risk_score,
        "position_hint": "轻仓/观察" if risk_pct >= 8 or action != "READY" else "标准仓位候选",
        "checklist": checklist,
        "management": management,
        "avoid_reasons": list(dict.fromkeys(avoid_reasons + risks))[:5],
        "pullback_validity": pullback_validity,
    }


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
        "pa_range_rule": "数据不足",
        "pa_failed_breakout_type": None,
        "pa_trap_risk": 0,
        "pa_micro_channel": "无",
        "pa_always_in_strength": 0,
        "pa_trend_damage": "无",
        "pa_channel_state": "无明显通道",
        "pa_position_strategy": "等待更多K线",
        "pa_weekly_context": "周线数据不足",
        "pa_multi_timeframe_score": 0,
        "pa_multi_timeframe_note": "日线样本不足，暂不做多周期确认。",
        "pa_current_week_complete": False,
        "price_action_version": PRICE_ACTION_VERSION,
        "target_model_version": TARGET_MODEL_VERSION,
        "score_model_version": SCORE_MODEL_VERSION,
        "pa_volume_pattern": "量能不足",
        "pa_volume_confirmed": False,
        "pa_volume_ratio": 0,
        "pa_volume_ratio_percentile": 0,
        "pa_breakout_volume_threshold": 0,
        "pa_confirmation_volume_threshold": 0,
        "pa_volume_risk": "量能数据不足",
        "pa_failed_second_entry": None,
        "pa_second_entry_risk": 0,
        "pa_gap_type": "无缺口",
        "pa_gap_risk": 0,
        "pa_range_width_quality": "未知",
        "pa_range_center_risk": 0,
        "pa_range_failed_breakout_count": 0,
        "pa_trend_phase": "数据不足",
        "pa_trend_phase_action": "等待更多K线",
        "pa_decision_summary": "K线样本不足，暂不识别价格行为结构。",
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
    avg_volume_20 = _safe_float(volumes.tail(20).mean())
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
    bull_pullback_legs = _count_pullback_legs(work, "bull")
    bear_pullback_legs = _count_pullback_legs(work, "bear")
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

    gap_pct = (_safe_float(last["开盘"]) - _safe_float(prev["收盘"])) / max(_safe_float(prev["收盘"]), 0.01)
    gap_type = "无缺口"
    gap_risk = 0
    if gap_pct >= 0.03 and last["收盘"] > last["开盘"] and last_close_position >= 0.65:
        gap_type = "向上跳空延续"
        gap_risk = 20
        tags.append("跳空延续")
    elif gap_pct >= 0.03 and last["收盘"] < last["开盘"]:
        gap_type = "高开低走缺口失败"
        gap_risk = 75
        risks.append("高开后收弱，需防缺口失败。")
        tags.append("缺口失败")
    elif gap_pct <= -0.03 and last["收盘"] < last["开盘"]:
        gap_type = "向下跳空破位"
        gap_risk = 80
        risks.append("向下跳空破位，短线风险升高。")
        tags.append("跳空破位")
    elif gap_pct <= -0.03 and last["收盘"] > last["开盘"]:
        gap_type = "向下跳空修复"
        gap_risk = 35
        tags.append("缺口修复")

    pattern = "无明确形态"
    pattern_score = 0
    near_ema20 = abs(_safe_float(last["收盘"]) - ema20_now) <= atr20 * 1.2
    broke_recent_high = last["收盘"] > prior_high_20 and is_bull_trend_bar
    broke_recent_low = last["收盘"] < prior_low_20 and is_bear_trend_bar

    last_10 = work.tail(10)
    had_breakout = bool((last_10["最高"].shift(1) > work["最高"].rolling(20).max().shift(2).tail(10)).fillna(False).any())
    pullback_then_bull = (
        regime in {"多头趋势", "向上突破"} and
        near_ema20 and
        is_bull_reversal and
        work["收盘"].iloc[-2] < work["收盘"].iloc[-3]
    )
    h1_entry = bull_context and bull_pullback_legs == 1 and breaks_prev_high and last["收盘"] > last["开盘"]
    h2_entry = bull_context and bull_pullback_legs >= 2 and breaks_prev_high and last["收盘"] > last["开盘"]
    l1_entry = bear_context and bear_pullback_legs == 1 and breaks_prev_low and last["收盘"] < last["开盘"]
    l2_entry = bear_context and bear_pullback_legs >= 2 and breaks_prev_low and last["收盘"] < last["开盘"]

    failed_second_entry = None
    second_entry_risk = 0
    prior_bull_second_try = (
        prev["收盘"] > prev["开盘"] and
        prev["最高"] > work["最高"].iloc[-3] and
        _count_pullback_legs(work.iloc[:-1], "bull") >= 2
    )
    prior_bear_second_try = (
        prev["收盘"] < prev["开盘"] and
        prev["最低"] < work["最低"].iloc[-3] and
        _count_pullback_legs(work.iloc[:-1], "bear") >= 2
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

    if h2_entry or pullback_then_bull:
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
    elif had_breakout and near_ema20 and last["收盘"] > last["开盘"]:
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
    if pullback_legs >= 2:
        pullback_structure = "双腿回调"
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
    elif had_breakout and last["收盘"] <= prior_high_20:
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
    failure_risk = int(max(0, min(100, failure_risk)))

    entry_quality_score = 0
    h2_quality = "不适用"
    if h2_entry or pullback_then_bull:
        entry_quality_score = 35
        if near_ema20:
            entry_quality_score += 20
        if is_bull_trend_bar or is_bull_reversal:
            entry_quality_score += 20
        if regime != "交易区间" or range_location != "区间上沿":
            entry_quality_score += 15
        if bull_pullback_legs >= 2:
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
    # 兜底：若止损 >= 入场价（信号棒倒置等异常），用 -3% 紧急止损
    if stop_price >= entry_price and entry_price > 0:
        stop_price = round(entry_price * 0.97, 2)
    support_price = max(stop_price, round(ema20_now, 2)) if bull_context else stop_price
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

    if trend_damage in {"跌破EMA60", "跌破EMA20", "短线低点破坏"}:
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
    if trend_damage in {"跌破EMA60", "跌破EMA20", "短线低点破坏"}:
        trend_phase = "趋势破坏"
        trend_phase_action = "减仓并等待修复"
    elif channel_state == "上轨过冲回落" or "楔形" in tags:
        trend_phase = "衰竭段"
        trend_phase_action = "不追高，优先保护利润"
    elif h2_entry or pullback_then_bull:
        trend_phase = "二次入场"
        trend_phase_action = "只在触发价有效站上后执行"
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
    if trend_phase:
        decision_parts.append(f"阶段：{trend_phase}")
    if failed_breakout_type:
        decision_parts.append(f"假突破：{failed_breakout_type}")
    if gap_type != "无缺口":
        decision_parts.append(f"缺口：{gap_type}")
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
        "pa_range_rule": range_rule,
        "pa_failed_breakout_type": failed_breakout_type,
        "pa_trap_risk": trap_risk,
        "pa_micro_channel": micro_channel,
        "pa_always_in_strength": always_in_strength,
        "pa_trend_damage": trend_damage,
        "pa_channel_state": channel_state,
        "pa_position_strategy": position_strategy,
        "pa_weekly_context": weekly_context,
        "pa_multi_timeframe_score": mtf_score,
        "pa_multi_timeframe_note": mtf_note,
        "pa_current_week_complete": current_week_complete,
        "price_action_version": PRICE_ACTION_VERSION,
        "target_model_version": TARGET_MODEL_VERSION,
        "score_model_version": SCORE_MODEL_VERSION,
        "pa_volume_pattern": volume_pattern,
        "pa_volume_confirmed": volume_confirmed,
        "pa_volume_ratio": round(volume_ratio, 2),
        "pa_volume_ratio_percentile": volume_ratio_percentile,
        "pa_breakout_volume_threshold": round(breakout_volume_threshold, 2),
        "pa_confirmation_volume_threshold": round(confirmation_volume_threshold, 2),
        "pa_volume_risk": volume_risk,
        "pa_failed_second_entry": failed_second_entry,
        "pa_second_entry_risk": second_entry_risk,
        "pa_gap_type": gap_type,
        "pa_gap_risk": gap_risk,
        "pa_range_width_quality": range_width_quality,
        "pa_range_center_risk": range_center_risk,
        "pa_range_failed_breakout_count": failed_breakout_count,
        "pa_trend_phase": trend_phase,
        "pa_trend_phase_action": trend_phase_action,
        "pa_decision_summary": decision_summary,
        "pa_eight_rules": eight_rules.get("signals", []),
        "pa_eight_rule_primary": primary_rule,
        "pa_eight_rule_score_delta": int(eight_rules.get("score_delta") or 0),
        "pa_eight_rule_risk_delta": int(eight_rules.get("risk_delta") or 0),
    }
    result["pa_trade_plan"] = build_price_action_trade_plan(result)
    return result


def build_price_action_annotations(df: pd.DataFrame, lookback: int = 90) -> Dict[str, Any]:
    """
    Build chart-ready price action markers and trend/support lines.

    The result is intentionally lightweight so the frontend can render it with
    lightweight-charts without understanding the recognition internals.
    """
    if df is None or df.empty or len(df) < 20:
        return {"summary": analyze_price_action(df), "markers": [], "lines": []}

    work = df.copy().reset_index(drop=True)
    work["日期"] = pd.to_datetime(work["日期"], errors="coerce")
    for col in ["开盘", "最高", "最低", "收盘"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["日期", "开盘", "最高", "最低", "收盘"]).reset_index(drop=True)
    if len(work) < 20:
        return {"summary": analyze_price_action(work), "markers": [], "lines": []}

    summary = analyze_price_action(work)
    start_idx = max(20, len(work) - lookback)
    markers = []
    seen_dates = set()

    for idx in range(start_idx, len(work)):
        sub = work.iloc[:idx + 1]
        pa = analyze_price_action(sub)
        signal = pa.get("price_action_signal")
        pattern = pa.get("price_action_pattern")
        score = int(pa.get("price_action_score") or 0)
        eight_rule = pa.get("pa_eight_rule_primary")
        time_str = work["日期"].iloc[idx].strftime("%Y-%m-%d")
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

    if len(low_points) >= 2:
        p1, p2 = low_points[-2], low_points[-1]
        if p1 < p2:
            lines.append({
                "kind": "support",
                "label": "上升趋势线/支撑",
                "color": "#0d9488",
                "style": "dashed",
                "points": [point(p1, "最低"), point(p2, "最低")],
            })

    if len(high_points) >= 2:
        p1, p2 = high_points[-2], high_points[-1]
        if p1 < p2:
            lines.append({
                "kind": "resistance",
                "label": "下降趋势线/压力",
                "color": "#dc2626",
                "style": "dashed",
                "points": [point(p1, "最高"), point(p2, "最高")],
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

    eight_markers = [marker for marker in markers if marker.get("source") == "eight_rule"][-6:]
    other_markers = [marker for marker in markers if marker.get("source") != "eight_rule"][-18:]
    compact_markers = sorted(other_markers + eight_markers, key=lambda marker: marker["time"])
    return {"summary": summary, "markers": compact_markers, "lines": lines}
