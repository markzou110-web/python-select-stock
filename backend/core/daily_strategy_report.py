from collections import Counter
from typing import Any, Dict, List, Optional

from core.industry_prosperity import build_industry_prosperity, prosperity_text
from core.logic_chain import build_capital_evidence_line, build_logic_chain_line


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _bucket(stock: Dict[str, Any]) -> str:
    return str(stock.get("trade_bucket") or "UNKNOWN").upper()


def _rank_score(stock: Dict[str, Any]) -> float:
    return _as_float(stock.get("final_trade_score") or stock.get("calibrated_score") or stock.get("Score") or stock.get("score"))


def _display_score(stock: Dict[str, Any]) -> float:
    value = None
    for key in ("display_opportunity_score", "trade_opportunity_score"):
        if stock.get(key) is not None:
            value = stock.get(key)
            break
    value = _as_float(_rank_score(stock) if value is None else value)
    return max(0.0, min(100.0, value))


def _action_label(stock: Dict[str, Any]) -> str:
    bucket = _bucket(stock)
    if bool(stock.get("trade_eligible")) and bucket == "TRADE":
        return stock.get("execution_instruction") or stock.get("trade_opportunity_label") or "小仓复核"
    if bucket == "EARLY" or stock.get("early_trade_candidate"):
        return "提前复核（非正式买点）"
    if bucket == "BLOCK":
        return "禁止新增仓位"
    return "仅观察"


def _stock_digest(stock: Dict[str, Any]) -> Dict[str, Any]:
    blockers = stock.get("trade_blockers") or []
    if isinstance(blockers, str):
        blockers = [part.strip(" []'\"") for part in blockers.split(",") if part.strip(" []'\"")]
    return {
        "code": str(stock.get("代码") or stock.get("code") or "").zfill(6),
        "name": stock.get("名称") or stock.get("name") or "",
        "industry": stock.get("行业") or stock.get("industry") or "",
        "bucket": _bucket(stock),
        "score": round(_display_score(stock), 2),
        "action": _action_label(stock),
        "blockers": blockers[:2],
        "logic": build_logic_chain_line(stock),
        "capital": build_capital_evidence_line(stock),
    }


def _ai_review_section(ai_review: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not ai_review or not ai_review.get("analyses"):
        return {"status": "none"}
    analyses = ai_review["analyses"]
    counts = Counter(str(item.get("action") or "WAIT") for item in analyses)
    buy_analyses = [item for item in analyses if str(item.get("action") or "").upper() == "BUY"]
    return {
        "status": "available",
        "model": ai_review.get("model"),
        "source": ai_review.get("source"),
        "market_summary": ai_review.get("market_summary"),
        "counts": dict(counts),
        "top_analyses": [
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "action": item.get("action"),
                "confidence": item.get("confidence"),
                "summary": item.get("summary"),
                "strategy_rank": item.get("strategy_rank"),
                "ai_rank": item.get("ai_rank"),
                "positive_factors": (
                    list(item.get("positive_factors") or [])[:3]
                    if isinstance(item.get("positive_factors"), list)
                    else []
                ),
            }
            for item in buy_analyses[:3]
        ],
    }


def build_daily_strategy_report(
    scan_results: List[Dict[str, Any]],
    *,
    scan_date: str = "",
    sector_gap_analysis: Optional[List[Dict[str, Any]]] = None,
    bark_push_count: int = 0,
    watchlist_count: int = 0,
    real_position_count: int = 0,
    ai_review: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a compact after-close strategy report from one day's scan snapshot."""
    results = scan_results or []
    bucket_counts = Counter(_bucket(stock) for stock in results)
    strategy_counts = Counter(str(stock.get("strategy_type") or "UNKNOWN") for stock in results)
    industry_counts = Counter(str(stock.get("行业") or stock.get("industry") or "UNKNOWN") for stock in results)

    trade = [
        stock for stock in results
        if _bucket(stock) == "TRADE" and stock.get("trade_eligible") is True
    ]
    early = [stock for stock in results if _bucket(stock) == "EARLY" or stock.get("early_trade_candidate")]
    observe = [stock for stock in results if _bucket(stock) == "OBSERVE"]
    block = [stock for stock in results if _bucket(stock) == "BLOCK"]
    sector_gaps = [item for item in (sector_gap_analysis or []) if not item.get("has_push_candidate")]

    if trade:
        stance = "有可交易候选，仍需按确认价/量能小仓复核"
    elif early:
        stance = "无正式买点，有提前复核候选，严禁追高"
    elif observe:
        stance = "以观察为主，等待确认价和量能"
    else:
        stance = "无有效候选，保持空仓/轻仓观察"

    next_actions = []
    if trade:
        next_actions.append("TRADE候选只在站稳确认价且量能确认时小仓复核")
    if early:
        next_actions.append("提前复核候选次日高开不追，优先等回踩/尾盘确认")
    if sector_gaps:
        next_actions.append("热门板块先看未推原因，后排/高位票不追")
    if block:
        next_actions.append("BLOCK候选仅复盘，不作为买入清单")

    top_candidates = sorted(trade + early + observe, key=_rank_score, reverse=True)[:8]
    prosperity_map = build_industry_prosperity(results)
    top_prosperity = sorted(
        prosperity_map.items(), key=lambda item: item[1].get("prosperity_score", 0), reverse=True
    )[:5]
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
        "strategy_counts": dict(strategy_counts.most_common(8)),
        "top_industries": [{"industry": k, "count": v} for k, v in industry_counts.most_common(8)],
        "industry_prosperity": [
            {"industry": industry, **data} for industry, data in top_prosperity
        ],
        "top_candidates": [_stock_digest(stock) for stock in top_candidates],
        "sector_push_gaps": sector_gaps[:5],
        "next_actions": next_actions,
        "ai_review": _ai_review_section(ai_review),
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
    prosperity = report.get("industry_prosperity") or []
    if prosperity:
        lines.append("")
        lines.append("行业景气（候选池基本面聚合）：")
        for item in prosperity[:3]:
            lines.append(f"{item.get('industry', '--')}｜{prosperity_text(item)}")
    actions = report.get("next_actions") or []
    if actions:
        lines.append("")
        lines.append("明日动作：")
        lines.extend(f"- {action}" for action in actions[:4])
    ai = report.get("ai_review") or {}
    if ai.get("status") == "available":
        lines.append("")
        counts = ai.get("counts") or {}
        lines.append(
            f"AI复核（{ai.get('model') or '--'}）BUY {counts.get('BUY', 0)} / "
            f"WAIT {counts.get('WAIT', 0)} / AVOID {counts.get('AVOID', 0)}"
        )
        if ai.get("market_summary"):
            lines.append(str(ai["market_summary"])[:160])
        for item in (ai.get("top_analyses") or [])[:3]:
            strategy_rank = item.get("strategy_rank") or "--"
            ai_rank = item.get("ai_rank") or "--"
            lines.append(
                f"BUY {item.get('name') or item.get('code')}({item.get('code') or '--'})"
                f"｜策略#{strategy_rank} → AI#{ai_rank}｜{item.get('confidence') or 0}分"
            )
            factors = [str(value) for value in (item.get("positive_factors") or [])[:3] if value]
            lines.append(f"依据：{'；'.join(factors) if factors else item.get('summary') or '--'}")
        wait_count = counts.get("WAIT", 0)
        avoid_count = counts.get("AVOID", 0)
        if wait_count or avoid_count:
            lines.append(
                f"WAIT {wait_count}只、AVOID {avoid_count}只仅留档观察，不作为本次推送标的"
            )
    return "\n".join(lines)
