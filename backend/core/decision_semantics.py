"""Stable decision labels for scan snapshots and later outcome attribution."""

from typing import Any


def apply_decision_semantics(results: list[dict[str, Any]]) -> None:
    """Separate observation state and executable action without letter grades.

    This is presentation and audit metadata only; it never changes selection,
    score, or trade eligibility rules.
    """
    for row in results:
        bucket = str(row.get("trade_bucket") or "OBSERVE").upper()
        raw_blockers = row.get("trade_blockers") or []
        if isinstance(raw_blockers, str):
            raw_blockers = [item.strip() for item in raw_blockers.split(";")]
        blockers = [str(item) for item in raw_blockers if str(item)]
        pullback = str(row.get("pa_pullback_status") or "").upper()

        if row.get("a_eod_controlled_trial") and bool(row.get("trade_eligible")) and bucket == "TRADE":
            stage, label, action = "EOD-CONTROLLED-TRIAL", "尾盘受控试仓", "可执行"
        elif row.get("a_minus_trial") and bool(row.get("trade_eligible")) and bucket == "TRADE":
            stage, label, action = "EARLY-CONTROLLED-TRIAL", "早期受控试仓", "可执行"
        elif bool(row.get("trade_eligible")) and bucket == "TRADE":
            stage, label, action = "TRADE", "可交易候选", "可执行"
        elif bucket == "EARLY":
            stage, label, action = "EARLY-REVIEW", "提前复核", "小仓复核"
        elif bucket == "BLOCK":
            stage, label, action = "BLOCKED", "禁止交易", "仅复盘"
        else:
            stage, label, action = "OBSERVE", "结构观察", "仅观察"

        if row.get("bottom_discovery_watch_only"):
            bottom_stage = str(row.get("bottom_discovery_stage") or "B0_BASE")
            lifecycle = "BOTTOM_REVERSAL_FOUND" if bottom_stage == "B1_REVERSAL" else "BOTTOM_BASE_FOUND"
            confirmation = "WAIT_SECTOR_AND_PRICE_CONFIRMATION"
        elif pullback == "INVALIDATED":
            lifecycle, confirmation = "INVALIDATED", "INVALIDATED"
        elif bool(row.get("trade_eligible")):
            lifecycle, confirmation = "ENTRY_CONFIRMED", "CONFIRMED"
        elif row.get("frozen_confirmation_triggered") and float(row.get("frozen_entry_extension_pct") or 0) > 3:
            lifecycle, confirmation = "PULLBACK_VALID", "TRIGGERED_EXTENDED_WAIT_PULLBACK"
        elif row.get("frozen_confirmation_triggered"):
            lifecycle, confirmation = "PULLBACK_VALID", "PRIOR_PLAN_TRIGGERED_WAIT_EXECUTION"
        elif row.get("sector_watch_only"):
            lifecycle, confirmation = "SECTOR_FOUND", "WAIT_SECTOR_CONFIRMATION"
        elif bucket == "EARLY":
            lifecycle, confirmation = "PULLBACK_VALID", "WAIT_PRICE_CONFIRMATION"
        else:
            lifecycle, confirmation = "WAITING_CONFIRMATION", "NOT_READY"

        early_state = None
        if str(row.get("strategy_type") or "") == "early_value":
            if bool(row.get("trade_eligible")):
                early_state = "ENTRY_CONFIRMED"
            elif row.get("early_value_sector_pending"):
                early_state = "TECHNICAL_MATCH_SECTOR_PENDING"
            elif row.get("early_value_sector_confirmed"):
                early_state = "SECTOR_CONFIRMED_WAIT_PRICE"
            else:
                early_state = "TECHNICAL_MATCH"

        bottom_state = None
        if str(row.get("strategy_type") or "") == "bottom_discovery":
            bottom_state = str(row.get("bottom_discovery_stage") or "B0_BASE")

        row.update({
            "decision_stage": stage,
            "decision_label": label,
            "decision_action": action,
            "decision_reason": "；".join(blockers[:2]) or "按策略信号与风控状态生成",
            "decision_lifecycle_state": lifecycle,
            "decision_lifecycle_action": action,
            "confirmation_event_state": confirmation,
            "confirmation_event_reason": "；".join(blockers[:2]) or "等待既有确认条件满足",
            "early_value_transition_state": early_state,
            "bottom_discovery_transition_state": bottom_state,
        })
