"""
Unified risk engine for paper-trading display, alerts, and automation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from core.risk_constants import (
    ATR_STOP_MAX_PCT,
    ATR_STOP_MIN_PCT,
    ATR_STOP_MULTIPLIER,
    CAPITAL_PROTECT_FLOOR_PCT,
    CAPITAL_PROTECT_THRESHOLD_PCT,
    FIXED_STOP_LOSS_RATIO,
    TAKE_PROFIT_RATIO,
    TIER_HIGH_PROFIT_PCT,
    TIER_HIGH_TRAIL_RATIO,
    TIER_MID_PROFIT_PCT,
    TIER_MID_TRAIL_RATIO,
    WEAK_REGIMES,
    WEAK_REGIME_STOP_RATIO,
)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def track_high_since_entry(
    entry_price: float,
    stored_high: float,
    current_price: float,
    snapshot_high: float,
    entry_date: Any,
    now: datetime | None = None,
) -> float:
    """Avoid treating a same-day pre-entry high as post-entry profit."""
    entry_price = safe_float(entry_price)
    current_price = safe_float(current_price, entry_price)
    now = now or datetime.now()
    parsed_entry_date = pd.to_datetime(entry_date, errors="coerce")
    if not pd.isna(parsed_entry_date) and parsed_entry_date.date() == now.date():
        return max(entry_price, current_price)
    return max(
        entry_price,
        safe_float(stored_high, entry_price),
        current_price,
        safe_float(snapshot_high, current_price),
    )


def compute_paper_risk_levels(
    entry_price: float,
    high_since_entry: float,
    current_price: float,
    price_action_summary: dict | None = None,
    atr: float | None = None,
    market_regime: str | None = None,
) -> dict:
    """
    Compute the current paper-trading risk ladder.

    Initial and valid structure stops are always considered; capital protection
    and trailing stops are activated only after profit milestones are reached.

    When ``atr`` is provided (个股波动率 ATR), the initial stop is tightened to
    ``max(固定止损, ATR自适应止损)``. 关键安全语义：ATR 只会让初始止损更紧、
    不会比固定 -9% 更宽，从而让实盘 active_stop_price 与回测行为一致，并避免
    对低波动股设置过宽的止损。这与 strategy.py 的 evaluate_exit_signals 中
    ATR 止损语义对齐，但这里额外用 max() 锁定"只收紧不放宽"。

    When ``market_regime`` 为弱市（bear/volatile，见 WEAK_REGIMES），已有持仓的
    初始止损从 -9% 进一步收紧到 -6%（WEAK_REGIME_STOP_RATIO），降低系统性回撤期
    的单笔风险。同样只收紧不放宽。
    """
    entry_price = safe_float(entry_price)
    if entry_price <= 0:
        return {
            "buy_price": 0.0,
            "initial_stop_price": 0.0,
            "structure_stop_price": 0.0,
            "capital_protect_price": 0.0,
            "moving_stop_price": 0.0,
            "stop_price": 0.0,
            "active_stop_price": 0.0,
            "take_profit_price": 0.0,
            "max_pl_pct": 0.0,
            "pl_pct": 0.0,
            "risk_reward": None,
            "risk_stage": "无持仓",
            "risk_notes": [],
        }

    high_since_entry = max(safe_float(high_since_entry, entry_price), entry_price)
    current_price = safe_float(current_price, entry_price) or entry_price
    pa = price_action_summary or {}

    buy_price = round(entry_price, 2)
    # 固定止损作为基准（-9%）。ATR 止损（若可用）只会更紧，不会更宽。
    fixed_stop_price = round(entry_price * FIXED_STOP_LOSS_RATIO, 2)
    atr_note = None
    atr = safe_float(atr, 0.0)
    if atr > 0:
        # ATR 自适应止损：entry - N * ATR，并 clamp 到 [ATR_STOP_MAX_PCT, ATR_STOP_MIN_PCT]
        # （即 [-15%, -5%]，防止低波动过紧、高波动过宽）。
        atr_stop_price = round(entry_price - atr * ATR_STOP_MULTIPLIER, 2)
        atr_stop_lower = round(entry_price * (1 + ATR_STOP_MAX_PCT / 100), 2)  # -15%
        atr_stop_upper = round(entry_price * (1 + ATR_STOP_MIN_PCT / 100), 2)  # -5%
        atr_stop_price = max(atr_stop_lower, min(atr_stop_upper, atr_stop_price))
        # 只收紧不放宽：ATR 止损至少要和固定止损一样紧（取较高者）。
        initial_stop_price = max(fixed_stop_price, atr_stop_price)
        if initial_stop_price > fixed_stop_price:
            atr_note = f"ATR 自适应止损收紧至 {initial_stop_price:.2f}（固定止损 {fixed_stop_price:.2f}）"
    else:
        initial_stop_price = fixed_stop_price

    risk_notes: list[str] = []

    # 弱市止损收紧：bear/volatile 时把初始止损从 -9% 收紧到 -6%，只收紧不放宽。
    regime_label = str(market_regime or "").lower()
    if regime_label in WEAK_REGIMES:
        weak_stop_price = round(entry_price * WEAK_REGIME_STOP_RATIO, 2)
        if weak_stop_price > initial_stop_price:
            initial_stop_price = weak_stop_price
            risk_notes.append(
                f"弱市({regime_label})止损收紧至 {initial_stop_price:.2f}"
                f"（常规固定止损 {fixed_stop_price:.2f}）"
            )

    max_pl_pct = ((high_since_entry - entry_price) / entry_price) * 100
    pl_pct = ((current_price - entry_price) / entry_price) * 100

    candidates = [initial_stop_price]
    if atr_note:
        risk_notes.append(atr_note)

    structure_stop_price = 0.0
    pa_stop = safe_float(pa.get("pa_stop_price"))
    if pa_stop > 0 and pa_stop < current_price:
        structure_risk_pct = (entry_price - pa_stop) / entry_price * 100
        current_gap_pct = (current_price - pa_stop) / current_price * 100
        if pa_stop > entry_price * 1.03:
            risk_notes.append("结构失效位高于买入价过多，未纳入执行风控")
        elif structure_risk_pct > 15:
            risk_notes.append("结构止损距离买入价超过15%，未纳入执行风控")
        else:
            structure_stop_price = round(pa_stop, 2)
            candidates.append(structure_stop_price)
            if current_gap_pct < 2:
                risk_notes.append("结构失效位距离现价不足2%，需警惕假突破回落")
            elif structure_risk_pct > 12:
                risk_notes.append("结构止损超过12%，仓位应降低")

    capital_protect_price = 0.0
    moving_stop_price = 0.0
    risk_stage = "初始/结构防守"

    if max_pl_pct >= CAPITAL_PROTECT_THRESHOLD_PCT:
        capital_protect_price = round(entry_price * (1 + CAPITAL_PROTECT_FLOOR_PCT / 100), 2)
        candidates.append(capital_protect_price)
        risk_stage = "保本保护"

    if max_pl_pct >= TIER_HIGH_PROFIT_PCT:
        moving_stop_price = round(high_since_entry * TIER_HIGH_TRAIL_RATIO, 2)
        candidates.append(moving_stop_price)
        risk_stage = "强盈利收紧"
    elif max_pl_pct >= TIER_MID_PROFIT_PCT:
        moving_stop_price = round(high_since_entry * TIER_MID_TRAIL_RATIO, 2)
        candidates.append(moving_stop_price)
        risk_stage = "移动风控"

    active_stop_price = round(max(candidates), 2)

    structure_target = safe_float(pa.get("pa_target_price"))
    risk_per_share = max(entry_price - active_stop_price, 0.01)
    rr_target = round(entry_price + risk_per_share * 2, 2)
    fixed_target = round(entry_price * TAKE_PROFIT_RATIO, 2)
    take_profit_price = fixed_target
    if structure_target > entry_price:
        take_profit_price = round(structure_target, 2)
    elif active_stop_price < entry_price:
        take_profit_price = rr_target

    risk_reward = None
    if active_stop_price < entry_price and take_profit_price > entry_price:
        risk_reward = round((take_profit_price - entry_price) / (entry_price - active_stop_price), 2)

    return {
        "buy_price": buy_price,
        "initial_stop_price": initial_stop_price,
        "structure_stop_price": structure_stop_price,
        "capital_protect_price": capital_protect_price,
        "moving_stop_price": moving_stop_price,
        "stop_price": active_stop_price,
        "active_stop_price": active_stop_price,
        "take_profit_price": take_profit_price,
        "max_pl_pct": round(max_pl_pct, 2),
        "pl_pct": round(pl_pct, 2),
        "risk_reward": risk_reward,
        "risk_stage": risk_stage,
        "risk_notes": risk_notes,
    }
