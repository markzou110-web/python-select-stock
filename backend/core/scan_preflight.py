from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from core.data_source_quality import build_local_data_quality_report


def _date_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def build_scan_preflight(
    engine: Optional[Engine],
    data_date: Optional[str] = None,
    min_stock_count: int = 1000,
    min_history_days: int = 120,
) -> Dict[str, Any]:
    """Check whether local data is ready before submitting an expensive scan."""
    if engine is None:
        return {
            "status": "error",
            "blocking": True,
            "message": "数据库连接不可用，无法执行本地扫描",
            "checks": [{"name": "database", "status": "error", "message": "数据库连接不可用"}],
            "summary": {},
        }

    checks = []
    summary: Dict[str, Any] = {
        "requested_date": data_date or "",
        "min_stock_count": min_stock_count,
        "min_history_days": min_history_days,
    }

    try:
        local_quality = build_local_data_quality_report(
            engine,
            target_date=data_date,
            min_stock_count=min_stock_count,
        )
        for item in local_quality.get("checks", []):
            if item.get("name") not in {"latest_coverage"}:
                checks.append(item)
        summary["local_data_quality"] = local_quality.get("summary", {})

        with engine.connect() as conn:
            if data_date:
                row = conn.execute(
                    text("""
                        SELECT date, COUNT(DISTINCT code) AS stock_count
                        FROM daily_k
                        WHERE date = :data_date
                        GROUP BY date
                    """),
                    {"data_date": data_date},
                ).fetchone()
            else:
                row = conn.execute(
                    text("""
                        SELECT date, COUNT(DISTINCT code) AS stock_count
                        FROM daily_k
                        GROUP BY date
                        ORDER BY date DESC
                        LIMIT 1
                    """)
                ).fetchone()

            if not row:
                return {
                    "status": "error",
                    "blocking": True,
                    "message": "未找到可用日线数据",
                    "checks": [{"name": "data_date", "status": "error", "message": "daily_k 中没有目标日期数据"}],
                    "summary": summary,
                }

            selected_date = _date_str(row[0])
            stock_count = int(row[1] or 0)
            summary["selected_date"] = selected_date
            summary["stock_count"] = stock_count

            history_row = conn.execute(
                text("""
                    SELECT COUNT(*) AS stock_count
                    FROM (
                        SELECT code
                        FROM daily_k
                        WHERE date <= :selected_date
                        GROUP BY code
                        HAVING COUNT(*) >= :min_history_days
                    ) ready
                """),
                {"selected_date": selected_date, "min_history_days": min_history_days},
            ).fetchone()
            ready_history_count = int(history_row[0] or 0) if history_row else 0
            summary["ready_history_count"] = ready_history_count
    except Exception as exc:
        return {
            "status": "error",
            "blocking": True,
            "message": f"扫描预检失败: {str(exc)[:120]}",
            "checks": [{"name": "preflight_query", "status": "error", "message": "预检查询失败"}],
            "summary": summary,
        }

    if stock_count >= min_stock_count:
        checks.append({"name": "date_coverage", "status": "ok", "message": f"{selected_date} 有 {stock_count} 只股票数据"})
    elif stock_count > 0:
        checks.append({"name": "date_coverage", "status": "warn", "message": f"{selected_date} 仅 {stock_count} 只股票数据"})
    else:
        checks.append({"name": "date_coverage", "status": "error", "message": f"{selected_date} 无股票数据"})

    if ready_history_count >= min_stock_count:
        checks.append({"name": "history_depth", "status": "ok", "message": f"{ready_history_count} 只股票满足 {min_history_days} 日历史"})
    elif ready_history_count > 0:
        checks.append({"name": "history_depth", "status": "warn", "message": f"仅 {ready_history_count} 只股票满足 {min_history_days} 日历史"})
    else:
        checks.append({"name": "history_depth", "status": "warn", "message": "没有股票满足最小历史天数，扫描结果可能为空"})

    error_count = sum(1 for item in checks if item["status"] == "error")
    warn_count = sum(1 for item in checks if item["status"] == "warn")
    if error_count:
        status = "error"
    elif warn_count:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "blocking": bool(error_count),
        "message": "扫描预检通过" if status == "ok" else "扫描预检存在风险",
        "checks": checks,
        "summary": summary,
    }
