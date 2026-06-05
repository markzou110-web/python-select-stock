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
