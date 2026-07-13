"""Database-backed task-slot idempotency with timeout takeover and failure release."""
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable

from sqlalchemy import text

from core.db import get_db_engine
from core.logging_config import logger


def claim_slot(slot_key: str, timeout_minutes: int = 120, engine=None) -> bool:
    engine = engine or get_db_engine()
    if engine is None:
        return False
    cutoff = datetime.now() - timedelta(minutes=max(5, int(timeout_minutes)))
    with engine.begin() as conn:
        inserted = conn.execute(text("""
            INSERT INTO task_slot_claims(slot_key, claimed_at, status)
            VALUES (:key, :now, 'RUNNING') ON CONFLICT(slot_key) DO NOTHING
        """), {"key": slot_key[:160], "now": datetime.now()})
        if inserted.rowcount:
            return True
        takeover = conn.execute(text("""
            UPDATE task_slot_claims SET claimed_at=:now, status='RUNNING'
            WHERE slot_key=:key AND (status='FAILED' OR (status='RUNNING' AND claimed_at < :cutoff))
        """), {"key": slot_key[:160], "now": datetime.now(), "cutoff": cutoff})
        return bool(takeover.rowcount)


def finish_slot(slot_key: str, status: str, engine=None) -> None:
    engine = engine or get_db_engine()
    if engine is None:
        return
    with engine.begin() as conn:
        conn.execute(text("UPDATE task_slot_claims SET status=:status WHERE slot_key=:key"), {
            "key": slot_key[:160], "status": status[:30],
        })


def release_slot(slot_key: str, engine=None) -> bool:
    engine = engine or get_db_engine()
    if engine is None:
        return False
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM task_slot_claims WHERE slot_key=:key"), {"key": slot_key[:160]})
    return bool(result.rowcount)


def daily_task_slot(task_name: str, slot_argument: str | None = None, timeout_minutes: int = 120):
    """Decorate a Celery task before registration: one run per date and logical slot."""
    def decorator(func: Callable):
        @wraps(func)
        def wrapped(*args: Any, **kwargs: Any):
            try:
                from celery import current_task
                request_id = getattr(getattr(current_task, "request", None), "id", None)
            except Exception:
                request_id = None
            if not request_id:
                # Direct calls (unit tests, local diagnostics) are not scheduler deliveries.
                return func(*args, **kwargs)
            slot = kwargs.get(slot_argument) if slot_argument else None
            if slot is None and slot_argument and args:
                slot = args[0]
            key = f"{task_name}:{datetime.now():%Y-%m-%d}:{slot or 'default'}"
            if not claim_slot(key, timeout_minutes=timeout_minutes):
                return {"status": "skipped", "reason": "duplicate_task_slot", "slot_key": key}
            try:
                result = func(*args, **kwargs)
                failed = isinstance(result, dict) and (result.get("error") or result.get("status") in {"error", "failed"})
                finish_slot(key, "FAILED" if failed else "SUCCESS")
                return result
            except Exception:
                finish_slot(key, "FAILED")
                logger.exception(f"Task slot failed: {key}")
                raise
        return wrapped
    return decorator
