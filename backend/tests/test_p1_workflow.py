import os
import sys
from datetime import datetime

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
    assert {"lifecycle_events", "task_run_audits", "notification_audits"} <= tables
    assert {"planned_entry_price", "actual_entry_price", "entry_slippage_pct", "logic_status"} <= paper_columns
    assert {"logic_status", "logic_last_review_at"} <= watch_columns


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
