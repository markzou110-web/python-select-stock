"""Event-driven watch semantics; catalysts never bypass execution gates."""
from datetime import date
from typing import Any, Dict, List

from core.risk_constants import (
    EVENT_TRIAL_MAX_DAILY_PCT,
    EVENT_TRIAL_MIN_RISK_REWARD,
    EVENT_TRIAL_POSITION_PCT,
    EVENT_TRIAL_WEAK_POSITION_PCT,
)


EVENT_MODEL_VERSION = "earnings-catalyst-watch-v1"


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def classify_post_limit_state(row: Dict[str, Any]) -> str:
    status = str(row.get("limit_up_status") or "").upper()
    pullback = str(row.get("pa_pullback_status") or "").upper()
    if pullback == "INVALIDATED":
        return "EVENT_INVALIDATED"
    if status == "SEALED" or _num(row.get("涨幅%")) >= 9.8:
        return "SEALED_UNBUYABLE"
    if pullback == "CONFIRMED" and bool(row.get("pa_volume_confirmed")):
        return "FIRST_PULLBACK_CONFIRMED"
    if (
        _num(row.get("limit_up_streak")) > 0
        or bool(row.get("frozen_confirmation_triggered"))
        or _num(row.get("pct_5d")) >= 15
    ):
        return "WAIT_FIRST_TRADABLE_PULLBACK"
    return "EVENT_DISCOVERY"


def apply_event_catalysts(results: List[Dict[str, Any]], catalyst_map: Dict[str, Dict[str, Any]]) -> None:
    for row in results:
        catalyst = catalyst_map.get(str(row.get("代码") or row.get("code") or "").zfill(6))
        if not catalyst:
            continue
        growth_low = _num(catalyst.get("profit_growth_low"))
        if str(catalyst.get("event_type")) != "EARNINGS_SURPRISE" or growth_low < 50:
            continue
        published = catalyst.get("published_at")
        row["event_catalyst"] = {
            "event_type": catalyst.get("event_type"),
            "title": catalyst.get("title"),
            "published_at": str(published) if published else None,
            "profit_growth_low": growth_low,
            "profit_growth_high": _num(catalyst.get("profit_growth_high")),
            "source_url": catalyst.get("source_url"),
            "verified": bool(catalyst.get("verified")),
        }
        row["event_driven_candidate"] = True
        row["event_model_version"] = EVENT_MODEL_VERSION
        row["event_post_limit_state"] = classify_post_limit_state(row)
        row["event_alert_tier"] = "STRONG_WATCH"
        row["event_health_scope"] = "EARNINGS_SURPRISE_SHADOW"
        row["trade_eligible"] = False
        row["trade_bucket"] = "OBSERVE"
        blockers = list(row.get("trade_blockers") or [])
        if row["event_post_limit_state"] == "SEALED_UNBUYABLE":
            blockers.append("重大业绩催化但涨停不可成交，等待首次可交易回踩")
        elif row["event_post_limit_state"] == "EVENT_INVALIDATED":
            blockers.append("重大业绩催化仍在，但价格结构已失效")
        else:
            blockers.append("事件策略样本未成熟，仅强势观察")
        row["trade_blockers"] = list(dict.fromkeys(blockers))


_EVENT_SOFT_BLOCKERS = (
    "市场退潮，暂停新增仓位",
    "板块退潮，暂停新增仓位",
    "板块强度不足，降级观察",
    "板块联动<70，降级观察",
    "综合机会分<60，暂不交易",
    "策略近期负期望，自动暂停",
    "事件策略样本未成熟，仅强势观察",
)

_EVENT_HARD_BLOCKER_MARKERS = (
    "价格行为建议回避", "回踩结构失效", "冲高回落", "涨停/近涨停", "高开",
    "量能未确认", "未站上确认价", "未站稳", "交易计划未确认", "关键点时字段不完整",
    "仅供研究", "风险收益",
)


def finalize_event_trade_state(results: List[Dict[str, Any]]) -> None:
    """Open a small trial only after a verified event's first pullback fully confirms."""
    for row in results:
        if not row.get("event_driven_candidate"):
            continue
        if classify_post_limit_state(row) != "FIRST_PULLBACK_CONFIRMED":
            row["trade_eligible"] = False
            row["trade_bucket"] = "OBSERVE"
            continue
        blockers = [str(item) for item in (row.get("trade_blockers") or [])]
        remaining = [
            item for item in blockers
            if not any(soft in item for soft in _EVENT_SOFT_BLOCKERS)
        ]
        hard_blocked = any(
            marker in item for item in remaining for marker in _EVENT_HARD_BLOCKER_MARKERS
        )
        risk_reward = _num(row.get("pa_risk_reward"))
        daily_pct = _num(row.get("涨幅%") or row.get("pct_chg"))
        status = str(row.get("limit_up_status") or "").upper()
        if (
            hard_blocked
            or remaining
            or risk_reward < EVENT_TRIAL_MIN_RISK_REWARD
            or daily_pct > EVENT_TRIAL_MAX_DAILY_PCT
            or status == "SEALED"
        ):
            row["trade_eligible"] = False
            row["trade_bucket"] = "OBSERVE"
            if risk_reward < EVENT_TRIAL_MIN_RISK_REWARD:
                remaining.append(f"事件试仓盈亏比<{EVENT_TRIAL_MIN_RISK_REWARD:g}")
            if daily_pct > EVENT_TRIAL_MAX_DAILY_PCT:
                remaining.append(f"事件试仓当日涨幅>{EVENT_TRIAL_MAX_DAILY_PCT:g}%，禁止追高")
            row["trade_blockers"] = list(dict.fromkeys(remaining))
            continue
        weak_context = str(row.get("market_sentiment_stage") or "").upper() in {"RETREAT", "ICE"}
        position_pct = EVENT_TRIAL_WEAK_POSITION_PCT if weak_context else EVENT_TRIAL_POSITION_PCT
        row["event_post_limit_state"] = "FIRST_PULLBACK_CONFIRMED"
        row["event_alert_tier"] = "EVENT_TRIAL"
        row["event_trial_trade"] = True
        row["trade_eligible"] = True
        row["trade_bucket"] = "TRADE"
        row["trade_blockers"] = []
        row["position_plan"] = {
            "label": "事件回踩试仓",
            "initial_position_pct": position_pct,
            "max_position_pct": position_pct,
        }
        row["execution_instruction"] = f"事件首次回踩确认，可试仓{position_pct}%；严格执行失效价"
