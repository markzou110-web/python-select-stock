"""AI review endpoints for deterministic strategy candidates."""

import re
from datetime import date, datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from core.ai_review_performance import build_ai_review_performance
from core.ai_stock_analysis import analyze_single_stock, analyze_strategy_candidates
from core.config import config
from core.db import (
    get_db_engine,
    get_latest_ai_candidate_reviews,
    get_scan_dates,
    save_ai_candidate_reviews,
)
from core.logging_config import logger
from core.stock_research import build_stock_research_signals
from routers.stock import _fetch_financials, _load_scan_candidate


router = APIRouter(prefix="/api/ai", tags=["ai-analysis"])

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _review_date(candidates: list) -> str:
    for item in candidates:
        raw = str((item or {}).get("data_date") or "")[:10]
        if _DATE_PATTERN.fullmatch(raw):
            return raw
    return datetime.now().strftime("%Y-%m-%d")


@router.get("/status")
def get_ai_analysis_status() -> Dict[str, Any]:
    return config.get_ai_safe_status()


@router.post("/analyze-candidates")
def analyze_candidates(data: Dict[str, Any]) -> Dict[str, Any]:
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        raise HTTPException(status_code=400, detail="candidates必须是数组")
    try:
        result = analyze_strategy_candidates(candidates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("status") == "success" and result.get("analyses"):
        try:
            save_ai_candidate_reviews(
                result["analyses"],
                review_date=_review_date(candidates),
                model=result.get("model") or "",
                market_summary=result.get("market_summary") or "",
                source="manual",
            )
        except Exception as exc:
            logger.warning(f"Manual AI review persistence skipped: {exc}")
    return result


@router.post("/analyze-stock")
def analyze_stock(data: Dict[str, Any]) -> Dict[str, Any]:
    """Deep AI review for one selected stock, anchored to its latest scan snapshot."""
    code = str(data.get("code") or "").strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="code必须是6位股票代码")
    date_raw = str(data.get("date") or "")[:10]
    signal_date = date_raw if _DATE_PATTERN.fullmatch(date_raw) else None
    force_refresh = data.get("force_refresh") is True

    candidate = _load_scan_candidate(code, date.fromisoformat(signal_date) if signal_date else None)
    if candidate is None:
        raise HTTPException(status_code=404, detail="未找到该股票的扫描快照，AI研判基于策略候选，请先扫描或从扫描结果中选择")
    if not config.is_ai_analysis_configured():
        return analyze_single_stock(candidate)

    # Keep the strategy snapshot authoritative, but fill fields that older scan
    # snapshots did not persist from the latest verified local fundamentals.
    try:
        financials = _fetch_financials(code)
        for source_key, candidate_key in (
            ("roe", "ROE"),
            ("net_profit_yoy", "净利YOY"),
            ("mkt_cap_yi", "mkt_cap_yi"),
        ):
            if candidate.get(candidate_key) is None and financials.get(source_key) is not None:
                candidate[candidate_key] = financials[source_key]
    except Exception as exc:
        logger.warning(f"Single-stock fundamental enrichment failed for {code}: {exc}")

    try:
        # A manual single-stock review answers the current decision question. The
        # strategy fields remain anchored to signal_date, while research uses the
        # TTL-managed latest snapshot so post-signal announcements are not hidden.
        research = build_stock_research_signals(
            code, trade_date=None, force_refresh=force_refresh,
        )
    except Exception as exc:
        logger.warning(f"Single-stock research fetch failed for {code}: {exc}")
        research = None
    if isinstance(research, dict):
        flow = research.get("money_flow")
        if isinstance(flow, dict):
            latest = flow.get("latest")
            if isinstance(latest, dict) and latest:
                candidate["money_flow"] = {
                    "main_net_inflow_yi": latest.get("main_net_inflow_yi"),
                    "main_net_ratio": latest.get("main_net_ratio"),
                    "source": flow.get("source"),
                }
                candidate["money_flow_status"] = str(flow.get("status") or "ok")
    try:
        return analyze_single_stock(candidate, research_snapshot=research)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reviews")
def get_ai_reviews(review_date: Optional[str] = None) -> Dict[str, Any]:
    if review_date and not _DATE_PATTERN.fullmatch(review_date[:10]):
        raise HTTPException(status_code=400, detail="date必须是YYYY-MM-DD")
    date_str = review_date or (get_scan_dates()[:1] or [datetime.now().strftime("%Y-%m-%d")])[0]
    review = get_latest_ai_candidate_reviews(date_str) if date_str else None
    if not review:
        return {"status": "none", "review_date": date_str, "analyses": []}
    return {"status": "available", **review}


@router.get("/performance")
def get_ai_performance(days: int = 180) -> Dict[str, Any]:
    """Compare AI actions against the same strategy-reviewed candidate universe."""
    return build_ai_review_performance(get_db_engine(), days=max(1, min(days, 3650)))
