"""Versioned confirmation-price event tracking."""
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import text


ENTRY_EVENT_VERSION = "confirmation-event-v1"


def classify_entry_event(
    current_price: float,
    confirmation_price: float,
    invalidation_price: float | None = None,
    *,
    close_confirmed: bool = False,
    tolerance_pct: float = 0.5,
) -> str:
    if current_price <= 0 or confirmation_price <= 0:
        return "INVALID_DATA"
    if invalidation_price and current_price <= invalidation_price:
        return "INVALIDATED"
    if current_price >= confirmation_price:
        return "CLOSE_CONFIRMED" if close_confirmed else "BROKE_OUT"
    gap_pct = (confirmation_price - current_price) / confirmation_price * 100
    return "APPROACHED" if gap_pct <= tolerance_pct else "WAITING"


def save_entry_event(engine, event: Dict[str, Any]) -> bool:
    """Append an entry event to the existing generic lifecycle event stream."""
    if engine is None:
        return False
    payload = {
        "event_time": event.get("event_time") or datetime.now(),
        "event_type": event["event_type"],
        "source": event.get("source") or "entry_confirmation",
        "code": event.get("code"),
        "name": event.get("name"),
        "strategy_type": event.get("strategy_type"),
        "payload": {
            "confirmation_price": event.get("confirmation_price"),
            "current_price": event.get("current_price"),
            "invalidation_price": event.get("invalidation_price"),
            "version": ENTRY_EVENT_VERSION,
        },
    }
    import json
    with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            conn.execute(text("""
                INSERT INTO lifecycle_events
                    (event_time, event_type, source, code, name, strategy_type, payload)
                VALUES
                    (:event_time, :event_type, :source, :code, :name, :strategy_type, :payload)
            """), {**payload, "payload": json.dumps(payload["payload"], ensure_ascii=False)})
        else:
            conn.execute(text("""
                INSERT INTO lifecycle_events
                    (event_time, event_type, source, code, name, strategy_type, payload)
                VALUES
                    (:event_time, :event_type, :source, :code, :name, :strategy_type, CAST(:payload AS JSONB))
            """), {**payload, "payload": json.dumps(payload["payload"], ensure_ascii=False)})
    return True
