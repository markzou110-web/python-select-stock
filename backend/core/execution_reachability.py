"""Post-selection execution reachability and shadow-only strong exceptions."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable

from core.risk_constants import (
    LIMIT_PRICE_TOLERANCE,
    STRONG_EXCEPTION_MIN_OPPORTUNITY_SCORE,
    STRONG_EXCEPTION_MIN_RISK_REWARD,
)
from core.execution_insights import get_active_execution_plan


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _limit_ratio(code: str, name: str) -> Decimal:
    if "ST" in name.upper():
        return Decimal("0.05")
    if code.startswith(("300", "301", "688")):
        return Decimal("0.20")
    if code.startswith(("4", "8", "92")):
        return Decimal("0.30")
    return Decimal("0.10")


def estimate_limit_up_price(candidate: Dict[str, Any]) -> float | None:
    """Estimate the current-session upper price from point-in-time price and pct."""
    price = _number(candidate.get("现价") or candidate.get("price"))
    pct = candidate.get("涨幅%") if candidate.get("涨幅%") is not None else candidate.get("pct")
    try:
        pct_value = Decimal(str(pct))
        current = Decimal(str(price))
    except (InvalidOperation, TypeError):
        return None
    if price is None or pct_value <= Decimal("-99"):
        return None
    previous_close = current / (Decimal("1") + pct_value / Decimal("100"))
    code = str(candidate.get("代码") or candidate.get("code") or "")
    name = str(candidate.get("名称") or candidate.get("name") or "")
    limit_price = previous_close * (Decimal("1") + _limit_ratio(code, name))
    return float(limit_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _strong_exception_shadow(candidate: Dict[str, Any], reachability: str) -> tuple[bool, str]:
    blockers = [str(item) for item in (candidate.get("trade_blockers") or [])]
    blocker_text = "；".join(blockers)
    if candidate.get("trade_eligible"):
        return False, "生产规则已可交易，无需影子例外"
    if reachability != "REACHABLE_TODAY":
        return False, "确认价今日不可达或涨停状态不可成交"
    if not any(marker in blocker_text for marker in ("市场退潮", "策略近期负期望")):
        return False, "不是市场或健康度门禁导致的候选"
    hard_markers = (
        "异常价格", "结构失效", "禁止实盘", "板块退潮", "涨停/近涨停",
        "关键点时字段不完整", "交易计划未确认", "未站上确认价",
    )
    if any(marker in blocker_text for marker in hard_markers):
        return False, "仍存在价格、板块或数据硬阻断"
    mainline = str(candidate.get("sector_mainline") or "").upper()
    phase = str(candidate.get("sector_phase") or "").upper()
    role = str(candidate.get("sector_role") or "").upper()
    if mainline not in {"MAIN", "ACTIVE", "LEADING"} and phase != "SECTOR_CONFIRM":
        return False, "不属于确认中的市场主线"
    if role not in {"CORE", "LEADER"}:
        return False, "不是板块核心或龙头"
    volume_confirmed = bool(candidate.get("pa_volume_confirmed")) or candidate.get("pa_volume_pattern") == "放量突破"
    if not volume_confirmed:
        return False, "量能尚未确认"
    opportunity = _number(candidate.get("trade_opportunity_score")) or 0
    risk_reward = _number(candidate.get("pa_risk_reward")) or _number((candidate.get("execution_rr") or {}).get("execution_rr")) or 0
    if opportunity < STRONG_EXCEPTION_MIN_OPPORTUNITY_SCORE:
        return False, "综合机会分不足"
    if risk_reward < STRONG_EXCEPTION_MIN_RISK_REWARD:
        return False, "执行盈亏比不足"
    return True, "主线核心、量价确认且仅被市场/健康度门禁拦截；仅做SHADOW反事实"


def apply_execution_reachability(candidates: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Attach reachability metadata; may only lower, never promote, trade permission."""
    summary = {"processed": 0, "unreachable": 0, "limit_locked": 0, "strong_exception_shadow": 0}
    for candidate in candidates:
        summary["processed"] += 1
        price = _number(candidate.get("现价") or candidate.get("price"))
        entry = _number(get_active_execution_plan(candidate)["entry"])
        limit_price = estimate_limit_up_price(candidate)
        reachability = "UNKNOWN"
        reason = "缺少现价、涨跌幅或确认价"
        if price and entry and limit_price:
            if entry > limit_price + 1e-9:
                reachability = "UNREACHABLE_TODAY"
                reason = f"确认价{entry:g}高于当日涨停价{limit_price:g}"
                summary["unreachable"] += 1
            elif price >= limit_price - LIMIT_PRICE_TOLERANCE and entry >= price:
                reachability = "LIMIT_LOCKED_WAIT_NEXT_SESSION"
                reason = "已到涨停或近涨停状态，等待下一交易日重新确认"
                summary["limit_locked"] += 1
            else:
                reachability = "REACHABLE_TODAY"
                reason = "确认价处于当日可达区间"
        candidate["confirmation_reachability"] = reachability
        candidate["confirmation_limit_price"] = limit_price
        candidate["confirmation_reachability_reason"] = reason

        if reachability in {"UNREACHABLE_TODAY", "LIMIT_LOCKED_WAIT_NEXT_SESSION"} and candidate.get("trade_eligible"):
            candidate["trade_eligible"] = False
            candidate["trade_bucket"] = "OBSERVE"
            candidate["trade_execution_policy"] = "NEXT_SESSION_REVIEW"
            blockers = list(candidate.get("trade_blockers") or [])
            blockers.append("确认价今日不可达，等待下一交易日重新确认")
            candidate["trade_blockers"] = list(dict.fromkeys(blockers))

        shadow, shadow_reason = _strong_exception_shadow(candidate, reachability)
        candidate["strong_exception_shadow"] = shadow
        candidate["strong_exception_shadow_reason"] = shadow_reason
        if shadow:
            candidate["strong_exception_shadow_entry"] = entry
            summary["strong_exception_shadow"] += 1

        if candidate.get("trade_eligible") and str(candidate.get("trade_bucket")) == "TRADE":
            candidate["execution_review_state"] = "TRADE"
        elif reachability in {"UNREACHABLE_TODAY", "LIMIT_LOCKED_WAIT_NEXT_SESSION"}:
            candidate["execution_review_state"] = "NEXT_DAY_REVIEW"
        elif shadow or float(candidate.get("涨幅%") or candidate.get("pct") or 0) >= 7:
            candidate["execution_review_state"] = "STRONG_WATCH"
        else:
            candidate["execution_review_state"] = "OBSERVE"
    return summary
