from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _date_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_suspected_corporate_action_gap(name: str, close_jump_pct: Optional[float], open_gap_pct: Optional[float]) -> bool:
    """Identify ex-right adjustment discontinuities without calling an external data source."""
    normalized_name = (name or "").upper()
    if normalized_name.startswith(("XR", "DR", "XD")):
        return True
    if close_jump_pct is None or open_gap_pct is None:
        return False
    return close_jump_pct <= -20 and open_gap_pct <= -15 and abs(close_jump_pct - open_gap_pct) <= 8


def build_local_data_quality_report(
    engine: Optional[Engine],
    target_date: Optional[str] = None,
    min_stock_count: int = 1000,
    max_abnormal_move_pct: float = 25.0,
) -> Dict[str, Any]:
    """Check local market data freshness, coverage, industry mapping, and price jumps."""
    if engine is None:
        return {
            "status": "error",
            "blocking": True,
            "checks": [{"name": "database", "status": "error", "message": "数据库连接不可用"}],
            "summary": {},
            "recommendations": ["检查数据库服务和连接配置"],
        }

    checks = []
    recommendations = []
    summary: Dict[str, Any] = {
        "target_date": target_date or "",
        "min_stock_count": int(min_stock_count),
    }

    try:
        with engine.connect() as conn:
            if target_date:
                latest_row = conn.execute(text("""
                    SELECT date, COUNT(DISTINCT code) AS stock_count
                    FROM daily_k
                    WHERE date = :target_date
                    GROUP BY date
                """), {"target_date": target_date}).fetchone()
            else:
                latest_row = conn.execute(text("""
                    SELECT date, COUNT(DISTINCT code) AS stock_count
                    FROM daily_k
                    GROUP BY date
                    ORDER BY date DESC
                    LIMIT 1
                """)).fetchone()

            if not latest_row:
                return {
                    "status": "error",
                    "blocking": True,
                    "checks": [{"name": "latest_daily", "status": "error", "message": "daily_k 没有可用日线数据"}],
                    "summary": summary,
                    "recommendations": ["先执行行情同步，再运行扫描"],
                }

            selected_date = _date_str(latest_row[0])
            stock_count = int(latest_row[1] or 0)
            summary["selected_date"] = selected_date
            summary["stock_count"] = stock_count

            prev_row = conn.execute(text("""
                SELECT date, COUNT(DISTINCT code) AS stock_count
                FROM daily_k
                WHERE date < :selected_date
                GROUP BY date
                ORDER BY date DESC
                LIMIT 1
            """), {"selected_date": selected_date}).fetchone()
            prev_stock_count = int(prev_row[1] or 0) if prev_row else 0
            summary["previous_date"] = _date_str(prev_row[0]) if prev_row else None
            summary["previous_stock_count"] = prev_stock_count
            missing_vs_previous = max(prev_stock_count - stock_count, 0)
            summary["missing_vs_previous"] = missing_vs_previous
            summary["coverage_ratio"] = round(stock_count / prev_stock_count * 100, 1) if prev_stock_count > 0 else 100

            industry_row = conn.execute(text("""
                SELECT COUNT(DISTINCT d.code) AS missing_industry
                FROM daily_k d
                LEFT JOIN stock_basic s ON s.code = d.code
                WHERE d.date = :selected_date
                  AND (s.industry IS NULL OR s.industry = '' OR s.industry = '未知')
            """), {"selected_date": selected_date}).fetchone()
            missing_industry = int(industry_row[0] or 0) if industry_row else 0
            summary["missing_industry_count"] = missing_industry

            abnormal_rows = conn.execute(text("""
                WITH ranked AS (
                    SELECT code, date, open, close,
                           LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev_close
                    FROM daily_k
                    WHERE date <= :selected_date
                )
                SELECT
                    r.code,
                    COALESCE(s.name, '') AS name,
                    r.prev_close,
                    r.open,
                    r.close,
                    (r.open - r.prev_close) / r.prev_close * 100 AS open_gap_pct,
                    (r.close - r.prev_close) / r.prev_close * 100 AS close_jump_pct
                FROM ranked r
                LEFT JOIN stock_basic s ON s.code = r.code
                WHERE r.date = :selected_date
                  AND r.prev_close IS NOT NULL
                  AND r.prev_close > 0
                  AND ABS((r.close - r.prev_close) / r.prev_close * 100) > :max_move
                ORDER BY ABS((r.close - r.prev_close) / r.prev_close * 100) DESC
            """), {"selected_date": selected_date, "max_move": float(max_abnormal_move_pct)}).fetchall()
            abnormal_samples = []
            suspected_corporate_action_count = 0
            for row in abnormal_rows:
                open_gap_pct = _float_or_none(row[5])
                close_jump_pct = _float_or_none(row[6])
                suspected = _is_suspected_corporate_action_gap(row[1], close_jump_pct, open_gap_pct)
                if suspected:
                    suspected_corporate_action_count += 1
                if len(abnormal_samples) < 10:
                    abnormal_samples.append({
                        "code": row[0],
                        "name": row[1],
                        "prev_close": round(float(row[2]), 3) if row[2] is not None else None,
                        "open": round(float(row[3]), 3) if row[3] is not None else None,
                        "close": round(float(row[4]), 3) if row[4] is not None else None,
                        "open_gap_pct": round(open_gap_pct, 2) if open_gap_pct is not None else None,
                        "close_jump_pct": round(close_jump_pct, 2) if close_jump_pct is not None else None,
                        "likely_reason": "suspected_corporate_action_gap" if suspected else "abnormal_price_move",
                    })
            abnormal_count = len(abnormal_rows)
            summary["abnormal_move_count"] = abnormal_count
            summary["suspected_corporate_action_gap_count"] = suspected_corporate_action_count
            summary["abnormal_move_samples"] = abnormal_samples

            zero_row = conn.execute(text("""
                SELECT COUNT(*) FROM daily_k
                WHERE date = :selected_date
                  AND (open <= 0 OR high <= 0 OR low <= 0 OR close <= 0 OR vol IS NULL OR vol < 0)
            """), {"selected_date": selected_date}).fetchone()
            invalid_price_count = int(zero_row[0] or 0) if zero_row else 0
            summary["invalid_price_count"] = invalid_price_count
    except Exception as exc:
        return {
            "status": "error",
            "blocking": True,
            "checks": [{"name": "local_data_query", "status": "error", "message": f"本地数据质量查询失败: {str(exc)[:120]}"}],
            "summary": summary,
            "recommendations": ["检查 daily_k / stock_basic 表结构和数据库权限"],
        }

    if stock_count >= min_stock_count:
        checks.append({"name": "latest_coverage", "status": "ok", "message": f"{selected_date} 覆盖 {stock_count} 只股票"})
    elif stock_count > 0:
        checks.append({"name": "latest_coverage", "status": "warn", "message": f"{selected_date} 仅覆盖 {stock_count} 只股票"})
        recommendations.append("最新交易日日线覆盖不足，建议重新同步行情")
    else:
        checks.append({"name": "latest_coverage", "status": "error", "message": f"{selected_date} 无股票数据"})

    coverage_ratio = float(summary.get("coverage_ratio") or 100)
    if prev_stock_count and coverage_ratio < 90:
        checks.append({"name": "coverage_drop", "status": "warn", "message": f"较上一交易日缺失 {missing_vs_previous} 只，覆盖率 {coverage_ratio}%"})
        recommendations.append("最新日线较上一交易日明显缺失，扫描前建议补齐数据")
    else:
        checks.append({"name": "coverage_drop", "status": "ok", "message": "最新日线覆盖未见明显断层"})

    if missing_industry > max(20, stock_count * 0.05):
        checks.append({"name": "industry_mapping", "status": "warn", "message": f"{missing_industry} 只股票缺少行业映射"})
        recommendations.append("补齐 stock_basic 行业信息，避免板块强度判断失真")
    else:
        checks.append({"name": "industry_mapping", "status": "ok", "message": "行业映射覆盖正常"})

    suspected_gap_count = int(summary.get("suspected_corporate_action_gap_count") or 0)
    unresolved_abnormal_count = max(abnormal_count - suspected_gap_count, 0)
    if abnormal_count > 0:
        if unresolved_abnormal_count == 0:
            checks.append({
                "name": "abnormal_move",
                "status": "warn",
                "message": f"{abnormal_count} 只股票疑似除权复权断点，需统一复权口径后再解读涨跌幅",
            })
        elif suspected_gap_count > 0:
            checks.append({
                "name": "abnormal_move",
                "status": "warn",
                "message": f"{abnormal_count} 只股票异常跳变，其中 {suspected_gap_count} 只疑似除权复权断点",
            })
        else:
            checks.append({"name": "abnormal_move", "status": "warn", "message": f"{abnormal_count} 只股票出现超过 {max_abnormal_move_pct:.0f}% 的异常跳变"})
        recommendations.append("对疑似除权复权断点股票补刷同一复权口径的历史K线，避免假信号进入扫描")
        if unresolved_abnormal_count > 0:
            recommendations.append("检查非除权类异常跳变是否来自数据源脏数据或停复牌特殊行情")
    else:
        checks.append({"name": "abnormal_move", "status": "ok", "message": "异常价格跳变数量可接受"})

    if invalid_price_count > 0:
        checks.append({"name": "invalid_price", "status": "error", "message": f"{invalid_price_count} 条最新K线价格或成交量无效"})
        recommendations.append("清洗无效 K 线后再扫描")
    else:
        checks.append({"name": "invalid_price", "status": "ok", "message": "最新K线价格与成交量有效"})

    error_count = sum(1 for item in checks if item["status"] == "error")
    warn_count = sum(1 for item in checks if item["status"] == "warn")
    status = "error" if error_count else ("warn" if warn_count else "ok")

    return {
        "status": status,
        "blocking": bool(error_count),
        "checks": checks,
        "summary": summary,
        "recommendations": list(dict.fromkeys(recommendations)),
    }


