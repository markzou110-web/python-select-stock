"""Stable execution explanations derived from existing scan fields."""
from typing import Any, Dict, List


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def get_active_execution_plan(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return the single plan downstream execution must use."""
    frozen = bool(row.get("execution_plan_frozen")) and _num(row.get("frozen_confirmation_price")) > 0
    if frozen:
        return {
            "entry": _num(row.get("frozen_confirmation_price")),
            "stop": _num(row.get("frozen_stop_price") or row.get("pa_stop_price") or row.get("stop_price")),
            "close_guard": _num(
                row.get("frozen_close_guard_price")
                or row.get("frozen_stop_price")
                or row.get("pa_stop_price")
                or row.get("stop_price")
            ),
            "target": _num(row.get("frozen_target_price") or row.get("pa_target_price") or row.get("target_price")),
            "source": "FROZEN",
        }
    return {
        "entry": _num(row.get("pa_entry_price") or row.get("entry_price")),
        "stop": _num(row.get("pa_stop_price") or row.get("stop_price") or row.get("plan_stop_price")),
        "close_guard": _num(
            row.get("pa_close_guard_price")
            or row.get("pa_stop_price")
            or row.get("stop_price")
            or row.get("plan_stop_price")
        ),
        "target": _num(row.get("pa_target_price") or row.get("target_price")),
        "source": "GENERATED",
    }


def build_execution_rr(row: Dict[str, Any]) -> Dict[str, Any]:
    plan = get_active_execution_plan(row)
    entry, stop, target = plan["entry"], plan["stop"], plan["target"]
    current = _num(row.get("现价") or row.get("price"))
    planned = (target - entry) / (entry - stop) if entry > stop > 0 and target > entry else 0
    current_rr = (target - current) / (current - stop) if current > stop > 0 and target > current else 0
    space = _num(row.get("pa_actual_space_rr"))
    execution = current_rr if current > 0 else planned
    return {
        "planned_rr": round(planned, 2), "current_rr": round(current_rr, 2),
        "space_rr": round(space, 2), "execution_rr": round(execution, 2),
        "price_basis": round(current or entry, 2), "plan_source": plan["source"],
    }


def build_frozen_plan_state(row: Dict[str, Any]) -> Dict[str, Any]:
    pullback = str(row.get("pa_pullback_status") or "").upper()
    extension = _num(row.get("frozen_entry_extension_pct"))
    if pullback == "INVALIDATED":
        state = "INVALIDATED"
    elif row.get("execution_plan_frozen") and row.get("frozen_confirmation_triggered") and extension > 3:
        state = "EXTENDED_WAIT_PULLBACK"
    elif pullback == "CONFIRMED" and row.get("pa_volume_confirmed"):
        state = "NEW_CONFIRMATION_ACTIVE"
    elif row.get("frozen_confirmation_triggered"):
        state = "FROZEN_TRIGGERED"
    elif row.get("execution_plan_frozen"):
        state = "FROZEN_ACTIVE"
    else:
        state = "GENERATED_PLAN"
    plan = get_active_execution_plan(row)
    active_confirmation = plan["entry"]
    return {
        "state": state,
        "active_confirmation_price": round(active_confirmation, 2) if active_confirmation else None,
        "prior_confirmation_price": row.get("frozen_confirmation_price"),
        "generated_confirmation_price": row.get("generated_confirmation_price"),
        "active_plan_source": plan["source"],
    }


def build_distance_to_trade(row: Dict[str, Any]) -> Dict[str, Any]:
    blockers = [str(item) for item in (row.get("trade_blockers") or [])]
    steps: List[str] = []
    if any("涨停/近涨停" in item for item in blockers):
        steps.append("脱离涨停/近涨停状态，不追高")
    if any("距冻结确认价" in item for item in blockers):
        steps.append("回踩至冻结确认价3%以内")
    if any("确认价" in item for item in blockers):
        steps.append("站稳当前有效确认价")
    if any("次一交易日" in item for item in blockers):
        steps.append("等待次一交易日重新确认")
    if any("量能" in item or "换手" in item for item in blockers):
        steps.append("完成量能确认")
    if any("策略近期负期望" in item for item in blockers):
        steps.append("等待策略健康度恢复")
    if any("板块" in item for item in blockers):
        steps.append("等待板块强度和联动恢复")
    if any("结构失效" in item or "回避" in item for item in blockers):
        steps = ["当前结构失效，需形成新的交易计划"]
    return {
        "remaining_count": len(list(dict.fromkeys(steps))),
        "steps": list(dict.fromkeys(steps))[:4],
        "invalidation_price": row.get("pa_stop_price") or row.get("plan_stop_price"),
    }
