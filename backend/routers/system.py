from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import text

from core.db import get_db_engine, load_active_event_catalysts, save_event_catalyst
from core.data_source_quality import build_data_source_quality_report
from core.data_source_quality import build_local_data_quality_report
from core.ops_summary import build_ops_summary
from core.portfolio_risk import build_portfolio_exposure, build_portfolio_stress
from core.pro_workflow import build_premarket_checklist, recommend_strategy_template
from core.research_summary import build_research_summary
from core.system_health import build_system_health_snapshot
from core.strategy_health import build_strategy_health
from core.source_comparison import compare_history_sources
from core.db import validate_stock_code
from core.bark_health import build_bark_self_check, send_bark_self_check
from core.strategy_release import evaluate_challenger
from core.execution_replay import run_historical_execution_replay
from core.point_in_time_warehouse import build_point_in_time_coverage
from core.strategy_governance import list_strategy_states, transition_strategy
from core.operational_metrics import build_operational_metrics, render_prometheus_metrics
from core.lookahead_audit import audit_causal_consistency

router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/strategy-release/evaluate")
def evaluate_strategy_release(payload: dict):
    """Evaluate a challenger; this endpoint never promotes or mutates production state."""
    return evaluate_challenger(payload)


@router.get("/strategy-release/states")
def get_strategy_release_states():
    return list_strategy_states(get_db_engine())


@router.post("/strategy-release/{strategy_key}/transition")
def post_strategy_transition(strategy_key: str, payload: dict):
    return transition_strategy(
        get_db_engine(), strategy_key, str(payload.get("target") or ""),
        payload.get("evidence") or {}, str(payload.get("reason") or ""), str(payload.get("version") or ""),
    )


