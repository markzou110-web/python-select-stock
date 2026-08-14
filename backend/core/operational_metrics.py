"""Structured operational metrics and alert rules without external dependencies."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import text


def _notification_attempt_failed(raw_result: Any) -> bool:
    """A notification attempt fails only when none of its requested channels succeeds."""
    value = raw_result
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return True
    if not isinstance(value, dict) or not value:
        return True
    return not any(bool(delivered) for delivered in value.values())


def build_operational_metrics(engine) -> Dict[str, Any]:
    now = datetime.now()
    latest_scan_sql = (
        "SELECT MAX(COALESCE(scanned_at,date)) FROM scan_history"
        if engine.dialect.name == "sqlite"
        else "SELECT MAX(COALESCE(scanned_at,date::timestamp)) FROM scan_history"
    )
    with engine.connect() as conn:
        latest_daily = conn.execute(text("SELECT MAX(date) FROM daily_k")).scalar()
        latest_scan = conn.execute(text(latest_scan_sql)).scalar()
        failed_tasks = int(conn.execute(text("SELECT COUNT(*) FROM task_run_audits WHERE status='FAILURE' AND COALESCE(started_at,finished_at)>=:cutoff"), {"cutoff": now - timedelta(days=1)}).scalar() or 0)
        degraded_tasks = int(conn.execute(text("SELECT COUNT(*) FROM task_run_audits WHERE status='SUCCESS_WITH_ERRORS' AND COALESCE(started_at,finished_at)>=:cutoff"), {"cutoff": now - timedelta(days=1)}).scalar() or 0)
        notifications = conn.execute(text("SELECT results FROM notification_audits WHERE sent_at>=:cutoff"), {"cutoff": now - timedelta(days=1)}).scalars().all()
        try:
            outbox_rows = conn.execute(text("""
                SELECT status, COUNT(*) AS count, MIN(created_at) AS oldest
                FROM notification_outbox
                WHERE status IN ('PENDING', 'PROCESSING', 'DEAD')
                GROUP BY status
            """)).mappings().all()
        except Exception:
            outbox_rows = []
        try:
            recovery_duration = (
                "(strftime('%s', sent_at) - strftime('%s', created_at))"
                if engine.dialect.name == "sqlite"
                else "EXTRACT(EPOCH FROM (sent_at - created_at))"
            )
            recovered_row = conn.execute(text(f"""
                SELECT COUNT(*) AS count,
                       AVG({recovery_duration}) AS avg_delay,
                       MAX({recovery_duration}) AS max_delay
                FROM notification_outbox
                WHERE status = 'SENT' AND sent_at >= :cutoff
            """), {"cutoff": now - timedelta(days=1)}).mappings().first()
        except Exception:
            recovered_row = None
        try:
            late_row = conn.execute(text("""
                SELECT started_at, status, result_summary
                FROM task_run_audits
                WHERE task_name IN (
                    'tasks.intraday_monitor_checkpoint', 'tasks.recover_late_formal_scan'
                )
                  AND (result_summary LIKE '%late_decision%' OR result_summary LIKE '%late_recovery%')
                ORDER BY COALESCE(finished_at, started_at) DESC
                LIMIT 1
            """)).mappings().first()
        except Exception:
            late_row = None
    delivery_total = len(notifications)
    delivery_failed = sum(1 for result in notifications if _notification_attempt_failed(result))
    outbox = {str(row["status"]): int(row["count"] or 0) for row in outbox_rows}
    pending_notifications = outbox.get("PENDING", 0) + outbox.get("PROCESSING", 0)
    dead_notifications = outbox.get("DEAD", 0)
    oldest_values = [
        row["oldest"] for row in outbox_rows
        if row.get("oldest") and str(row.get("status")) in {"PENDING", "PROCESSING"}
    ]
    oldest_pending = min(oldest_values) if oldest_values else None
    recovered_notifications = int((recovered_row or {}).get("count") or 0)
    average_recovery_seconds = round(float((recovered_row or {}).get("avg_delay") or 0), 1)
    maximum_recovery_seconds = round(float((recovered_row or {}).get("max_delay") or 0), 1)
    late_summary = {}
    if late_row and late_row.get("result_summary"):
        try:
            late_summary = json.loads(late_row["result_summary"])
        except (TypeError, json.JSONDecodeError):
            late_summary = {}
    late_scan_completed = bool(late_summary.get("formal_scan_completed"))
    backup_dir = Path(__file__).resolve().parents[1] / "backups"
    backups = sorted(backup_dir.glob("*.dump"), key=lambda path: path.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    latest_backup = datetime.fromtimestamp(backups[0].stat().st_mtime) if backups else None
    alerts = []
    if failed_tasks:
        alerts.append({"severity": "warning", "code": "TASK_FAILURE", "message": f"近24小时任务失败{failed_tasks}次"})
    if degraded_tasks:
        alerts.append({"severity": "warning", "code": "TASK_DEGRADED", "message": f"近24小时有{degraded_tasks}次任务带错误完成"})
    if delivery_failed:
        alerts.append({"severity": "warning", "code": "NOTIFICATION_INITIAL_FAILURE", "message": f"近24小时有{delivery_failed}次通知首次发送未成功"})
    if pending_notifications:
        alerts.append({"severity": "warning", "code": "NOTIFICATION_PENDING", "message": f"有{pending_notifications}条通知正在自动重试"})
    if dead_notifications:
        alerts.append({"severity": "critical", "code": "NOTIFICATION_DEAD", "message": f"有{dead_notifications}条通知已停止重试，可人工重新排队"})
    if maximum_recovery_seconds > 900:
        alerts.append({
            "severity": "warning", "code": "NOTIFICATION_RECOVERY_DELAY",
            "message": f"近24小时通知最长延迟恢复{round(maximum_recovery_seconds / 60)}分钟",
        })
    if late_row and str(late_row.get("started_at") or "")[:10] == now.strftime("%Y-%m-%d") and not late_scan_completed:
        alerts.append({"severity": "critical", "code": "LATE_FORMAL_SCAN_INCOMPLETE", "message": "今日尾盘节点未完成正式全市场扫描"})
    if latest_backup is None or latest_backup < now - timedelta(days=2):
        alerts.append({"severity": "warning", "code": "BACKUP_STALE", "message": "数据库备份缺失或超过48小时"})
    return {
        "generated_at": now.isoformat(),
        "metrics": {
            "latest_daily_date": str(latest_daily) if latest_daily else None,
            "latest_scan_at": str(latest_scan) if latest_scan else None,
            "failed_tasks_24h": failed_tasks,
            "degraded_tasks_24h": degraded_tasks,
            "notifications_24h": delivery_total,
            "notification_failures_24h": delivery_failed,
            "notification_pending": pending_notifications,
            "notification_dead": dead_notifications,
            "notification_recovered_24h": recovered_notifications,
            "notification_avg_recovery_seconds_24h": average_recovery_seconds,
            "notification_max_recovery_seconds_24h": maximum_recovery_seconds,
            "oldest_pending_at": str(oldest_pending) if oldest_pending else None,
            "latest_late_decision_at": str(late_row.get("started_at")) if late_row else None,
            "late_formal_scan_completed": int(late_scan_completed),
            "latest_backup_at": latest_backup.isoformat() if latest_backup else None,
        },
        "alerts": alerts,
        "status": "critical" if any(item["severity"] == "critical" for item in alerts) else "warning" if alerts else "ok",
    }


def render_prometheus_metrics(report: Dict[str, Any]) -> str:
    """Render non-sensitive numeric health metrics in Prometheus text format."""
    metrics = report.get("metrics") or {}
    status_value = {"ok": 0, "warning": 1, "critical": 2}.get(str(report.get("status")), 2)
    values = {
        "alphavision_operational_status": status_value,
        "alphavision_failed_tasks_24h": int(metrics.get("failed_tasks_24h") or 0),
        "alphavision_degraded_tasks_24h": int(metrics.get("degraded_tasks_24h") or 0),
        "alphavision_notifications_24h": int(metrics.get("notifications_24h") or 0),
        "alphavision_notification_failures_24h": int(metrics.get("notification_failures_24h") or 0),
        "alphavision_notification_pending": int(metrics.get("notification_pending") or 0),
        "alphavision_notification_dead": int(metrics.get("notification_dead") or 0),
        "alphavision_notification_recovered_24h": int(metrics.get("notification_recovered_24h") or 0),
        "alphavision_notification_max_recovery_seconds_24h": int(metrics.get("notification_max_recovery_seconds_24h") or 0),
        "alphavision_late_formal_scan_completed": int(metrics.get("late_formal_scan_completed") or 0),
        "alphavision_active_alerts": len(report.get("alerts") or []),
    }
    lines = [
        "# HELP alphavision_operational_status Overall status: 0=ok, 1=warning, 2=critical.",
        "# TYPE alphavision_operational_status gauge",
    ]
    lines.extend(f"{name} {value}" for name, value in values.items())
    return "\n".join(lines) + "\n"
