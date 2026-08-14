"""Live shadow classification for the confirmed three-route strategy document."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text

from core.strategy_research_protocol import breadth_axis, route_permissions


SHADOW_POLICY_VERSION = "new-strategy-shadow-v1"
REPAIR_STAGES = frozenset({"V_REPAIR"})


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _index_axis(regime: dict[str, Any]) -> str:
    indices = regime.get("indices") or {}
    sh = str((indices.get("上证") or {}).get("trend") or "").upper()
    cyb = str((indices.get("创业") or indices.get("创业板") or {}).get("trend") or "").upper()
    if sh == "BULL" and cyb == "BULL":
        return "T2"
    if sh == "BEAR" and cyb == "BEAR":
        return "T0"
    if sh in {"BULL", "BEAR"} and cyb in {"BULL", "BEAR"}:
        return "T1"
    return "UNKNOWN"


def build_shadow_market_context(
    regime: dict[str, Any],
    breadth_stage: str,
    *,
    previous_route_a_candidate_permission: str | None,
    completed_day: bool = True,
) -> dict[str, Any]:
    """Build today's fail-closed market state and both research hysteresis arms."""
    index_axis = _index_axis(regime)
    stage = str(breadth_stage or "UNKNOWN").upper()
    width_axis = breadth_axis(stage)
    permissions = route_permissions(index_axis, width_axis)
    candidate_permission = permissions["route_a"]
    permission_1d = candidate_permission
    permission_2d = candidate_permission
    if candidate_permission == "CONFIRM":
        if completed_day:
            permission_2d = (
                "CONFIRM"
                if previous_route_a_candidate_permission == "CONFIRM"
                else "OBSERVE"
            )
        else:
            permission_1d = "PENDING_CLOSE"
            permission_2d = "PENDING_CLOSE"
    return {
        "completed_day": completed_day,
        "index_axis": index_axis,
        "breadth_axis": width_axis,
        "breadth_stage": stage,
        "route_a_candidate_permission": candidate_permission,
        "route_a_permission_1d": permission_1d,
        "route_a_permission_2d": permission_2d,
        "route_b_permission": permissions["route_b"],
        "route_c_permission": permissions["route_c"],
        "route_c_market_watch": (
            stage in REPAIR_STAGES
            or permissions["route_c"] in {"SHADOW_REPAIR", "OBSERVE_REPAIR_ORIGIN"}
        ),
        "regime_status": str(regime.get("status") or "UNKNOWN"),
        "regime_desc": str(regime.get("desc") or ""),
    }


def _is_strict_dual(candidate: dict[str, Any]) -> bool:
    return (
        str(candidate.get("tv_match") or "") == "双命中"
        or (
            str(candidate.get("tv_ma_signal") or "") == "B共振"
            and str(candidate.get("tv_zp_signal") or "") == "long"
        )
    )


def _shadow_plan_id(data_date: str, route: str, code: str) -> str:
    digest = hashlib.sha256(
        f"{SHADOW_POLICY_VERSION}|{data_date}|{route}|{code}".encode("utf-8")
    ).hexdigest()[:16]
    return f"SHADOW-{digest}"


