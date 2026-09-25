from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import text

from core.data import get_cached_data, set_cached_data
from core.direct_sources import cninfo_announcements, eastmoney_stock_news
from core.logging_config import logger
from core.stock_research import OPPORTUNITY_KEYWORDS, RISK_KEYWORDS


RADAR_CACHE_KEY = "candidate_research_radar"
RADAR_TTL_SECONDS = 3600


def _load_research_targets(engine, limit: int) -> List[Dict[str, Any]]:
    if engine is None:
        return []
    rows: List[Dict[str, Any]] = []
    queries = [
        ("POSITION", """
            SELECT code, name, NULL AS industry
            FROM paper_trading WHERE status = 'OPEN'
            ORDER BY entry_date DESC LIMIT :limit
        """),
        ("WATCHLIST", """
            SELECT code, name, industry
            FROM watchlist WHERE status = 'WATCHING'
            ORDER BY created_at DESC LIMIT :limit
        """),
        ("SCAN", """
            SELECT code, name, industry
            FROM scan_history
            WHERE COALESCE(data_date, date) = (
                SELECT MAX(COALESCE(data_date, date)) FROM scan_history
            )
            ORDER BY COALESCE(sop_quality_score, score, 0) DESC
            LIMIT :limit
        """),
    ]
    with engine.connect() as conn:
        for scope, query in queries:
            try:
                result = conn.execute(text(query), {"limit": limit}).mappings().all()
                rows.extend({**dict(row), "scope": scope} for row in result)
            except Exception as exc:
                logger.debug(f"Research radar target source {scope} unavailable: {exc}")

    targets: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("code") or "").zfill(6)
        if not code or code == "000000":
            continue
        if code not in targets:
            targets[code] = {
                "code": code,
                "name": row.get("name") or code,
                "industry": row.get("industry") or "未知",
                "scopes": [],
            }
        if row.get("scope") not in targets[code]["scopes"]:
            targets[code]["scopes"].append(row.get("scope"))
        if targets[code]["industry"] == "未知" and row.get("industry"):
            targets[code]["industry"] = row["industry"]
    return list(targets.values())[:limit]


def _evidence_kind(title: str) -> str:
    if any(keyword in title for keyword in RISK_KEYWORDS):
        return "RISK"
    if any(keyword in title for keyword in OPPORTUNITY_KEYWORDS):
        return "CATALYST"
    return "INFO"


def _fetch_target_evidence(target: Dict[str, Any]) -> Dict[str, Any]:
    code = target["code"]
    errors = []
    try:
        news = eastmoney_stock_news(code, page_size=6)
    except Exception as exc:
        news = []
        errors.append(f"news: {str(exc)[:80]}")
    try:
        announcements = cninfo_announcements(code, page_size=6)
    except Exception as exc:
        announcements = []
        errors.append(f"announcements: {str(exc)[:80]}")

    evidence = []
    for evidence_type, rows in (("NEWS", news), ("ANNOUNCEMENT", announcements)):
        for row in rows:
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            evidence.append({
                "type": evidence_type,
                "kind": _evidence_kind(title),
                "title": title,
                "date": str(row.get("date") or row.get("time") or "")[:19],
                "source": row.get("source") or row.get("type") or ("巨潮资讯" if evidence_type == "ANNOUNCEMENT" else "东方财富"),
                "url": row.get("url") or "",
            })
    deduped = []
    seen = set()
    for item in evidence:
        key = item["title"].replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    deduped.sort(key=lambda item: item.get("date") or "", reverse=True)
    return {**target, "evidence": deduped[:8], "errors": errors}


def build_candidate_research_radar(engine, limit: int = 10, force_refresh: bool = False) -> Dict[str, Any]:
    limit = max(1, min(int(limit), 20))
    cache_key = f"{RADAR_CACHE_KEY}:{limit}"
    if not force_refresh:
        cached = get_cached_data(cache_key, RADAR_TTL_SECONDS)
        if cached is not None:
            return {**cached, "cache_hit": True}

    targets = _load_research_targets(engine, limit)
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(targets)))) as executor:
        items = list(executor.map(_fetch_target_evidence, targets)) if targets else []
    all_evidence = [evidence for item in items for evidence in item["evidence"]]
    payload = {
        "status": "ok",
        "updated_at": datetime.now().isoformat(),
        "cache_hit": False,
        "ttl_seconds": RADAR_TTL_SECONDS,
        "summary": {
            "targets": len(items),
            "evidence": len(all_evidence),
            "risk": sum(1 for item in all_evidence if item["kind"] == "RISK"),
            "catalyst": sum(1 for item in all_evidence if item["kind"] == "CATALYST"),
            "partial_targets": sum(1 for item in items if item["errors"]),
        },
        "items": items,
        "policy": "研究证据只展示，不修改策略分、交易资格或执行门禁",
    }
    set_cached_data(cache_key, payload)
    return payload
