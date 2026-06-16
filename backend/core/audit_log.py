import json
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text

from core.db import _json_safe, get_db_engine
from core.logging_config import logger


def record_lifecycle_event(event_type: str, **values: Any) -> bool:
    engine = get_db_engine()
    if not engine:
        return False
    payload = values.pop("payload", {}) or {}
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO lifecycle_events (
                    event_time, event_type, source, code, name, watchlist_id,
                    trade_id, strategy_type, theme, payload
                ) VALUES (
                    :event_time, :event_type, :source, :code, :name, :watchlist_id,
                    :trade_id, :strategy_type, :theme, :payload
                )
            """), {
                "event_time": datetime.now(),
                "event_type": event_type,
                "source": values.get("source"),
                "code": values.get("code"),
                "name": values.get("name"),
                "watchlist_id": values.get("watchlist_id"),
                "trade_id": values.get("trade_id"),
                "strategy_type": values.get("strategy_type"),
                "theme": values.get("theme"),
                "payload": json.dumps(_json_safe(payload), ensure_ascii=False),
            })
        return True
    except Exception as exc:
        logger.warning(f"Lifecycle audit skipped: {exc}")
        return False


def record_position_decision_change(**values: Any) -> bool:
    engine = get_db_engine()
    if not engine:
        return False
    code = values.get("code")
    snapshot = values.get("snapshot") or {}
    try:
        with engine.connect() as conn:
            latest = conn.execute(text("""
                SELECT payload
                FROM lifecycle_events
                WHERE event_type = 'POSITION_DECISION_CHANGED' AND code = :code
                ORDER BY event_time DESC
                LIMIT 1
            """), {"code": code}).fetchone()
        latest_payload = latest[0] if latest else {}
        if isinstance(latest_payload, str):
            latest_payload = json.loads(latest_payload)
        if (
            latest_payload
            and latest_payload.get("action") == snapshot.get("action")
            and latest_payload.get("trigger") == snapshot.get("trigger")
            and latest_payload.get("executable") == snapshot.get("executable")
        ):
            return False
        return record_lifecycle_event(
            "POSITION_DECISION_CHANGED",
            source=values.get("source") or "position_decision",
            code=code,
            name=values.get("name"),
            trade_id=values.get("trade_id"),
            strategy_type=values.get("strategy_type"),
            theme=values.get("theme"),
            payload=snapshot,
        )
    except Exception as exc:
        logger.warning(f"Position decision audit skipped: {exc}")
        return False


def get_position_decision_timeline(code: str, limit: int = 20) -> list[Dict[str, Any]]:
    engine = get_db_engine()
    if not engine:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT event_time, payload
                FROM lifecycle_events
                WHERE event_type = 'POSITION_DECISION_CHANGED' AND code = :code
                ORDER BY event_time DESC
                LIMIT :limit
            """), {"code": code, "limit": max(1, min(int(limit), 100))}).fetchall()
        items = []
        for event_time, payload in rows:
            if isinstance(payload, str):
                payload = json.loads(payload)
            items.append({"event_time": str(event_time), **(payload or {})})
        return items
    except Exception as exc:
        logger.warning(f"Position decision timeline unavailable: {exc}")
        return []


def record_notification_audit(
    title: str,
    channels: list[str],
    results: Dict[str, bool],
    group: Optional[str],
    body: str,
) -> bool:
    engine = get_db_engine()
    if not engine:
        return False
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO notification_audits (sent_at, title, channels, results, group_name, body_preview)
                VALUES (:sent_at, :title, :channels, :results, :group_name, :body_preview)
            """), {
                "sent_at": datetime.now(),
                "title": title[:200],
                "channels": json.dumps(channels, ensure_ascii=False),
                "results": json.dumps(results, ensure_ascii=False),
                "group_name": group,
                "body_preview": body[:500],
            })
        return True
    except Exception as exc:
        logger.warning(f"Notification audit skipped: {exc}")
        return False


def record_task_run(
    task_id: str,
    task_name: Optional[str],
    status: str,
    *,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    result_summary: Optional[str] = None,
    error_message: Optional[str] = None,
) -> bool:
    engine = get_db_engine()
    if not engine or not task_id:
        return False
    try:
        with engine.begin() as conn:
            existing = conn.execute(
                text("SELECT started_at FROM task_run_audits WHERE task_id = :task_id"),
                {"task_id": task_id},
            ).fetchone()
            original_started_at = existing[0] if existing else None
            if isinstance(original_started_at, str):
                original_started_at = datetime.fromisoformat(original_started_at)
            effective_started_at = started_at or original_started_at
            duration = None
            if effective_started_at and finished_at:
                duration = max(0.0, (finished_at - effective_started_at).total_seconds())
            conn.execute(text("""
                INSERT INTO task_run_audits (
                    task_id, task_name, status, started_at, finished_at,
                    duration_sec, result_summary, error_message
                ) VALUES (
                    :task_id, :task_name, :status, :started_at, :finished_at,
                    :duration_sec, :result_summary, :error_message
                )
                ON CONFLICT (task_id) DO UPDATE SET
                    task_name = COALESCE(excluded.task_name, task_run_audits.task_name),
                    status = excluded.status,
                    started_at = COALESCE(excluded.started_at, task_run_audits.started_at),
                    finished_at = COALESCE(excluded.finished_at, task_run_audits.finished_at),
                    duration_sec = COALESCE(excluded.duration_sec, task_run_audits.duration_sec),
                    result_summary = COALESCE(excluded.result_summary, task_run_audits.result_summary),
                    error_message = COALESCE(excluded.error_message, task_run_audits.error_message)
            """), {
                "task_id": task_id,
                "task_name": task_name,
                "status": status,
                "started_at": effective_started_at,
                "finished_at": finished_at,
                "duration_sec": duration,
                "result_summary": result_summary[:1000] if result_summary else None,
                "error_message": error_message[:2000] if error_message else None,
            })
        return True
    except Exception as exc:
        logger.warning(f"Task audit skipped: {exc}")
        return False
