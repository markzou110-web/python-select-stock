"""Stable decision labels for scan snapshots and later outcome attribution."""

from typing import Any


def apply_decision_semantics(results: list[dict[str, Any]]) -> None:
    """Separate structural grade, observation state, and executable action.

    This is presentation and audit metadata only; it never changes selection,
    score, or trade eligibility rules.
    """
    for row in results:
        grade = str(row.get("sop_grade") or "UNKNOWN").upper()
        bucket = str(row.get("trade_bucket") or "OBSERVE").upper()
        raw_blockers = row.get("trade_blockers") or []
        if isinstance(raw_blockers, str):
            raw_blockers = [item.strip() for item in raw_blockers.split(";")]
        blockers = [str(item) for item in raw_blockers if str(item)]
        pullback = str(row.get("pa_pullback_status") or "").upper()

        if grade == "A" and bool(row.get("trade_eligible")):
            stage, label, action = "A-TRADE", "A级可交易", "可执行"
        elif grade == "A" and bucket == "EARLY":
            stage, label, action = "A-EARLY", "A级提前复核", "小仓复核"
        elif grade == "A":
            stage, label, action = "A-STRUCTURE", "A级结构", "等待确认"
        else:
            stage, label, action = f"{grade}-STRUCTURE", f"{grade}级结构", "仅观察"

        if pullback == "INVALIDATED":
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
        elif grade == "A":
            lifecycle, confirmation = "CORE_CANDIDATE", "WAIT_PRICE_CONFIRMATION"
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

        row.update({
            "grade_stage": stage,
            "grade_label": label,
            "grade_action": action,
            "grade_reason": "；".join(blockers[:2]) or "结构评级与执行状态已分离记录",
            "decision_lifecycle_state": lifecycle,
            "decision_lifecycle_action": action,
            "confirmation_event_state": confirmation,
            "confirmation_event_reason": "；".join(blockers[:2]) or "等待既有确认条件满足",
            "early_value_transition_state": early_state,
        })
