"""Structured operational metrics and alert rules without external dependencies."""
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import text


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
        notifications = conn.execute(text("SELECT results FROM notification_audits WHERE sent_at>=:cutoff"), {"cutoff": now - timedelta(days=1)}).scalars().all()
    delivery_total = len(notifications)
    delivery_failed = sum(1 for result in notifications if 'false' in str(result).lower())
    backup_dir = Path(__file__).resolve().parents[1] / "backups"
    backups = sorted(backup_dir.glob("*.dump"), key=lambda path: path.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    latest_backup = datetime.fromtimestamp(backups[0].stat().st_mtime) if backups else None
    alerts = []
    if failed_tasks:
        alerts.append({"severity": "warning", "code": "TASK_FAILURE", "message": f"近24小时任务失败{failed_tasks}次"})
    if delivery_failed:
        alerts.append({"severity": "critical", "code": "NOTIFICATION_FAILURE", "message": f"近24小时通知失败{delivery_failed}次"})
    if latest_backup is None or latest_backup < now - timedelta(days=2):
        alerts.append({"severity": "warning", "code": "BACKUP_STALE", "message": "数据库备份缺失或超过48小时"})
    return {
        "generated_at": now.isoformat(),
        "metrics": {
            "latest_daily_date": str(latest_daily) if latest_daily else None,
            "latest_scan_at": str(latest_scan) if latest_scan else None,
            "failed_tasks_24h": failed_tasks,
            "notifications_24h": delivery_total,
            "notification_failures_24h": delivery_failed,
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
        "alphavision_notifications_24h": int(metrics.get("notifications_24h") or 0),
        "alphavision_notification_failures_24h": int(metrics.get("notification_failures_24h") or 0),
        "alphavision_active_alerts": len(report.get("alerts") or []),
    }
    lines = [
        "# HELP alphavision_operational_status Overall status: 0=ok, 1=warning, 2=critical.",
        "# TYPE alphavision_operational_status gauge",
    ]
    lines.extend(f"{name} {value}" for name, value in values.items())
    return "\n".join(lines) + "\n"
