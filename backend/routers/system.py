from fastapi import APIRouter
from sqlalchemy import text

from core.db import get_db_engine
from core.data_source_quality import build_data_source_quality_report
from core.ops_summary import build_ops_summary
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
