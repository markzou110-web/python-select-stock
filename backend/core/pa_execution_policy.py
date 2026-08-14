"""Execution-only price-action tiers; stock discovery remains unchanged."""
from __future__ import annotations

from typing import Any, Dict

from core.risk_constants import (
    PA_EXECUTION_NORMAL_MIN_SCORE,
    PA_EXECUTION_T1_MIN_SCORE,
    PA_PULLBACK_WATCH_MIN_SCORE,
)


PA_EXECUTION_POLICY_VERSION = "pa-execution-tier-v1"
HARD_BLOCKED_SETUPS = {"外包K", "交易区间假突破"}


def _score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def classify_price_action_execution(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Return one execution tier without changing the candidate selection result."""
    score = _score(candidate.get("price_action_score"))
    discovery_watch = any(candidate.get(key) for key in (
        "bottom_discovery_watch_only",
        "tv_reversal_watch_only",
        "sector_watch_only",
        "early_value_watch_only",
        "momentum_acceleration_watch_only",
    ))
    plan = candidate.get("pa_trade_plan") or {}
    if not isinstance(plan, dict):
        plan = {}
    action = str(
        candidate.get("pa_trade_action")
        or plan.get("action")
        or ""
    ).upper()
    setup = str(candidate.get("pa_trade_setup") or candidate.get("price_action_pattern") or "")
    pullback_status = str(candidate.get("pa_pullback_status") or "").upper()

    hard_reason = None
    if action == "AVOID":
        hard_reason = "价格行为建议回避"
    elif pullback_status == "INVALIDATED":
        hard_reason = "回踩结构失效"
    elif setup in HARD_BLOCKED_SETUPS:
        hard_reason = f"{setup}结构不进入交易池"
    elif score < PA_PULLBACK_WATCH_MIN_SCORE:
        hard_reason = f"价格行为评分<{PA_PULLBACK_WATCH_MIN_SCORE:.0f}，禁止实盘"

    if discovery_watch:
        hard_reason = None
        tier, label = "DISCOVERY_WATCH", "发现策略观察"
    elif hard_reason:
        tier, label = "HARD_BLOCK", "价格行为硬阻断"
    elif score >= PA_EXECUTION_NORMAL_MIN_SCORE:
        tier, label = "NORMAL", "正常执行复核"
    elif score >= PA_EXECUTION_T1_MIN_SCORE:
        tier, label = "T1_CONFIRM", "次日确认"
    else:
        tier, label = "PULLBACK_WATCH", "回踩观察"
    return {
        "version": PA_EXECUTION_POLICY_VERSION,
        "score": round(score, 1),
        "tier": tier,
        "label": label,
        "hard_blocked": bool(hard_reason),
        "hard_reason": hard_reason,
    }
