from datetime import datetime, timedelta
from fastapi import APIRouter
from sqlalchemy import text

from core.db import get_db_engine
from core.data_source_quality import build_data_source_quality_report
from core.data_source_quality import build_local_data_quality_report
from core.ops_summary import build_ops_summary
from core.portfolio_risk import build_portfolio_exposure
from core.pro_workflow import build_premarket_checklist, recommend_strategy_template
from core.research_summary import build_research_summary
from core.system_health import build_system_health_snapshot
from core.strategy_health import build_strategy_health
from core.source_comparison import compare_history_sources
from core.db import validate_stock_code

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def get_system_health():
    """Return an operational readiness snapshot for daily trading workflow."""
    return build_system_health_snapshot(get_db_engine())


@router.get("/data-sources")
def get_data_source_quality():
    """Return multi-source availability and quality diagnostics."""
    return build_data_source_quality_report(get_db_engine())


@router.get("/data-repair-candidates")
def get_data_repair_candidates(target_date: str | None = None):
    """Return suspected adjustment-gap repair candidates without mutating market data."""
    engine = get_db_engine()
    report = build_local_data_quality_report(engine, target_date=target_date)
    summary = report.get("summary") or {}
    samples = [
        item for item in (summary.get("abnormal_move_samples") or [])
        if item.get("likely_reason") == "suspected_corporate_action_gap"
    ]
    selected_date = summary.get("selected_date")
    command = (
        f"cd backend && source venv_new/bin/activate && "
        f"python scripts/repair_adjustment_gaps.py --target-date {selected_date} --apply"
        if selected_date and samples else ""
    )
    return {
        "status": report.get("status"),
        "selected_date": selected_date,
        "count": len(samples),
        "items": samples,
        "recommended_command": command,
    }


@router.get("/portfolio-exposure")
def get_portfolio_exposure():
    """Return open-position sector/strategy concentration against risk limits."""
    return build_portfolio_exposure(get_db_engine())


@router.get("/premarket-checklist")
def get_premarket_checklist(market_regime: str = "UNKNOWN", recent_win_rate: float = 0):
    """Return the daily pre-market checklist for scan and trading readiness."""
    engine = get_db_engine()
    health = build_system_health_snapshot(engine)
    exposure = build_portfolio_exposure(engine)
    data_quality = build_data_source_quality_report(engine)
    template = recommend_strategy_template(
        market_regime=market_regime,
        risk_status=exposure.get("status", "ok"),
        recent_win_rate=float(recent_win_rate or 0),
    )
    checklist = build_premarket_checklist(health, exposure, data_quality, template)
    return {
        **checklist,
        "template_recommendation": template,
        "exposure_summary": exposure.get("summary", {}),
        "health_score": health.get("score", 0),
    }


@router.get("/ops-summary")
def get_ops_summary(limit: int = 50):
    """Return aggregated operational quality metrics for the ops dashboard."""
    return build_ops_summary(get_db_engine(), limit=limit)


@router.get("/research-summary")
def get_research_summary(limit: int = 500):
    """Return recent scan-result distribution metrics for strategy research."""
    return build_research_summary(get_db_engine(), limit=limit)


@router.get("/strategy-health")
def get_strategy_health(days: int = 120):
    """Return verified strategy health used by automatic scan controls."""
    return build_strategy_health(get_db_engine(), days=days)


@router.get("/scan-audits")
def get_scan_audits(limit: int = 20):
    """Return recent scan audit logs for operational review."""
    engine = get_db_engine()
    if not engine:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT scan_date, started_at, finished_at, duration_sec, status, strategy_type,
                   total_snapshot, candidate_count, result_count, fail_reasons, error_message
            FROM scan_audit_log
            ORDER BY started_at DESC
            LIMIT :limit
        """), {"limit": min(max(limit, 1), 100)}).mappings().all()
    return [dict(row) for row in rows]


@router.get("/failure-samples")
def get_failure_samples(limit: int = 50):
    """Return recent failure samples for strategy review."""
    engine = get_db_engine()
    if not engine:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, name, sample_date, strategy_type, failure_type, reason, pnl_pct, source, created_at
            FROM failure_samples
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"limit": min(max(limit, 1), 200)}).mappings().all()
    return [dict(row) for row in rows]


@router.get("/conversion-funnel")
def get_conversion_funnel(days: int = 30):
    """Return scan-to-close lifecycle counts for the recent period."""
    engine = get_db_engine()
    if not engine:
        return {"days": days, "stages": [], "rates": {}}
    stages = [
        ("SCAN_RECOMMENDED", "扫描推荐"),
        ("WATCHLIST_ADDED", "加入观察池"),
        ("WATCHLIST_TRIGGERED", "观察池触发"),
        ("PAPER_OPENED", "拟合实盘"),
        ("REAL_CONVERTED", "转为实盘"),
        ("TRADE_CLOSED", "已平仓"),
    ]
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT event_type, COUNT(*) AS count
            FROM lifecycle_events
            WHERE event_time >= :cutoff
            GROUP BY event_type
        """), {"cutoff": datetime.now() - timedelta(days=min(max(days, 1), 365))}).mappings().all()
    counts = {row["event_type"]: int(row["count"]) for row in rows}
    values = [{"key": key, "label": label, "count": counts.get(key, 0)} for key, label in stages]
    watch_count = counts.get("WATCHLIST_ADDED", 0)
    paper_count = counts.get("PAPER_OPENED", 0)
    return {
        "days": days,
        "stages": values,
        "rates": {
            "watch_to_trigger_pct": round(counts.get("WATCHLIST_TRIGGERED", 0) / watch_count * 100, 1) if watch_count else 0,
            "watch_to_paper_pct": round(paper_count / watch_count * 100, 1) if watch_count else 0,
            "paper_to_real_pct": round(counts.get("REAL_CONVERTED", 0) / paper_count * 100, 1) if paper_count else 0,
        },
    }


@router.get("/task-runs")
def get_task_runs(limit: int = 30):
    engine = get_db_engine()
    if not engine:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT task_id, task_name, status, started_at, finished_at, duration_sec, result_summary, error_message
            FROM task_run_audits ORDER BY COALESCE(started_at, finished_at) DESC LIMIT :limit
        """), {"limit": min(max(limit, 1), 100)}).mappings().all()
    return [dict(row) for row in rows]


@router.get("/notification-audits")
def get_notification_audits(limit: int = 30):
    engine = get_db_engine()
    if not engine:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT sent_at, title, channels, results, group_name, body_preview
            FROM notification_audits ORDER BY sent_at DESC LIMIT :limit
        """), {"limit": min(max(limit, 1), 100)}).mappings().all()
    return [dict(row) for row in rows]


@router.get("/source-comparison")
def get_source_comparison(codes: str = "000001,600519", lookback_days: int = 14):
    selected = list(dict.fromkeys(code.strip() for code in codes.replace("，", ",").split(",") if code.strip()))
    if not selected or len(selected) > 3 or any(not validate_stock_code(code) for code in selected):
        return {"status": "error", "detail": "codes must contain 1-3 valid stock codes"}
    return compare_history_sources(selected, lookback_days=min(max(lookback_days, 7), 60))
