import json
import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import text

from core.db import _json_safe, get_db_engine
from core.logging_config import logger


NOTIFICATION_MAX_AGE_MINUTES = 90
NOTIFICATION_MAX_RETRY_ATTEMPTS = 5


def _notification_dedupe_key(channel: str, title: str, body: str, now: Optional[datetime] = None) -> str:
    day = (now or datetime.now()).strftime("%Y-%m-%d")
    return hashlib.sha256(f"{day}|{channel}|{title}|{body}".encode("utf-8")).hexdigest()


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


def record_watchlist_theme_state_change(**values: Any) -> bool:
    engine = get_db_engine()
    if not engine:
        return False
    code = values.get("code")
    watchlist_id = values.get("watchlist_id")
    state = values.get("state")
    if not code or not state:
        return False
    try:
        with engine.connect() as conn:
            latest = conn.execute(text("""
                SELECT payload
                FROM lifecycle_events
                WHERE event_type = 'WATCHLIST_THEME_STATE_CHANGED'
                  AND code = :code
                  AND (
                    (:watchlist_id IS NULL AND watchlist_id IS NULL)
                    OR watchlist_id = :watchlist_id
                  )
                ORDER BY event_time DESC
                LIMIT 1
            """), {"code": code, "watchlist_id": watchlist_id}).fetchone()
        latest_payload = latest[0] if latest else {}
        if isinstance(latest_payload, str):
            latest_payload = json.loads(latest_payload)
        if latest_payload and latest_payload.get("state") == state:
            return False
        payload = {
            "state": state,
            "label": values.get("label"),
            "action": values.get("action"),
            "current_price": values.get("current_price"),
            "watch_price": values.get("watch_price"),
            "target_price": values.get("target_price"),
            "stop_price": values.get("stop_price"),
            "pl_pct": values.get("pl_pct"),
        }
        return record_lifecycle_event(
            "WATCHLIST_THEME_STATE_CHANGED",
            source=values.get("source") or "watchlist_theme_state",
            code=code,
            name=values.get("name"),
            watchlist_id=watchlist_id,
            strategy_type=values.get("strategy_type"),
            theme=values.get("theme"),
            payload=payload,
        )
    except Exception as exc:
        logger.warning(f"Watchlist theme state audit skipped: {exc}")
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


def enqueue_notification(
    channel: str,
    title: str,
    body: str,
    *,
    url: Optional[str] = None,
    group: Optional[str] = None,
    is_archive: int = 1,
) -> bool:
    """Persist a failed configured notification without storing channel credentials."""
    engine = get_db_engine()
    if not engine or not channel or not title or not body:
        return False
    dedupe_key = _notification_dedupe_key(channel, title, body)
    now = datetime.now()
    try:
        with engine.begin() as conn:
            result = conn.execute(text("""
                INSERT INTO notification_outbox (
                    dedupe_key, channel, title, body, url, group_name, is_archive,
                    status, attempts, next_retry_at, created_at
                ) VALUES (
                    :dedupe_key, :channel, :title, :body, :url, :group_name, :is_archive,
                    'PENDING', 0, :next_retry_at, :created_at
                )
                ON CONFLICT(dedupe_key) DO NOTHING
            """), {
                "dedupe_key": dedupe_key,
                "channel": channel[:30],
                "title": title[:200],
                "body": body,
                "url": url,
                "group_name": group,
                "is_archive": int(is_archive),
                "next_retry_at": now + timedelta(minutes=5),
                "created_at": now,
            })
        return bool(result.rowcount)
    except Exception as exc:
        logger.warning(f"Notification outbox enqueue skipped: {exc}")
        return False