def classify_shadow_candidates(
    source_candidates: list[dict[str, Any]],
    market_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Classify discovery results while forcibly removing all execution permission."""
    classified: list[dict[str, Any]] = []
    for source in source_candidates or []:
        candidate = dict(source)
        code = str(candidate.get("代码") or candidate.get("code") or "")
        data_date = str(candidate.get("data_date") or date.today().isoformat())[:10]
        pct_5d = _number(candidate.get("pct_5d"))
        pa_score = _number(candidate.get("price_action_score"))
        action = str(
            (candidate.get("pa_trade_plan") or {}).get("action")
            or candidate.get("pa_trade_action")
            or "WAIT"
        ).upper()
        entry = _number(candidate.get("pa_entry_price") or candidate.get("entry_price"))
        stop = _number(candidate.get("pa_stop_price") or candidate.get("stop_price"))
        strict_dual = _is_strict_dual(candidate)
        source_trade_eligible = bool(candidate.get("trade_eligible"))
        blockers = ["新策略历史证据未达到E3"]
        hard_blockers: list[str] = []

        if not strict_dual:
            route = "DISCOVERY"
            state = "WAIT_STRICT_DUAL"
            instruction = "仅发现观察；等待均线B与TV-ZP同时命中"
            hard_blockers.append("未形成严格双策略共振")
        elif pct_5d > 15:
            route = "B"
            state = "OVEREXTENDED_WATCH"
            instruction = "等待1～3日回踩确认；禁止立即追价"
            hard_blockers.append("5日涨幅>15%，禁止立即追价")
            if market_context.get("route_b_permission") != "SHADOW_PULLBACK":
                hard_blockers.append("市场双轴未进入路线B影子回踩状态")
            if action == "AVOID":
                hard_blockers.append("PA行动为AVOID")
                state = "BLOCKED_SHADOW"
            if entry <= 0 or stop <= 0 or stop >= entry:
                hard_blockers.append("确认价或止损价无效")
                state = "BLOCKED_SHADOW"
        else:
            route = "A"
            state = "A_CONFIRMATION_WATCH"
            instruction = "观察T+1确认价触发；当前不可交易"
            if action == "AVOID":
                hard_blockers.append("PA行动为AVOID")
            if pa_score < 55:
                hard_blockers.append("PA分数低于55")
            if entry <= 0 or stop <= 0 or stop >= entry:
                hard_blockers.append("确认价或止损价无效")
            if market_context.get("route_a_permission_1d") != "CONFIRM":
                hard_blockers.append("市场双轴未允许路线A确认")
            if hard_blockers:
                state = "BLOCKED_SHADOW"

        blockers.extend(hard_blockers)
        candidate.update(
            {
                "source_trade_eligible": source_trade_eligible,
                "source_trade_bucket": candidate.get("trade_bucket"),
                "trade_eligible": False,
                "trade_bucket": "SHADOW",
                "trade_execution_policy": "NEW_STRATEGY_SHADOW_ONLY",
                "requires_bark_confirmation": True,
                "shadow_policy_version": SHADOW_POLICY_VERSION,
                "shadow_plan_id": _shadow_plan_id(data_date, route, code),
                "shadow_route": route,
                "shadow_state": state,
                "shadow_instruction": instruction,
                "shadow_blockers": blockers,
                "trade_blockers": blockers,
                "shadow_market": dict(market_context),
            }
        )
        classified.append(candidate)

    route_order = {"A": 0, "B": 1, "DISCOVERY": 2}
    return sorted(
        classified,
        key=lambda item: (
            route_order.get(str(item.get("shadow_route")), 9),
            -_number(item.get("price_action_score")),
            str(item.get("代码") or item.get("code") or ""),
        ),
    )


def _breadth_stage_from_live_context(
    source_candidates: list[dict[str, Any]],
    regime: dict[str, Any],
) -> tuple[str, str]:
    for candidate in source_candidates or []:
        stage = candidate.get("market_sentiment_stage")
        if stage:
            return str(stage).upper(), str(candidate.get("data_date") or date.today())[:10]
    try:
        from core.data import get_market_snapshot, snapshot_data_date
        from core.db import get_db_engine
        from core.decision_layer import (
            build_market_decision_context,
            load_market_cycle_history,
        )

        snapshot = get_market_snapshot()
        data_date = snapshot_data_date(snapshot) or date.today().isoformat()
        engine = get_db_engine()
        context = build_market_decision_context(
            snapshot,
            regime,
            cycle_history=load_market_cycle_history(engine, days=10),
            data_date=data_date,
        )
        return str(context.get("market_sentiment_stage") or "UNKNOWN"), data_date
    except Exception:
        return "UNKNOWN", date.today().isoformat()


def load_previous_route_a_candidate_permission(
    engine,
    *,
    data_date: str,
) -> str | None:
    """Read the latest prior completed-day shadow state without adding a schema."""
    if engine is None:
        return None
    try:
        frame = pd.read_sql(
            text(
                """
                SELECT payload
                FROM lifecycle_events
                WHERE event_type = 'NEW_STRATEGY_SHADOW_MARKET'
                ORDER BY event_time DESC
                LIMIT 20
                """
            ),
            engine,
        )
    except Exception:
        return None
    from core.trading_calendar import previous_a_share_trading_date

    expected_date = previous_a_share_trading_date(data_date)
    for payload in frame.get("payload", pd.Series(dtype="object")):
        try:
            item = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        item_date = str(item.get("data_date") or "")[:10]
        if item.get("completed_day") is True and item_date == expected_date:
            return str(item.get("route_a_candidate_permission") or "") or None
    return None


def build_live_shadow_report(
    source_candidates: list[dict[str, Any]],
    *,
    regime: dict[str, Any] | None = None,
    previous_route_a_candidate_permission: str | None = None,
    completed_day: bool = False,
) -> dict[str, Any]:
    """Build one auditable live shadow report; no execution intent is created."""
    if regime is None:
        from core.data import get_market_regime

        regime = get_market_regime()
    breadth_stage, data_date = _breadth_stage_from_live_context(
        source_candidates,
        regime,
    )
    if previous_route_a_candidate_permission is None:
        try:
            from core.db import get_db_engine

            previous_route_a_candidate_permission = (
                load_previous_route_a_candidate_permission(
                    get_db_engine(),
                    data_date=data_date,
                )
            )
        except Exception:
            previous_route_a_candidate_permission = None
    market = build_shadow_market_context(
        regime,
        breadth_stage,
        previous_route_a_candidate_permission=previous_route_a_candidate_permission,
        completed_day=completed_day,
    )
    candidates = classify_shadow_candidates(source_candidates, market)
    counts = {
        route: sum(item.get("shadow_route") == route for item in candidates)
        for route in ("A", "B", "DISCOVERY")
    }
    return {
        "mode": "SHADOW",
        "policy_version": SHADOW_POLICY_VERSION,
        "data_date": data_date,
        "completed_day": completed_day,
        "market": market,
        "route_counts": counts,
        "candidates": candidates,
        "trade_eligible": False,
    }


def record_new_strategy_shadow_report(report: dict[str, Any]) -> None:
    """Persist shadow observations without creating orders or execution intents."""
    from core.audit_log import record_lifecycle_event

    market = report.get("market") or {}
    record_lifecycle_event(
        "NEW_STRATEGY_SHADOW_MARKET",
        source="new_strategy_shadow",
        strategy_type="new_strategy_shadow",
        payload={
            "data_date": report.get("data_date"),
            "completed_day": bool(report.get("completed_day")),
            "policy_version": report.get("policy_version"),
            **market,
        },
    )
    for candidate in report.get("candidates") or []:
        record_lifecycle_event(
            "NEW_STRATEGY_SHADOW_OBSERVED",
            source="new_strategy_shadow",
            code=candidate.get("代码") or candidate.get("code"),
            name=candidate.get("名称") or candidate.get("name"),
            strategy_type=f"new_strategy_route_{str(candidate.get('shadow_route')).lower()}",
            theme=candidate.get("行业") or candidate.get("industry"),
            payload={
                "data_date": report.get("data_date"),
                "completed_day": bool(report.get("completed_day")),
                "shadow_plan_id": candidate.get("shadow_plan_id"),
                "shadow_route": candidate.get("shadow_route"),
                "shadow_state": candidate.get("shadow_state"),
                "shadow_instruction": candidate.get("shadow_instruction"),
                "shadow_blockers": candidate.get("shadow_blockers"),
                "confirmation_price": candidate.get("pa_entry_price"),
                "stop_price": candidate.get("pa_stop_price"),
                "trade_eligible": False,
            },
        )
