from collections import Counter
from typing import Any, Dict, List


HOT_SECTOR_PHASES = {"SECTOR_EARLY", "SECTOR_CONFIRM", "SECTOR_CLIMAX"}
EXECUTABLE_BUCKETS = {"TRADE", "EARLY"}
REAR_ROLES = {"FOLLOWER", "LAGGARD"}

REASON_LABELS = {
    "HAS_PUSH": "已有可推候选",
    "NO_SCAN_CANDIDATE": "板块强，但扫描策略没有选出候选",
    "REAR_ROLE": "候选偏后排，暂不追",
    "TOO_EXTENDED": "涨幅偏高，等回踩确认",
    "NO_BUY_POINT": "结构未到买点，等确认价",
    "WEAK_VOLUME": "量能/资金不足，等放量",
    "STRUCTURE_INVALID": "结构失效或风险过高",
    "WEAK_SCORE": "评分不足，继续观察",
}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, tuple):
        return [str(item) for item in value if item]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip(" []'\"") for part in text.replace("；", ",").replace("，", ",").split(",") if part.strip()]


def _is_hot_sector(sector: Dict[str, Any], min_score: float) -> bool:
    return (
        str(sector.get("sector_phase") or "") in HOT_SECTOR_PHASES
        or _as_float(sector.get("sector_momentum_score")) >= min_score
    )


def _is_executable_candidate(stock: Dict[str, Any]) -> bool:
    bucket = str(stock.get("trade_bucket") or "").upper()
    return bool(stock.get("trade_eligible")) or bucket in EXECUTABLE_BUCKETS


def _classify_candidate_gap_reason(stock: Dict[str, Any]) -> str:
    role = str(stock.get("sector_role") or "").upper()
    blockers = _as_list(stock.get("trade_blockers")) + _as_list(stock.get("sop_vetoes")) + _as_list(stock.get("sop_risks"))
    text = " ".join(blockers)

    if stock.get("sector_rear_role_watch") or role in REAR_ROLES or "后排" in text:
        return "REAR_ROLE"
    if any(key in text for key in ("涨停", "近涨停", "涨幅偏高", "5日涨幅", "禁止追涨", "动量加速")):
        return "TOO_EXTENDED"
    if any(key in text for key in ("量能未确认", "换手不足", "主力资金流出", "资金流", "等待放量")):
        return "WEAK_VOLUME"
    if any(key in text for key in ("未站上确认价", "交易计划未确认", "缺少价格行为", "等待突破确认", "仅观察")):
        return "NO_BUY_POINT"
    if any(key in text for key in ("结构失效", "结构风险", "趋势破坏", "风险偏高", "回避", "不进入交易池")):
        return "STRUCTURE_INVALID"
    return "WEAK_SCORE"


def _candidate_digest(stock: Dict[str, Any]) -> Dict[str, Any]:
    blockers = _as_list(stock.get("trade_blockers"))
    return {
        "code": str(stock.get("代码") or stock.get("code") or "").zfill(6),
        "name": stock.get("名称") or stock.get("name") or "",
        "industry": stock.get("行业") or stock.get("industry") or "",
        "price": round(_as_float(stock.get("现价") or stock.get("price") or stock.get("watch_price")), 2),
        "pct": round(_as_float(stock.get("涨幅%") or stock.get("pct")), 2),
        "score": round(_as_float(stock.get("final_trade_score") or stock.get("Score") or stock.get("score")), 2),
        "trade_bucket": stock.get("trade_bucket") or "UNKNOWN",
        "sector_role": stock.get("sector_role") or "UNKNOWN",
        "reason": _classify_candidate_gap_reason(stock),
        "reason_label": REASON_LABELS.get(_classify_candidate_gap_reason(stock), "继续观察"),
        "blockers": blockers[:3],
    }


def build_hot_sector_push_gap_analysis(
    sector_items: List[Dict[str, Any]],
    scan_results: List[Dict[str, Any]],
    *,
    limit: int = 8,
    min_hot_score: float = 58.0,
) -> List[Dict[str, Any]]:
    """Explain why hot sectors did or did not produce executable push candidates."""
    if not sector_items:
        return []

    by_sector: Dict[str, List[Dict[str, Any]]] = {}
    for stock in scan_results or []:
        industry = str(stock.get("行业") or stock.get("industry") or "").strip()
        if industry:
            by_sector.setdefault(industry, []).append(stock)

    hot_sectors = [sector for sector in sector_items if _is_hot_sector(sector, min_hot_score)]
    hot_sectors = sorted(
        hot_sectors,
        key=lambda item: _as_float(item.get("sector_momentum_score")),
        reverse=True,
    )[: max(1, limit)]

    analysis: List[Dict[str, Any]] = []
    for sector in hot_sectors:
        industry = str(sector.get("industry") or "").strip()
        candidates = sorted(
            by_sector.get(industry, []),
            key=lambda stock: _as_float(stock.get("final_trade_score") or stock.get("Score") or stock.get("score")),
            reverse=True,
        )
        executable = [stock for stock in candidates if _is_executable_candidate(stock)]
        if executable:
            primary = "HAS_PUSH"
        elif not candidates:
            primary = "NO_SCAN_CANDIDATE"
        else:
            reasons = Counter(_classify_candidate_gap_reason(stock) for stock in candidates[:10])
            primary = reasons.most_common(1)[0][0] if reasons else "WEAK_SCORE"

        analysis.append({
            "industry": industry,
            "sector_phase": sector.get("sector_phase") or "UNKNOWN",
            "sector_momentum_score": round(_as_float(sector.get("sector_momentum_score")), 1),
            "sector_breadth": round(_as_float(sector.get("sector_breadth")), 1),
            "has_push_candidate": bool(executable),
            "push_candidate_count": len(executable),
            "scan_candidate_count": len(candidates),
            "primary_reason": primary,
            "primary_reason_label": REASON_LABELS.get(primary, "继续观察"),
            "reason_counts": dict(Counter(_classify_candidate_gap_reason(stock) for stock in candidates)),
            "representative_candidates": [_candidate_digest(stock) for stock in (executable or candidates)[:3]],
            "leaders": (sector.get("leaders") or [])[:3],
        })
    return analysis