def load_due_notifications(
    limit: int = 20,
    *,
    now: Optional[datetime] = None,
) -> list[Dict[str, Any]]:
    engine = get_db_engine()
    if not engine:
        return []
    now = now or datetime.now()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE notification_outbox
                SET status = 'DEAD', last_error = 'expired_before_delivery'
                WHERE status IN ('PENDING', 'PROCESSING') AND created_at < :stale_before
            """), {
                "stale_before": now - timedelta(minutes=NOTIFICATION_MAX_AGE_MINUTES),
            })
            lock_clause = " FOR UPDATE SKIP LOCKED" if engine.dialect.name != "sqlite" else ""
            rows = conn.execute(text(f"""
                SELECT id, channel, title, body, url, group_name, is_archive, attempts
                FROM notification_outbox
                WHERE status IN ('PENDING', 'PROCESSING') AND next_retry_at <= :now
                ORDER BY next_retry_at, id
                LIMIT :limit
                {lock_clause}
            """), {"now": now, "limit": max(1, min(int(limit), 100))}).mappings().all()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ", ".join(f":id_{index}" for index in range(len(ids)))
                params = {f"id_{index}": value for index, value in enumerate(ids)}
                params["next_retry_at"] = now + timedelta(minutes=15)
                conn.execute(text(f"""
                    UPDATE notification_outbox
                    SET status = 'PROCESSING', next_retry_at = :next_retry_at
                    WHERE id IN ({placeholders})
                """), params)
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.warning(f"Notification outbox load skipped: {exc}")
        return []


def record_notification_retry(
    outbox_id: int,
    sent: bool,
    error: Optional[str] = None,
    *,
    permanent: bool = False,
) -> bool:
    engine = get_db_engine()
    if not engine:
        return False
    try:
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT attempts FROM notification_outbox WHERE id = :id"
            ), {"id": outbox_id}).first()
            if not row:
                return False
            attempts = int(row[0] or 0) + 1
            if sent:
                conn.execute(text("""
                    UPDATE notification_outbox
                    SET status = 'SENT', attempts = :attempts, sent_at = :now, last_error = NULL
                    WHERE id = :id
                """), {"attempts": attempts, "now": datetime.now(), "id": outbox_id})
            else:
                status = "DEAD" if permanent or attempts >= NOTIFICATION_MAX_RETRY_ATTEMPTS else "PENDING"
                delay_minutes = min(60, 5 * (2 ** min(attempts, 4)))
                conn.execute(text("""
                    UPDATE notification_outbox
                    SET status = :status, attempts = :attempts, next_retry_at = :next_retry_at,
                        last_error = :last_error
                    WHERE id = :id
                """), {
                    "status": status,
                    "attempts": attempts,
                    "next_retry_at": datetime.now() + timedelta(minutes=delay_minutes),
                    "last_error": (error or "delivery_failed")[:2000],
                    "id": outbox_id,
                })
        return True
    except Exception as exc:
        logger.warning(f"Notification outbox update skipped: {exc}")
        return False


def resolve_queued_notification(channel: str, title: str, body: str) -> bool:
    """Close a queued duplicate when a caller's immediate retry already succeeded."""
    engine = get_db_engine()
    if not engine:
        return False
    try:
        with engine.begin() as conn:
            result = conn.execute(text("""
                UPDATE notification_outbox
                SET status = 'SENT', sent_at = :now, last_error = NULL
                WHERE dedupe_key = :dedupe_key AND status IN ('PENDING', 'PROCESSING')
            """), {
                "now": datetime.now(),
                "dedupe_key": _notification_dedupe_key(channel, title, body),
            })
        return bool(result.rowcount)
    except Exception as exc:
        logger.warning(f"Notification outbox resolve skipped: {exc}")
        return False


def requeue_dead_notifications(limit: int = 20) -> int:
    """Move a bounded number of dead deliveries back to the retry queue."""
    engine = get_db_engine()
    if not engine:
        return 0
    try:
        with engine.begin() as conn:
            lock_clause = " FOR UPDATE SKIP LOCKED" if engine.dialect.name != "sqlite" else ""
            rows = conn.execute(text(f"""
                SELECT id FROM notification_outbox
                WHERE status = 'DEAD' AND COALESCE(last_error, '') != 'expired_before_delivery'
                ORDER BY created_at, id
                LIMIT :limit
                {lock_clause}
            """), {"limit": max(1, min(int(limit), 100))}).all()
            ids = [int(row[0]) for row in rows]
            if not ids:
                return 0
            placeholders = ", ".join(f":id_{index}" for index in range(len(ids)))
            params = {f"id_{index}": value for index, value in enumerate(ids)}
            params["next_retry_at"] = datetime.now()
            result = conn.execute(text(f"""
                UPDATE notification_outbox
                SET status = 'PENDING', attempts = 0, next_retry_at = :next_retry_at
                WHERE id IN ({placeholders}) AND status = 'DEAD'
            """), params)
        return int(result.rowcount or 0)
    except Exception as exc:
        logger.warning(f"Notification outbox requeue skipped: {exc}")
        return 0
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
