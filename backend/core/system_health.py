from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine


CORE_TABLES = [
    "stock_basic",
    "daily_k",
    "scan_history",
    "paper_trading",
    "system_settings",
    "stock_fundamentals",
    "watchlist",
    "strategy_templates",
]


def _safe_scalar(conn, sql: str, default: Any = None) -> Any:
    try:
        return conn.execute(text(sql)).scalar()
    except Exception:
        return default


def _iso_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def build_system_health_snapshot(engine: Optional[Engine]) -> Dict[str, Any]:
    """Build an operational health snapshot without mutating local data."""
    now = datetime.now()
    if engine is None:
        return {
            "status": "error",
            "time": now.isoformat(),
            "score": 0,
            "checks": [{
                "name": "database_connection",
                "status": "error",
                "message": "数据库连接不可用",
            }],
            "summary": {},
            "recommendations": ["检查 db_config.json 或数据库服务状态"],
        }

    checks: List[Dict[str, Any]] = []
    recommendations: List[str] = []
    summary: Dict[str, Any] = {}

    try:
        with engine.connect() as conn:
            stock_count = int(_safe_scalar(conn, "SELECT COUNT(*) FROM stock_basic", 0) or 0)
            daily_count = int(_safe_scalar(conn, "SELECT COUNT(*) FROM daily_k", 0) or 0)
            scan_count = int(_safe_scalar(conn, "SELECT COUNT(*) FROM scan_history", 0) or 0)
            open_positions = int(_safe_scalar(conn, "SELECT COUNT(*) FROM paper_trading WHERE status = 'OPEN'", 0) or 0)
            watch_count = int(_safe_scalar(conn, "SELECT COUNT(*) FROM watchlist WHERE status = 'WATCHING'", 0) or 0)
            template_count = int(_safe_scalar(conn, "SELECT COUNT(*) FROM strategy_templates", 0) or 0)
            fundamental_count = int(_safe_scalar(conn, "SELECT COUNT(*) FROM stock_fundamentals", 0) or 0)
            latest_daily = _iso_date(_safe_scalar(conn, "SELECT MAX(date) FROM daily_k"))
            latest_scan = _iso_date(_safe_scalar(conn, "SELECT MAX(date) FROM scan_history"))
            bark_key = _safe_scalar(conn, "SELECT value FROM system_settings WHERE key = 'bark_key'", "")

            table_counts = {}
            missing_tables = []
            for table in CORE_TABLES:
                count = _safe_scalar(conn, f"SELECT COUNT(*) FROM {table}", None)
                if count is None:
                    missing_tables.append(table)
                else:
                    table_counts[table] = int(count)

    except Exception as exc:
        return {
            "status": "error",
            "time": now.isoformat(),
            "score": 0,
            "checks": [{
                "name": "database_query",
                "status": "error",
                "message": f"数据库查询失败: {str(exc)[:120]}",
            }],
            "summary": {},
            "recommendations": ["检查数据库表结构和连接权限"],
        }

    summary.update({
        "stock_count": stock_count,
        "daily_k_count": daily_count,
        "scan_history_count": scan_count,
        "open_positions": open_positions,
        "watchlist_count": watch_count,
        "strategy_template_count": template_count,
        "fundamental_count": fundamental_count,
        "latest_daily_date": latest_daily,
        "latest_scan_date": latest_scan,
        "table_counts": table_counts,
    })

    checks.append({
        "name": "database_connection",
        "status": "ok",
        "message": "数据库连接正常",
    })

    if missing_tables:
        checks.append({
            "name": "schema_integrity",
            "status": "error",
            "message": f"缺少核心表: {', '.join(missing_tables)}",
        })
        recommendations.append("运行数据库初始化/迁移，确保核心表完整")
    else:
        checks.append({
            "name": "schema_integrity",
            "status": "ok",
            "message": "核心表结构可查询",
        })

    if stock_count >= 4000 and daily_count >= stock_count * 120:
        data_status = "ok"
        data_msg = "股票基础数据和日线样本量充足"
    elif stock_count > 0 and daily_count > 0:
        data_status = "warn"
        data_msg = "已有行情数据，但样本覆盖可能不足"
        recommendations.append("同步全市场基础信息和至少 120 个交易日 K 线")
    else:
        data_status = "error"
        data_msg = "行情数据为空，无法支撑专业扫描"
        recommendations.append("先执行本地行情同步，再进行全市场扫描")
    checks.append({"name": "market_data_coverage", "status": data_status, "message": data_msg})

    if fundamental_count >= max(1, int(stock_count * 0.5)):
        checks.append({"name": "fundamental_coverage", "status": "ok", "message": "基本面覆盖较完整"})
    else:
        checks.append({"name": "fundamental_coverage", "status": "warn", "message": "基本面覆盖不足，评分可能偏技术面"})
        recommendations.append("补充 stock_fundamentals，提高综合评分可信度")

    if latest_scan:
        checks.append({"name": "scan_history", "status": "ok", "message": f"最近扫描日期 {latest_scan}"})
    else:
        checks.append({"name": "scan_history", "status": "warn", "message": "暂无扫描历史，复盘中心样本不足"})
        recommendations.append("完成至少一次策略扫描，建立复盘基线")

    if template_count >= 3:
        checks.append({"name": "strategy_templates", "status": "ok", "message": "策略模板已配置"})
    else:
        checks.append({"name": "strategy_templates", "status": "warn", "message": "策略模板偏少"})
        recommendations.append("为稳健、进攻、防守市场分别保存策略模板")

    if bark_key:
        checks.append({"name": "notification", "status": "ok", "message": "Bark 推送已配置"})
    else:
        checks.append({"name": "notification", "status": "warn", "message": "Bark 推送未配置"})
        recommendations.append("配置 Bark Key，确保风险告警能触达手机")

    error_count = sum(1 for item in checks if item["status"] == "error")
    warn_count = sum(1 for item in checks if item["status"] == "warn")
    score = max(0, 100 - error_count * 35 - warn_count * 10)
    status = "ok" if error_count == 0 and warn_count <= 1 else "warn"
    if error_count:
        status = "error"

    return {
        "status": status,
        "time": now.isoformat(),
        "score": score,
        "checks": checks,
        "summary": summary,
        "recommendations": list(dict.fromkeys(recommendations)),
    }