def build_data_source_quality_report(engine: Optional[Engine] = None) -> Dict[str, Any]:
    """Return current multi-source availability and a concise professional diagnosis."""
    try:
        from core.multi_source_sync import MultiSourceSync

        syncer = MultiSourceSync()
        sources = syncer.manager.get_status_report()
    except Exception as exc:
        return {
            "status": "error",
            "available_count": 0,
            "sources": {},
            "message": f"数据源状态检查失败: {str(exc)[:120]}",
            "recommendations": ["检查网络、代理和数据源依赖"],
            "local_data": build_local_data_quality_report(engine) if engine is not None else {},
        }

    available = [name for name, info in sources.items() if info.get("status") == "available"]
    degraded = [name for name, info in sources.items() if info.get("status") != "available"]
    status = "ok" if len(available) >= 2 else ("warn" if available else "error")

    recommendations = []
    if len(available) < 2:
        recommendations.append("至少保持两个可用数据源，避免单点数据故障")
    if degraded:
        recommendations.append(f"关注异常数据源: {', '.join(degraded)}")

    local_data = build_local_data_quality_report(engine) if engine is not None else {}
    combined_status = status
    if local_data.get("status") == "error":
        combined_status = "error"
    elif local_data.get("status") == "warn" and combined_status == "ok":
        combined_status = "warn"

    return {
        "status": combined_status,
        "available_count": len(available),
        "total_count": len(sources),
        "sources": sources,
        "message": f"可用数据源 {len(available)}/{len(sources)}",
        "recommendations": list(dict.fromkeys(recommendations + (local_data.get("recommendations") or []))),
        "local_data": local_data,
    }
