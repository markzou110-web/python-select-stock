import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.operational_metrics import build_operational_metrics, render_prometheus_metrics


def test_operational_metrics_supports_sqlite_and_counts_failures():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE daily_k (date TEXT)"))
        conn.execute(text("CREATE TABLE scan_history (date TEXT, scanned_at TEXT)"))
        conn.execute(text("CREATE TABLE task_run_audits (status TEXT, started_at TEXT, finished_at TEXT)"))
        conn.execute(text("CREATE TABLE notification_audits (sent_at TEXT, results TEXT)"))
        conn.execute(text("CREATE TABLE notification_outbox (status TEXT, created_at TEXT, sent_at TEXT)"))
        conn.execute(text("INSERT INTO daily_k VALUES (date('now'))"))
        conn.execute(text("INSERT INTO scan_history VALUES (date('now'), datetime('now'))"))
        conn.execute(text("INSERT INTO task_run_audits VALUES ('FAILURE', datetime('now'), NULL)"))
        conn.execute(text("INSERT INTO task_run_audits VALUES ('SUCCESS_WITH_ERRORS', datetime('now'), NULL)"))
        conn.execute(text("INSERT INTO notification_audits VALUES (datetime('now'), '{\"bark\": false}')"))
        conn.execute(text("INSERT INTO notification_audits VALUES (datetime('now'), '{\"bark\": true, \"feishu\": false}')"))
        conn.execute(text("INSERT INTO notification_outbox VALUES ('PENDING', datetime('now'), NULL)"))
        conn.execute(text("INSERT INTO notification_outbox VALUES ('DEAD', datetime('now'), NULL)"))
        conn.execute(text("INSERT INTO notification_outbox VALUES ('SENT', datetime('now', '-20 minutes'), datetime('now'))"))
        conn.execute(text("""
            INSERT INTO task_run_audits VALUES (
                'SUCCESS', datetime('now'), datetime('now')
            )
        """))

    report = build_operational_metrics(engine)

    assert report["metrics"]["failed_tasks_24h"] == 1
    assert report["metrics"]["degraded_tasks_24h"] == 1
    assert report["metrics"]["notification_failures_24h"] == 1
    assert report["metrics"]["notifications_24h"] == 2
    assert report["metrics"]["notification_pending"] == 1
    assert report["metrics"]["notification_dead"] == 1
    assert report["metrics"]["notification_recovered_24h"] == 1
    assert report["metrics"]["notification_max_recovery_seconds_24h"] >= 1200
    assert report["status"] == "critical"
    assert any(item["code"] == "NOTIFICATION_INITIAL_FAILURE" for item in report["alerts"])


def test_prometheus_output_is_numeric_and_does_not_expose_details():
    output = render_prometheus_metrics({
        "status": "warning",
        "metrics": {
            "failed_tasks_24h": 2, "degraded_tasks_24h": 4,
            "notifications_24h": 5, "notification_failures_24h": 0,
            "notification_pending": 3, "notification_dead": 1, "late_formal_scan_completed": 1,
            "notification_recovered_24h": 2, "notification_max_recovery_seconds_24h": 1200,
        },
        "alerts": [{"message": "private detail"}],
    })

    assert "alphavision_operational_status 1" in output
    assert "alphavision_failed_tasks_24h 2" in output
    assert "alphavision_degraded_tasks_24h 4" in output
    assert "alphavision_notification_pending 3" in output
    assert "alphavision_notification_dead 1" in output
    assert "alphavision_notification_recovered_24h 2" in output
    assert "alphavision_notification_max_recovery_seconds_24h 1200" in output
    assert "alphavision_late_formal_scan_completed 1" in output
    assert "private detail" not in output
