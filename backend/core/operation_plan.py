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


def evaluate_operation_trigger(current_price: float, plan: Dict[str, Any]) -> Dict[str, Any]:
    current = safe_num(current_price)
    trigger = safe_num(plan.get("add_trigger_price"))
    guard = safe_num(plan.get("add_guard_price"))
    active_stop = safe_num(plan.get("active_stop_price"))
    structure_stop = safe_num(plan.get("structure_stop_price"))
    if current <= 0:
        return {"triggered": False, "level": "none", "action": "暂无有效现价", "price": current}

    if structure_stop > 0 and current <= structure_stop:
        return {
            "triggered": True,
            "level": "critical",
            "priority": "P0",
            "action": f"现价 {current:.2f} 跌破结构失效线 {structure_stop:.2f}，优先退出复核",
            "price": current,
            "threshold": round(structure_stop, 2),
            "kind": "STRUCTURE_EXIT",
        }
    if active_stop > 0 and current <= active_stop:
        return {
            "triggered": True,
            "level": "warning",
            "priority": "P1",
            "action": f"现价 {current:.2f} 跌破减仓线 {active_stop:.2f}，减仓或收紧风控",
            "price": current,
            "threshold": round(active_stop, 2),
            "kind": "REDUCE",
        }
    if guard > 0 and current <= guard:
        return {
            "triggered": True,
            "level": "notice",
            "priority": "P2",
            "action": f"现价 {current:.2f} 跌破加仓撤退线 {guard:.2f}，撤回加仓计划",
            "price": current,
            "threshold": round(guard, 2),
            "kind": "CANCEL_ADD",
        }
    if trigger > 0 and current >= trigger:
        return {
            "triggered": True,
            "level": "opportunity",
            "priority": "P1",
            "action": f"现价 {current:.2f} 突破加仓触发线 {trigger:.2f}，等待量能与收盘确认后小幅加仓",
            "price": current,
            "threshold": round(trigger, 2),
            "kind": "ADD_TRIGGER",
        }
    return {
        "triggered": False,
        "level": "none",
        "priority": "P3",
        "action": "未触发操作价位",
        "price": current,
        "kind": "HOLD",
    }


def alert_priority(level: str = "", kind: str = "") -> Dict[str, Any]:
    if kind == "STRUCTURE_EXIT" or level == "critical":
        return {"priority": "P0", "label": "必须立即处理", "emoji": "🚨"}
    if kind in {"REDUCE", "ADD_TRIGGER"} or level in {"warning", "opportunity"}:
        return {"priority": "P1", "label": "需要盘中决策", "emoji": "⚠️" if kind == "REDUCE" else "🟢"}
    if kind == "CANCEL_ADD" or level == "notice":
        return {"priority": "P2", "label": "观察提醒", "emoji": "🟠"}
    return {"priority": "P3", "label": "复盘信息", "emoji": "ℹ️"}


def pre_trade_check(
    current_price: float,
    plan: Dict[str, Any],
    market_status: str = "",
    sector_phase: str = "",
    volume_confirmed: bool = False,
    close_confirmed: bool = False,
    high_open_pct: float = 0.0,
    pullback_warning: bool = False,
    portfolio_warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    current = safe_num(current_price)
    trigger = safe_num(plan.get("add_trigger_price") or plan.get("trigger_price"))
    guard = safe_num(plan.get("add_guard_price") or plan.get("guard_price"))
    blockers: List[str] = []
    warnings = portfolio_warnings or []

    if trigger > 0 and current < trigger:
        blockers.append(f"未站上触发价 {trigger:.2f}")
    if guard > 0 and current <= guard:
        blockers.append(f"跌破撤退线 {guard:.2f}")
    if high_open_pct > 3:
        blockers.append(f"高开 {high_open_pct:.1f}% 超过追价阈值")
    if pullback_warning:
        blockers.append("出现冲高回落警告")
    if market_status in {"DEFENSIVE", "CRITICAL"}:
        blockers.append(f"市场处于{market_status}模式")
    if sector_phase in {"SECTOR_FADE"}:
        blockers.append("板块退潮")
    if not volume_confirmed:
        blockers.append("量能未确认")
    if not close_confirmed:
        blockers.append("收盘/站稳未确认")
    if warnings:
        blockers.extend(warnings[:2])

    passed = len(blockers) == 0
    action = "允许小仓执行" if passed else "禁止买入/加仓"
    if not passed and trigger > 0 and guard > 0 and current > guard:
        action = "继续观察，等待价量收齐"
    return {
        "passed": passed,
        "action": action,
        "blockers": blockers,
        "trigger_price": round(trigger, 2) if trigger > 0 else None,
        "guard_price": round(guard, 2) if guard > 0 else None,
    }
