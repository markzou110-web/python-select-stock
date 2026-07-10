from collections import Counter
from typing import Any, Dict, List, Optional


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _bucket(stock: Dict[str, Any]) -> str:
    return str(stock.get("trade_bucket") or "UNKNOWN").upper()


def _rank_score(stock: Dict[str, Any]) -> float:
    return _as_float(stock.get("final_trade_score") or stock.get("calibrated_score") or stock.get("Score") or stock.get("score"))


def _stock_digest(stock: Dict[str, Any]) -> Dict[str, Any]:
    blockers = stock.get("trade_blockers") or []
    if isinstance(blockers, str):
        blockers = [part.strip(" []'\"") for part in blockers.split(",") if part.strip(" []'\"")]
    return {
        "code": str(stock.get("代码") or stock.get("code") or "").zfill(6),
        "name": stock.get("名称") or stock.get("name") or "",
        "industry": stock.get("行业") or stock.get("industry") or "",
        "grade": stock.get("early_trade_grade") or stock.get("sop_grade") or "?",
        "bucket": _bucket(stock),
        "score": round(_rank_score(stock), 2),
        "action": stock.get("trade_opportunity_label") or stock.get("execution_instruction") or "",
        "blockers": blockers[:2],
    }


def build_daily_strategy_report(
    scan_results: List[Dict[str, Any]],
    *,
    scan_date: str = "",
    sector_gap_analysis: Optional[List[Dict[str, Any]]] = None,
    bark_push_count: int = 0,
    watchlist_count: int = 0,
    real_position_count: int = 0,
) -> Dict[str, Any]:
    """Build a compact after-close strategy report from one day's scan snapshot."""
    results = scan_results or []
    bucket_counts = Counter(_bucket(stock) for stock in results)
    grade_counts = Counter(str(stock.get("sop_grade") or "?") for stock in results)
    strategy_counts = Counter(str(stock.get("strategy_type") or "UNKNOWN") for stock in results)
    industry_counts = Counter(str(stock.get("行业") or stock.get("industry") or "UNKNOWN") for stock in results)

    trade = [stock for stock in results if _bucket(stock) == "TRADE" or stock.get("trade_eligible") is True]
    early = [stock for stock in results if _bucket(stock) == "EARLY" or stock.get("early_trade_candidate")]
    observe = [stock for stock in results if _bucket(stock) == "OBSERVE"]
    block = [stock for stock in results if _bucket(stock) == "BLOCK"]
    sector_gaps = [item for item in (sector_gap_analysis or []) if not item.get("has_push_candidate")]

    if trade:
        stance = "有可交易候选，仍需按确认价/量能小仓复核"
    elif early:
        stance = "无正式买点，有A-提前复核候选，严禁追高"
    elif observe:
        stance = "以观察为主，等待确认价和量能"
    else:
        stance = "无有效候选，保持空仓/轻仓观察"

    next_actions = []
    if trade:
        next_actions.append("TRADE候选只在站稳确认价且量能确认时小仓复核")
    if early:
        next_actions.append("A-候选次日高开不追，优先等回踩/尾盘确认")
    if sector_gaps:
        next_actions.append("热门板块先看未推原因，后排/高位票不追")
    if block:
        next_actions.append("BLOCK候选仅复盘，不作为买入清单")

    top_candidates = sorted(trade + early + observe, key=_rank_score, reverse=True)[:8]
    return {
        "scan_date": scan_date,
        "summary": {
            "scan_count": len(results),
            "trade_count": len(trade),
            "early_count": len(early),
            "observe_count": len(observe),
            "block_count": len(block),
            "bark_push_count": int(bark_push_count or 0),
            "watchlist_count": int(watchlist_count or 0),
            "real_position_count": int(real_position_count or 0),
            "stance": stance,
        },
        "bucket_counts": dict(bucket_counts),
        "grade_counts": dict(grade_counts),
        "strategy_counts": dict(strategy_counts.most_common(8)),
        "top_industries": [{"industry": k, "count": v} for k, v in industry_counts.most_common(8)],
        "top_candidates": [_stock_digest(stock) for stock in top_candidates],
        "sector_push_gaps": sector_gaps[:5],
        "next_actions": next_actions,
    }


def build_daily_strategy_report_body(report: Dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        f"数据日期：{report.get('scan_date') or '--'}",
        (
            f"扫描{summary.get('scan_count', 0)}只 | "
            f"TRADE {summary.get('trade_count', 0)} | "
            f"EARLY {summary.get('early_count', 0)} | "
            f"OBSERVE {summary.get('observe_count', 0)} | "
            f"BLOCK {summary.get('block_count', 0)}"
        ),
        f"Bark事件 {summary.get('bark_push_count', 0)} | 观察池 {summary.get('watchlist_count', 0)} | 持仓 {summary.get('real_position_count', 0)}",
        f"结论：{summary.get('stance', '--')}",
    ]
    gaps = report.get("sector_push_gaps") or []
    if gaps:
        lines.append("")
        lines.append("热门板块未推：")
        for item in gaps[:3]:
            lines.append(f"{item.get('industry', '--')}：{item.get('primary_reason_label', '继续观察')}")
    actions = report.get("next_actions") or []
    if actions:
        lines.append("")
        lines.append("明日动作：")
        lines.extend(f"- {action}" for action in actions[:4])
    return "\n".join(lines)
