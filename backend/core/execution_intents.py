"""Auditable lifecycle between a Bark trade instruction and the user's execution."""
import json
import uuid
from datetime import datetime, time
from typing import Any, Dict, Iterable

from sqlalchemy import text


STATES = ("ISSUED", "SEEN", "ACCEPTED", "SKIPPED", "ORDERED", "PARTIAL", "FILLED", "CANCELLED", "EXPIRED")
TERMINAL_STATES = {"SKIPPED", "FILLED", "CANCELLED", "EXPIRED"}
ALLOWED = {
    "ISSUED": {"SEEN", "ACCEPTED", "SKIPPED", "EXPIRED"},
    "SEEN": {"ACCEPTED", "SKIPPED", "EXPIRED"},
    "ACCEPTED": {"ORDERED", "CANCELLED", "EXPIRED"},
    "ORDERED": {"PARTIAL", "FILLED", "CANCELLED", "EXPIRED"},
    "PARTIAL": {"PARTIAL", "FILLED", "CANCELLED"},
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def create_bark_execution_intents(stocks: Iterable[Dict[str, Any]], engine, issued_at: datetime | None = None) -> list[str]:
    """Create intents only for successfully delivered, explicitly tradable candidates."""
    now = issued_at or datetime.now()
    json_expr = ":snapshot" if engine.dialect.name == "sqlite" else "CAST(:snapshot AS JSON)"
    created = []
    with engine.begin() as conn:
        for stock in stocks:
            detail = stock.get("price_action_detail") or {}
            if not isinstance(detail, dict):
                detail = {}
            if not bool(stock.get("trade_eligible") or detail.get("trade_eligible")) or str(stock.get("trade_bucket") or detail.get("trade_bucket")) != "TRADE":
                continue
            code = str(stock.get("代码") or stock.get("code") or "")
            strategy = str(stock.get("strategy_type") or "squeeze")
            if not code:
                continue
            identity = f"bark:{now.date()}:{code}:{strategy}"
            intent_id = f"int_{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex[:20]}"
            position_plan = stock.get("position_plan") or detail.get("position_plan") or {}
            if not isinstance(position_plan, dict):
                position_plan = {}
            snapshot = {
                "grade": stock.get("sop_grade"), "score": stock.get("Score") or stock.get("score"),
                "a_minus_trial": bool(stock.get("a_minus_trial")),
                "a_minus_trial_grade": stock.get("a_minus_trial_grade"),
                "a_minus_trial_policy_version": stock.get("a_minus_trial_policy_version"),
                "a_minus_trial_health": stock.get("a_minus_trial_health"),
                "a_minus_portfolio_cap_pct": stock.get("a_minus_portfolio_cap_pct"),
                "a_eod_controlled_trial": bool(stock.get("a_eod_controlled_trial")),
                "a_eod_policy_version": stock.get("a_eod_policy_version"),
                "a_eod_trade_cautions": stock.get("a_eod_trade_cautions") or [],
                "a_eod_portfolio_cap_pct": stock.get("a_eod_portfolio_cap_pct"),
                "a_eod_max_positions": stock.get("a_eod_max_positions"),
                "a_eod_t1_plan": bool(stock.get("a_eod_t1_plan")),
                "a_eod_t1_confirmed": bool(stock.get("a_eod_t1_confirmed")),
                "a_eod_t1_policy_version": stock.get("a_eod_t1_policy_version"),
                "a_eod_t1_frozen_entry_price": stock.get("a_eod_t1_frozen_entry_price"),
                "a_eod_t1_frozen_stop_price": stock.get("a_eod_t1_frozen_stop_price"),
                "a_eod_t1_frozen_target_price": stock.get("a_eod_t1_frozen_target_price"),
                "a_eod_t1_entry_extension_pct": stock.get("a_eod_t1_entry_extension_pct"),
                "a_eod_t1_portfolio_cap_pct": stock.get("a_eod_t1_portfolio_cap_pct"),
                "a_eod_t1_max_positions": stock.get("a_eod_t1_max_positions"),
                "trade_opportunity_score": stock.get("trade_opportunity_score"),
                "pa_execution_policy_version": stock.get("pa_execution_policy_version"),
                "pa_execution_tier": stock.get("pa_execution_tier"),
                "pa_execution_tier_label": stock.get("pa_execution_tier_label"),
                "signal_sources": stock.get("signal_sources") or detail.get("signal_sources") or [],
                "tv_execution_policy_version": stock.get("tv_execution_policy_version") or detail.get("tv_execution_policy_version"),
                "tv_execution_tier": stock.get("tv_execution_tier") or detail.get("tv_execution_tier"),
                "tv_execution_risk_unit": stock.get("tv_execution_risk_unit") or detail.get("tv_execution_risk_unit"),
                "entry_condition": stock.get("pa_entry_condition") or detail.get("pa_entry_condition"),
                "blockers": stock.get("trade_blockers") or detail.get("trade_blockers") or [],
                "market_regime": stock.get("market_regime") or detail.get("market_regime"),
                "market_sentiment_stage": stock.get("market_sentiment_stage") or detail.get("market_sentiment_stage"),
                "sector_phase": stock.get("sector_phase") or detail.get("sector_phase"),
                "sector_mainline": stock.get("sector_mainline") or detail.get("sector_mainline"),
                "evidence_id": stock.get("evidence_id") or detail.get("evidence_id"),
                "evidence_grade": stock.get("evidence_grade") or detail.get("evidence_grade"),
                "evidence_status": stock.get("evidence_status") or detail.get("evidence_status"),
                "evidence_reason_codes": stock.get("evidence_reason_codes") or detail.get("evidence_reason_codes") or [],
                "signal_price": _number(stock.get("现价") or stock.get("price")),
                "position_plan": position_plan,
                "execution_instruction": stock.get("execution_instruction") or detail.get("execution_instruction"),
            }
            params = {
                "id": intent_id, "signal_date": now.date(), "issued_at": now,
                "valid_until": datetime.combine(now.date(), time(15, 0)), "source": "bark",
                "code": code, "name": stock.get("名称") or stock.get("name"), "strategy": strategy,
                "entry": _number(stock.get("pa_entry_price") or detail.get("pa_entry_price")),
                "stop": _number(stock.get("pa_stop_price") or detail.get("pa_stop_price")),
                "target": _number(stock.get("pa_target_price") or detail.get("pa_target_price")),
                "position": _number(
                    stock.get("suggested_position_pct")
                    or stock.get("position_pct")
                    or position_plan.get("initial_position_pct")
                ),
                "snapshot": json.dumps(snapshot, ensure_ascii=False, default=str), "updated_at": now,
            }
            inserted = conn.execute(text(f"""
                INSERT INTO execution_intents(
                    intent_id,signal_date,issued_at,valid_until,source,code,name,strategy_type,
                    instruction,state,planned_entry_price,stop_price,target_price,planned_position_pct,
                    signal_snapshot,updated_at
                ) VALUES (
                    :id,:signal_date,:issued_at,:valid_until,:source,:code,:name,:strategy,
                    '可交易','ISSUED',:entry,:stop,:target,:position,{json_expr},:updated_at
                ) ON CONFLICT(signal_date,source,code,strategy_type) DO NOTHING
            """), params).rowcount
            if inserted:
                conn.execute(text("""
                    INSERT INTO execution_intent_events(intent_id,event_at,from_state,to_state,note)
                    VALUES (:id,:event_at,NULL,'ISSUED','Bark可交易指令已成功发送')
                """), {"id": intent_id, "event_at": now})
                created.append(intent_id)
    return created


def transition_execution_intent(engine, intent_id: str, target: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    target = str(target or "").upper()
    if target not in STATES or target == "ISSUED":
        return {"changed": False, "error": "invalid_target_state"}
    with engine.begin() as conn:
        query = "SELECT * FROM execution_intents WHERE intent_id=:id"
        if engine.dialect.name != "sqlite":
            query += " FOR UPDATE"
        row = conn.execute(text(query), {"id": intent_id}).mappings().first()
        if not row:
            return {"changed": False, "error": "intent_not_found"}
        current = str(row["state"])
        if target not in ALLOWED.get(current, set()):
            return {"changed": False, "state": current, "error": "transition_not_allowed"}
        shares = int(payload.get("shares") or row.get("filled_shares") or 0)
        actual_price = _number(payload.get("actual_price"))
        if target == "ORDERED" and shares <= 0:
            return {"changed": False, "state": current, "error": "ordered_shares_required"}
        if target in {"PARTIAL", "FILLED"} and (shares <= 0 or actual_price is None):
            return {"changed": False, "state": current, "error": "fill_price_and_shares_required"}
        ordered_shares = shares if target == "ORDERED" else int(row.get("ordered_shares") or 0)
        if target in {"PARTIAL", "FILLED"} and ordered_shares and shares > ordered_shares:
            return {"changed": False, "state": current, "error": "filled_shares_exceed_order"}
        planned = _number(row.get("planned_entry_price"))
        slippage = (actual_price - planned) / planned * 100 if actual_price and planned else None
        now = datetime.now()
        conn.execute(text("""
            UPDATE execution_intents SET state=:target,
                ordered_shares=CASE WHEN :ordered > 0 THEN :ordered ELSE ordered_shares END,
                filled_shares=CASE WHEN :filled > 0 THEN :filled ELSE filled_shares END,
                actual_price=COALESCE(:price,actual_price),slippage_pct=COALESCE(:slippage,slippage_pct),updated_at=:updated
            WHERE intent_id=:id
        """), {
            "target": target, "ordered": ordered_shares, "filled": shares if target in {"PARTIAL", "FILLED"} else 0,
            "price": actual_price, "slippage": slippage, "updated": now, "id": intent_id,
        })
        conn.execute(text("""
            INSERT INTO execution_intent_events(intent_id,event_at,from_state,to_state,actual_price,shares,note)
            VALUES (:id,:event_at,:from_state,:to_state,:price,:shares,:note)
        """), {
            "id": intent_id, "event_at": now, "from_state": current, "to_state": target,
            "price": actual_price, "shares": shares or None, "note": str(payload.get("note") or "")[:500] or None,
        })
    return {"changed": True, "intent_id": intent_id, "previous_state": current, "state": target, "slippage_pct": round(slippage, 4) if slippage is not None else None}


def list_execution_intents(engine, state: str | None = None, limit: int = 100):
    where = "WHERE state=:state" if state else ""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT intent_id,signal_date,issued_at,valid_until,source,code,name,strategy_type,instruction,state,
                   planned_entry_price,stop_price,target_price,planned_position_pct,ordered_shares,filled_shares,
                   actual_price,slippage_pct,updated_at
            FROM execution_intents {where} ORDER BY issued_at DESC LIMIT :limit
        """), {"state": state, "limit": min(max(int(limit), 1), 500)}).mappings().all()
    return [dict(row) for row in rows]


def get_execution_intent(engine, intent_id: str):
    with engine.connect() as conn:
        intent = conn.execute(text("SELECT * FROM execution_intents WHERE intent_id=:id"), {"id": intent_id}).mappings().first()
        events = conn.execute(text("SELECT * FROM execution_intent_events WHERE intent_id=:id ORDER BY event_at,id"), {"id": intent_id}).mappings().all()
    return {"intent": dict(intent), "events": [dict(row) for row in events]} if intent else None


def expire_due_execution_intents(engine, now: datetime | None = None) -> int:
    now = now or datetime.now()
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT intent_id,state FROM execution_intents
            WHERE valid_until IS NOT NULL AND valid_until < :now
              AND state IN ('ISSUED','SEEN','ACCEPTED','ORDERED')
        """), {"now": now}).mappings().all()
        for row in rows:
            conn.execute(text("UPDATE execution_intents SET state='EXPIRED',updated_at=:now WHERE intent_id=:id"), {"now": now, "id": row["intent_id"]})
            conn.execute(text("""
                INSERT INTO execution_intent_events(intent_id,event_at,from_state,to_state,note)
                VALUES (:id,:now,:state,'EXPIRED','有效交易窗口结束，系统自动过期')
            """), {"id": row["intent_id"], "now": now, "state": row["state"]})
    return len(rows)


