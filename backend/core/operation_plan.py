from datetime import datetime
from typing import Any, Dict, List, Optional


def safe_num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def price_instruction(
    trigger: float = 0.0,
    guard: float = 0.0,
    active_stop: float = 0.0,
    structure_stop: float = 0.0,
    confirmed: bool = False,
    profitable: bool = False,
    trigger_action: Optional[str] = None,
) -> str:
    trigger = safe_num(trigger)
    guard = safe_num(guard)
    active_stop = safe_num(active_stop)
    structure_stop = safe_num(structure_stop)
    actions: List[str] = []

    if trigger > 0:
        action = trigger_action or ("可小幅加仓" if confirmed and profitable else "只确认不追，等价量收齐")
        actions.append(f">{trigger:.2f}: {action}")
    if trigger > 0 and guard > 0 and guard < trigger:
        actions.append(f"{guard:.2f}-{trigger:.2f}: 持有观察，不加仓")
    if guard > 0:
        actions.append(f"<{guard:.2f}: 撤回加仓计划")
    if active_stop > 0:
        actions.append(f"<{active_stop:.2f}: 减仓/收紧风控")
    if structure_stop > 0 and abs(structure_stop - active_stop) > 0.001:
        actions.append(f"<{structure_stop:.2f}: 结构失效，退出复核")
    return "；".join(actions)


def position_size_advice(entry: float, guard: float, trigger: float, confirmed: bool, portfolio_warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    entry = safe_num(entry)
    guard = safe_num(guard)
    trigger = safe_num(trigger)
    warnings = portfolio_warnings or []
    risk_pct = (trigger - guard) / trigger * 100 if trigger > 0 and guard > 0 else 0
    if warnings:
        return {"label": "不加仓", "max_add_pct": 0, "risk_pct": round(risk_pct, 2), "reason": "组合风险预算触发"}
    if not confirmed:
        return {"label": "等待确认", "max_add_pct": 0, "risk_pct": round(risk_pct, 2), "reason": "价量收未全部确认"}
    if risk_pct <= 2.5:
        return {"label": "确认加仓", "max_add_pct": 30, "risk_pct": round(risk_pct, 2), "reason": "加仓撤退线较近"}
    if risk_pct <= 5:
        return {"label": "小幅加仓", "max_add_pct": 20, "risk_pct": round(risk_pct, 2), "reason": "风险距离适中"}
    return {"label": "轻仓试错", "max_add_pct": 10, "risk_pct": round(risk_pct, 2), "reason": "风险距离偏宽"}


def position_health_score(
    pl_pct: float,
    stop_buffer_pct: float,
    trap_risk: float = 0.0,
    trend_damage: str = "",
    volume_pattern: str = "",
    confirmed_breakout: bool = False,
) -> Dict[str, Any]:
    score = 60
    notes: List[str] = []
    if pl_pct > 5:
        score += 12
        notes.append("已有盈利垫")
    elif pl_pct < 0:
        score -= 12
        notes.append("持仓未盈利")
    if stop_buffer_pct >= 6:
        score += 10
        notes.append("风控缓冲充足")
    elif 0 < stop_buffer_pct < 2:
        score -= 18
        notes.append("贴近风控线")
    if confirmed_breakout:
        score += 12
        notes.append("放量突破确认")
    if trap_risk >= 75:
        score -= 18
        notes.append("陷阱风险高")
    if trend_damage in {"跌破EMA20", "跌破EMA60", "短线低点破坏"}:
        score -= 18
        notes.append(trend_damage)
    if volume_pattern in {"放量失败突破", "缩量阴跌", "放量破位"}:
        score -= 12
        notes.append(volume_pattern)
    score = int(max(0, min(100, score)))
    if score >= 75:
        label = "健康持有"
    elif score >= 55:
        label = "观察持有"
    elif score >= 40:
        label = "防守持有"
    else:
        label = "优先处理"
    return {"score": score, "label": label, "notes": notes}


def watch_instruction(item: Dict[str, Any]) -> Dict[str, Any]:
    current = safe_num(item.get("current_price"))
    target = safe_num(item.get("target_price"))
    stop = safe_num(item.get("stop_price"))
    watch_price = safe_num(item.get("watch_price"))
    guard = stop or (watch_price * 0.95 if watch_price > 0 else 0)
    if target > 0:
        instruction = price_instruction(
            trigger=target,
            guard=max(guard, target * 0.985) if guard > 0 else target * 0.985,
            active_stop=guard,
            structure_stop=stop,
            confirmed=current >= target,
            profitable=current > watch_price if watch_price > 0 else False,
            trigger_action="可转拟合实盘/小仓试买" if current >= target else "等放量站稳再试",
        )
    else:
        instruction = price_instruction(active_stop=guard, structure_stop=stop)
    return {
        "trigger_price": round(target, 2) if target > 0 else None,
        "guard_price": round(guard, 2) if guard > 0 else None,
        "instruction": instruction,
    }


def watch_exit_decision(item: Dict[str, Any], max_watch_days: int = 15) -> Dict[str, Any]:
    status = item.get("status") or "WATCHING"
    if status != "WATCHING":
        return {"should_exit": False, "reason": ""}
    current = safe_num(item.get("current_price"))
    stop = safe_num(item.get("stop_price"))
    decision = item.get("computed_decision") or item.get("watch_decision") or ""
    if stop > 0 and current > 0 and current <= stop:
        return {"should_exit": True, "reason": f"跌破失效价 {stop:.2f}"}
    if decision == "INVALIDATE":
        return {"should_exit": True, "reason": item.get("computed_action") or item.get("watch_action") or "观察条件失效"}
    created = item.get("created_at")
    try:
        created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00")).replace(tzinfo=None)
        age_days = (datetime.now() - created_dt).days
    except Exception:
        age_days = 0
    if age_days >= max_watch_days and decision not in {"PROMOTE", "NEAR_TRIGGER"}:
        return {"should_exit": True, "reason": f"观察 {age_days} 天未触发，自动淘汰"}
    return {"should_exit": False, "reason": ""}


def operation_bands(trigger: float = 0.0, guard: float = 0.0, active_stop: float = 0.0, structure_stop: float = 0.0) -> List[Dict[str, Any]]:
    rows = []
    if trigger > 0:
        rows.append({"price": round(trigger, 2), "label": "加仓触发线", "color": "#16a34a", "action": f">{trigger:.2f} 可按指令加仓"})
    if guard > 0:
        rows.append({"price": round(guard, 2), "label": "加仓撤退线", "color": "#d97706", "action": f"<{guard:.2f} 撤回加仓计划"})
    if active_stop > 0:
        rows.append({"price": round(active_stop, 2), "label": "减仓线", "color": "#e11d48", "action": f"<{active_stop:.2f} 减仓/收紧风控"})
    if structure_stop > 0 and abs(structure_stop - active_stop) > 0.001:
        rows.append({"price": round(structure_stop, 2), "label": "退出线", "color": "#2563eb", "action": f"<{structure_stop:.2f} 结构失效"})
    return rows
