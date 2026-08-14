import os
import sys
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import audit_log
from core.db import init_db
from routers import system


def _engine():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return engine


def test_p1_schema_is_created_without_replacing_existing_tables():
    engine = _engine()
    with engine.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))}
        paper_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(paper_trading)"))}
        watch_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(watchlist)"))}
        snapshot_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(point_in_time_stock_snapshots)"))}
    assert {"lifecycle_events", "task_run_audits", "notification_audits", "notification_outbox"} <= tables
    assert {"planned_entry_price", "actual_entry_price", "entry_slippage_pct", "logic_status"} <= paper_columns
    assert {"logic_status", "logic_last_review_at"} <= watch_columns
    assert {"price", "pct_chg", "amount", "limit_up"} <= snapshot_columns


def test_lifecycle_notification_and_task_audits(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(audit_log, "get_db_engine", lambda: engine)

    assert audit_log.record_lifecycle_event("WATCHLIST_ADDED", code="000001", payload={"price": 10})
    assert audit_log.record_notification_audit("test", ["bark"], {"bark": True}, "AlphaVision", "body")
    started_at = datetime.now()
    assert audit_log.record_task_run("task-1", "scan.run_market_scan_task", "STARTED", started_at=started_at)
    assert audit_log.record_task_run("task-1", "scan.run_market_scan_task", "SUCCESS", finished_at=datetime.now())

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM lifecycle_events")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM notification_audits")).scalar() == 1
        task = conn.execute(text("SELECT status, duration_sec FROM task_run_audits WHERE task_id = 'task-1'")).fetchone()
    assert task[0] == "SUCCESS"
    assert task[1] is not None


def test_failed_notification_outbox_can_be_loaded_and_marked_sent(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(audit_log, "get_db_engine", lambda: engine)

    assert audit_log.enqueue_notification("bark", "收盘日报", "完整正文", group="AlphaVision")
    with engine.begin() as conn:
        conn.execute(text("UPDATE notification_outbox SET next_retry_at = :now"), {"now": datetime.now()})
    pending = audit_log.load_due_notifications()

    assert len(pending) == 1
    assert pending[0]["body"] == "完整正文"
    assert audit_log.record_notification_retry(pending[0]["id"], True)
    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM notification_outbox")).scalar()
    assert status == "SENT"


def test_stale_notification_is_expired_instead_of_retried(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(audit_log, "get_db_engine", lambda: engine)
    assert audit_log.enqueue_notification("bark", "昨日尾盘提醒", "过期正文")
    now = datetime.now()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE notification_outbox
            SET created_at = :created_at, next_retry_at = :next_retry_at
        """), {
            "created_at": now - timedelta(minutes=audit_log.NOTIFICATION_MAX_AGE_MINUTES + 1),
            "next_retry_at": now,
        })

    assert audit_log.load_due_notifications(now=now) == []
    with engine.connect() as conn:
        status, error = conn.execute(text(
            "SELECT status, last_error FROM notification_outbox"
        )).one()
    assert (status, error) == ("DEAD", "expired_before_delivery")


def test_permanent_notification_failure_is_marked_dead_immediately(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(audit_log, "get_db_engine", lambda: engine)
    assert audit_log.enqueue_notification("bark", "超长通知", "正文")
    with engine.connect() as conn:
        outbox_id = conn.execute(text("SELECT id FROM notification_outbox")).scalar()

    assert audit_log.record_notification_retry(
        outbox_id, False, "payload_too_large", permanent=True,
    )

    with engine.connect() as conn:
        status, attempts, error = conn.execute(text(
            "SELECT status, attempts, last_error FROM notification_outbox WHERE id = :id"
        ), {"id": outbox_id}).one()
    assert (status, attempts, error) == ("DEAD", 1, "payload_too_large")


def test_dead_notifications_can_be_requeued_without_changing_payload(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(audit_log, "get_db_engine", lambda: engine)
    assert audit_log.enqueue_notification("bark", "尾盘结论", "完整正文")
    with engine.begin() as conn:
        conn.execute(text("UPDATE notification_outbox SET status = 'DEAD', attempts = 12"))

    assert audit_log.requeue_dead_notifications(limit=10) == 1

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, attempts, body FROM notification_outbox"
        )).first()
    assert row == ("PENDING", 0, "完整正文")


def test_conversion_funnel_counts_recent_events(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(system, "get_db_engine", lambda: engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO lifecycle_events (event_time, event_type)
            VALUES (:now, 'WATCHLIST_ADDED'), (:now, 'WATCHLIST_TRIGGERED'), (:now, 'PAPER_OPENED')
        """), {"now": datetime.now()})

    result = system.get_conversion_funnel(days=30)
    counts = {item["key"]: item["count"] for item in result["stages"]}
    assert counts["WATCHLIST_ADDED"] == 1
    assert counts["WATCHLIST_TRIGGERED"] == 1
    assert result["rates"]["watch_to_paper_pct"] == 100.0