def build_execution_attribution(engine, days: int = 30) -> Dict[str, Any]:
    days = min(max(int(days), 1), 365)
    cutoff_expr = "datetime('now', '-' || :days || ' days')" if engine.dialect.name == "sqlite" else "CURRENT_TIMESTAMP - (:days || ' days')::interval"
    with engine.connect() as conn:
        states = conn.execute(text(f"""
            SELECT state,COUNT(*) count FROM execution_intents
            WHERE issued_at >= {cutoff_expr} GROUP BY state
        """), {"days": days}).mappings().all()
        fills = conn.execute(text(f"""
            SELECT COUNT(*) count,AVG(slippage_pct) avg_slippage FROM execution_intents
            WHERE issued_at >= {cutoff_expr} AND state='FILLED'
        """), {"days": days}).mappings().first()
    counts = {row["state"]: int(row["count"]) for row in states}
    total = sum(counts.values())
    filled = int((fills or {}).get("count") or 0)
    skipped = counts.get("SKIPPED", 0)
    unresolved = sum(counts.get(state, 0) for state in ("ISSUED", "SEEN", "ACCEPTED", "ORDERED", "PARTIAL"))
    return {
        "days": days, "total_intents": total, "states": counts,
        "filled": filled, "skipped": skipped, "unresolved": unresolved,
        "fill_rate_pct": round(filled / total * 100, 1) if total else 0,
        "skip_rate_pct": round(skipped / total * 100, 1) if total else 0,
        "avg_entry_slippage_pct": round(float((fills or {}).get("avg_slippage") or 0), 4) if filled else None,
        "note": "成交率用于执行归因，不等于策略胜率。",
    }