@router.post("/event-catalysts")
def upsert_event_catalyst(payload: dict):
    """Store a verified point-in-time catalyst; it only creates strong-watch candidates."""
    try:
        published_at = datetime.fromisoformat(str(payload.get("published_at")))
        growth_low = float(payload.get("profit_growth_low"))
        growth_high = float(payload.get("profit_growth_high"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="published_at and profit growth range are required")
    event = {
        **payload,
        "published_at": published_at,
        "profit_growth_low": growth_low,
        "profit_growth_high": growth_high,
        "event_type": payload.get("event_type") or "EARNINGS_SURPRISE",
    }
    if not event.get("source_url") or not payload.get("verified"):
        raise HTTPException(status_code=400, detail="verified official source_url is required")
    if not save_event_catalyst(event, get_db_engine()):
        raise HTTPException(status_code=400, detail="failed to save event catalyst")
    return {"saved": True, "code": event.get("code"), "event_type": event["event_type"]}


@router.get("/event-catalysts")
def get_event_catalysts(days: int = 10):
    return {"items": list(load_active_event_catalysts(get_db_engine(), days=days).values())}


@router.post("/event-catalysts/discover")
def discover_event_catalysts(trade_date: str | None = None):
    from core.event_ingestion import discover_official_event_catalysts
    return discover_official_event_catalysts(get_db_engine(), trade_date=trade_date)


@router.get("/health")
def get_system_health():
    """Return an operational readiness snapshot for daily trading workflow."""
    return build_system_health_snapshot(get_db_engine())


@router.get("/strategy-evidence")
def get_strategy_evidence(days: int = 120):
    """Separate candidate health from current execution-policy evidence."""
    engine = get_db_engine()
    return {
        "candidate_health": build_strategy_health(engine, days=days),
        "execution_policy": run_historical_execution_replay(engine, days=days) if engine else {},
        "layers": ["research_candidate", "bark_executable", "real_trade"],
    }


@router.get("/point-in-time-coverage")
def get_point_in_time_coverage():
    return build_point_in_time_coverage(get_db_engine())


@router.get("/operational-metrics")
def get_operational_metrics():
    return build_operational_metrics(get_db_engine())


@router.get("/operational-metrics/prometheus", response_class=Response)
def get_operational_metrics_prometheus():
    report = build_operational_metrics(get_db_engine())
    return Response(render_prometheus_metrics(report), media_type="text/plain; version=0.0.4")


@router.get("/lookahead-audit/{code}")
def get_lookahead_audit(code: str, lookback: int = 300, sample_points: int = 20):
    normalized = str(code).strip()
    if not validate_stock_code(normalized):
        raise HTTPException(status_code=400, detail="invalid stock code")
    lookback = min(max(int(lookback), 100), 1000)
    sample_points = min(max(int(sample_points), 1), 100)
    with get_db_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT date AS "日期", open AS "开盘", high AS "最高", low AS "最低",
                   close AS "收盘", vol AS "成交量"
            FROM daily_k WHERE code=:code ORDER BY date DESC LIMIT :lookback
        """), {"code": normalized, "lookback": lookback}).mappings().all()
    import pandas as pd
    return {"code": normalized, **audit_causal_consistency(pd.DataFrame(rows), sample_points=sample_points)}


@router.post("/bark-self-check")
def post_bark_self_check(notify: bool = False):
    """Build or send a Bark readiness report without exposing private keys."""
    return send_bark_self_check() if notify else build_bark_self_check(get_db_engine())


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


@router.get("/portfolio-stress")
def get_portfolio_stress(lookback: int = 60):
    return build_portfolio_stress(get_db_engine(), lookback=lookback)


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
                   total_snapshot, candidate_count, result_count, fail_reasons, error_message,
                   as_of, data_mode, field_coverage, effective_filters, research_only,
                   degradation_reasons
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


@router.get("/task-slots")
def get_task_slots(limit: int = 100):
    engine = get_db_engine()
    if not engine:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT slot_key, claimed_at, status FROM task_slot_claims ORDER BY claimed_at DESC LIMIT :limit"), {"limit": min(max(limit, 1), 500)}).mappings().all()
    return [dict(row) for row in rows]


@router.delete("/task-slots/{slot_key:path}")
def reset_task_slot(slot_key: str):
    from core.task_idempotency import release_slot
    return {"released": release_slot(slot_key, get_db_engine()), "slot_key": slot_key}


@router.get("/schema-migrations")
def get_schema_migrations():
    engine = get_db_engine()
    if not engine:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version, applied_at, description FROM schema_migrations ORDER BY applied_at DESC")).mappings().all()
    return [dict(row) for row in rows]


@router.get("/notification-audits")
def get_notification_audits(limit: int = 30):
    engine = get_db_engine()
    if not engine:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, sent_at, title, channels, results, group_name, body_preview
            FROM notification_audits ORDER BY sent_at DESC LIMIT :limit
        """), {"limit": min(max(limit, 1), 100)}).mappings().all()
    return [dict(row) for row in rows]


@router.post("/notification-audits/{audit_id}/retry")
async def retry_notification_audit(audit_id: int):
    """Explicitly retry one failed audit; never retries successful or missing records."""
    engine = get_db_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="Database unavailable")
    with engine.connect() as conn:
        row = conn.execute(text("SELECT title, results, body_preview FROM notification_audits WHERE id=:id"), {"id": audit_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="notification audit not found")
    results = row.get("results") or {}
    if isinstance(results, str):
        import json
        results = json.loads(results)
    if results.get("bark") is True:
        return {"retried": False, "reason": "already_successful"}
    preview = row.get("body_preview") or ""
    if "指令：可交易" in preview or "必须立即处理" in preview or "止损" in preview:
        return {
            "retried": False,
            "reason": "time_sensitive_instruction_requires_regeneration",
            "action": "重新运行当前扫描/风控，禁止补发可能过期或被截断的交易指令",
        }
    from core.notifier import notifier
    sent = await notifier.send(f"补发｜{row['title']}", preview, channels=["bark"])
    return {"retried": True, "results": sent, "warning": "补发正文最多保留原审计前500字"}


@router.get("/source-comparison")
def get_source_comparison(codes: str = "000001,600519", lookback_days: int = 14):
    selected = list(dict.fromkeys(code.strip() for code in codes.replace("，", ",").split(",") if code.strip()))
    if not selected or len(selected) > 3 or any(not validate_stock_code(code) for code in selected):
        return {"status": "error", "detail": "codes must contain 1-3 valid stock codes"}
    return compare_history_sources(selected, lookback_days=min(max(lookback_days, 7), 60))
