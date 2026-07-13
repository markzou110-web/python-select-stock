import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.data import get_cached_data, set_cached_data
from core.direct_sources import (
    block_trade,
    cninfo_announcements,
    dividend_history,
    daily_dragon_tiger,
    dragon_tiger_board,
    eastmoney_concept_blocks,
    eastmoney_fund_flow_minute,
    eastmoney_reports,
    eastmoney_stock_news,
    holder_num_change,
    industry_comparison,
    lockup_expiry,
    margin_trading,
    ths_hot_reason,
)
from core.logging_config import logger
from core.money_flow import get_stock_money_flow


STOCK_RESEARCH_TTL_SECONDS = int(os.getenv("STOCK_RESEARCH_TTL_SECONDS", "21600"))
RISK_KEYWORDS = ("减持", "问询", "处罚", "诉讼", "仲裁", "立案", "退市", "亏损", "解禁", "担保")
OPPORTUNITY_KEYWORDS = ("回购", "增持", "中标", "签订", "订单", "业绩预增", "分红", "股权激励")
POINT_IN_TIME_UNAVAILABLE = {"concepts", "money_flow", "intraday_fund_flow", "industry_comparison"}
SOURCE_NAMES = {
    "concepts": "eastmoney", "money_flow": "eastmoney", "hot_theme": "10jqka",
    "intraday_fund_flow": "eastmoney", "industry_comparison": "eastmoney",
    "daily_dragon_tiger": "eastmoney", "dragon_tiger": "eastmoney", "lockup": "eastmoney",
    "margin": "eastmoney", "block_trade": "eastmoney", "holder_count": "eastmoney",
    "dividends": "eastmoney", "reports": "eastmoney", "news": "eastmoney",
    "announcements": "cninfo",
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).replace(",", "").replace("%", "").strip()
        if text in {"", "-", "--"}:
            return default
        return float(text)
    except Exception:
        return default


def _first(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return rows[0] if rows else {}


def _sum(values: List[Any]) -> float:
    return sum(_num(value) for value in values)


def _keyword_hits(rows: List[Dict[str, Any]], key: str, keywords: tuple[str, ...]) -> List[Dict[str, str]]:
    hits = []
    for row in rows:
        text = str(row.get(key) or "")
        matched = [word for word in keywords if word in text]
        if matched:
            hits.append({
                "date": str(row.get("date") or row.get("time") or "")[:10],
                "title": text,
                "keywords": ",".join(matched),
            })
    return hits


def _row_date(row: Dict[str, Any]) -> Optional[datetime]:
    for key in ("published_at", "publish_date", "notice_date", "report_date", "trade_date", "date", "time"):
        value = str(row.get(key) or "").strip()
        if not value:
            continue
        try:
            return datetime.fromisoformat(value[:19].replace("/", "-"))
        except ValueError:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d")
            except ValueError:
                continue
    return None


def _filter_rows_as_of(rows: Any, trade_date: str) -> Any:
    """Exclude future/undated rows from historical research snapshots."""
    if not isinstance(rows, list):
        return rows
    cutoff = datetime.strptime(trade_date[:10], "%Y-%m-%d").date()
    filtered = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        observed = _row_date(row)
        if observed is not None and observed.date() <= cutoff:
            filtered.append(row)
    return filtered


def _summarise_research_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    money_flow = payload.get("money_flow") or {}
    dragon = payload.get("dragon_tiger") or {}
    lockup = payload.get("lockup") or {}
    margin_rows = payload.get("margin") or []
    block_rows = payload.get("block_trade") or []
    holder_rows = payload.get("holder_count") or []
    reports = payload.get("reports") or []
    news = payload.get("news") or []
    announcements = payload.get("announcements") or []
    dividends = payload.get("dividends") or []
    hot_theme = payload.get("hot_theme") or {}
    intraday_flow = payload.get("intraday_fund_flow") or []
    industry_rank = payload.get("industry_comparison") or {}
    daily_lhb = payload.get("daily_dragon_tiger") or {}

    risk_flags: List[str] = []
    opportunity_flags: List[str] = []
    score_delta = 0.0
    components: Dict[str, float] = {}

    summary = money_flow.get("summary") or {}
    main_5d = _num(summary.get("main_net_5d_yi"))
    consecutive = str(summary.get("consecutive_direction") or "")
    consecutive_days = int(_num(summary.get("consecutive_days")))
    if main_5d > 0:
        opportunity_flags.append(f"近5日主力净流入 {main_5d:.2f} 亿")
        components["money_flow"] = min(4, 1.5 + max(0, main_5d) * 0.4 + min(2, consecutive_days * 0.5 if consecutive == "inflow" else 0))
    elif main_5d < 0:
        risk_flags.append(f"近5日主力净流出 {abs(main_5d):.2f} 亿")
        components["money_flow"] = max(-4, main_5d * 0.5)

    records = dragon.get("records") or []
    institution = dragon.get("institution") or {}
    if records:
        opportunity_flags.append(f"近30日龙虎榜上榜 {len(records)} 次")
        components["dragon_tiger"] = 1.5
        inst_net = _num(institution.get("net_amt_wan"))
        if inst_net > 0:
            opportunity_flags.append(f"机构席位净买入 {inst_net:.0f} 万")
            components["dragon_tiger"] += 2
        elif inst_net < 0:
            risk_flags.append(f"机构席位净卖出 {abs(inst_net):.0f} 万")
            components["dragon_tiger"] -= 2

    if daily_lhb.get("matched"):
        stock = daily_lhb.get("stock") or {}
        opportunity_flags.append(f"当日全市场龙虎榜：净买入 {_num(stock.get('net_buy_wan')):.0f} 万")
        components["daily_dragon_tiger"] = 1.5 if _num(stock.get("net_buy_wan")) > 0 else -1

    if hot_theme.get("matched"):
        reason = str(hot_theme.get("reason") or "").strip()
        opportunity_flags.append(f"同花顺热点题材：{reason[:36] or '强势股'}")
        components["hot_theme"] = 2.5

    latest_intraday = _first(intraday_flow[::-1])
    if latest_intraday:
        intraday_main_yi = _num(latest_intraday.get("main_net")) / 100000000
        if intraday_main_yi > 0.2:
            opportunity_flags.append(f"盘中主力净流入 {intraday_main_yi:.2f} 亿")
            components["intraday_fund_flow"] = min(2.5, 1 + intraday_main_yi)
        elif intraday_main_yi < -0.2:
            risk_flags.append(f"盘中主力净流出 {abs(intraday_main_yi):.2f} 亿")
            components["intraday_fund_flow"] = max(-2.5, intraday_main_yi)

    matched_industries = industry_rank.get("matched") or []
    if not matched_industries:
        concept_names = set((payload.get("concepts") or {}).get("concept_tags") or [])
        matched_industries = [
            row for row in (industry_rank.get("top") or [])
            if row.get("name") in concept_names
        ]
        matched_industries.sort(key=lambda item: int(_num(item.get("rank"), 9999)))
    if matched_industries:
        best = matched_industries[0]
        rank = int(_num(best.get("rank")))
        total = int(_num(industry_rank.get("total")))
        if rank and total and rank <= max(3, total * 0.15):
            opportunity_flags.append(f"所属行业强势：{best.get('name')} 排名 {rank}/{total}")
            components["industry_rank"] = 1.5
        elif rank and total and rank >= max(1, total - max(3, total * 0.15)):
            risk_flags.append(f"所属行业弱势：{best.get('name')} 排名 {rank}/{total}")
            components["industry_rank"] = -1.5

    upcoming = lockup.get("upcoming") or []
    if upcoming:
        max_ratio = max(_num(row.get("ratio")) for row in upcoming)
        risk_flags.append(f"未来90天存在限售解禁，最高解禁比例 {max_ratio:.2f}%")
        components["lockup"] = -6 if max_ratio >= 5 else -3

    latest_margin = _first(margin_rows)
    previous_margin = margin_rows[4] if len(margin_rows) >= 5 else {}
    if latest_margin and previous_margin:
        rzye_delta = (_num(latest_margin.get("rzye")) - _num(previous_margin.get("rzye"))) / 100000000
        if rzye_delta > 0.5:
            opportunity_flags.append(f"融资余额短期增加 {rzye_delta:.2f} 亿")
            components["margin"] = 1.5
        elif rzye_delta < -0.5:
            risk_flags.append(f"融资余额短期下降 {abs(rzye_delta):.2f} 亿")
            components["margin"] = -1.5

    recent_blocks = block_rows[:5]
    if recent_blocks:
        avg_premium = _sum([row.get("premium_pct") for row in recent_blocks]) / len(recent_blocks)
        if avg_premium < -3:
            risk_flags.append(f"近期大宗交易平均折价 {abs(avg_premium):.2f}%")
            components["block_trade"] = -2
        elif avg_premium > 0:
            opportunity_flags.append(f"近期大宗交易平均溢价 {avg_premium:.2f}%")
            components["block_trade"] = 1

    latest_holder = _first(holder_rows)
    if latest_holder:
        change_ratio = _num(latest_holder.get("change_ratio"))
        if change_ratio < -3:
            opportunity_flags.append(f"股东户数下降 {abs(change_ratio):.2f}%，筹码趋于集中")
            components["holder_count"] = 2
        elif change_ratio > 5:
            risk_flags.append(f"股东户数增加 {change_ratio:.2f}%，筹码分散")
            components["holder_count"] = -1.5

    risk_hits = _keyword_hits(news, "title", RISK_KEYWORDS) + _keyword_hits(announcements, "title", RISK_KEYWORDS)
    opportunity_hits = _keyword_hits(news, "title", OPPORTUNITY_KEYWORDS) + _keyword_hits(announcements, "title", OPPORTUNITY_KEYWORDS)
    if risk_hits:
        risk_flags.append(f"新闻/公告命中风险关键词：{risk_hits[0]['keywords']}")
        components["news_announcement"] = -2
    elif opportunity_hits:
        opportunity_flags.append(f"新闻/公告命中催化关键词：{opportunity_hits[0]['keywords']}")
        components["news_announcement"] = 1.5

    if reports:
        latest_rating = str(reports[0].get("rating") or "")
        if latest_rating in {"买入", "增持", "强烈推荐"}:
            opportunity_flags.append(f"最新研报评级：{latest_rating}")
            components["reports"] = 1

    if dividends:
        latest_dividend = _first(dividends)
        if _num(latest_dividend.get("bonus_rmb")) > 0:
            opportunity_flags.append("具备分红记录")
            components["dividends"] = 0.5

    score_delta = round(max(-10, min(10, sum(components.values()))), 2)
    score = round(max(0, min(100, 50 + score_delta * 5)), 1)
    if score_delta >= 4:
        label = "研究增强"
    elif score_delta <= -4:
        label = "事件风险"
    elif score_delta > 0:
        label = "轻微加分"
    elif score_delta < 0:
        label = "轻微减分"
    else:
        label = "中性"

    return {
        "score": score,
        "score_delta": score_delta,
        "label": label,
        "components": {key: round(value, 2) for key, value in components.items()},
        "risk_flags": list(dict.fromkeys(risk_flags))[:8],
        "opportunity_flags": list(dict.fromkeys(opportunity_flags))[:8],
        "risk_keyword_hits": risk_hits[:5],
        "opportunity_keyword_hits": opportunity_hits[:5],
    }


def build_stock_research_signals(
    code: str,
    trade_date: Optional[str] = None,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    cache_key = f"stock_research_signals_{code}_{trade_date or 'latest'}"
    if not force_refresh:
        cached = get_cached_data(cache_key, STOCK_RESEARCH_TTL_SECONDS)
        if cached is not None:
            return {**cached, "cache_hit": True}

    trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
    try:
        historical = datetime.strptime(trade_date[:10], "%Y-%m-%d").date() < datetime.now().date()
    except ValueError:
        historical = False
    payload: Dict[str, Any] = {
        "status": "ok",
        "code": code,
        "trade_date": trade_date,
        "cache_hit": False,
        "updated_at": datetime.now().isoformat(),
        "errors": {},
        "source_status": {},
        "point_in_time": historical,
        "as_of": trade_date,
    }

    fetchers = {
        "concepts": lambda: eastmoney_concept_blocks(code),
        "money_flow": lambda: get_stock_money_flow(code),
        "hot_theme": lambda: _match_hot_theme(code, trade_date),
        "intraday_fund_flow": lambda: eastmoney_fund_flow_minute(code),
        "industry_comparison": lambda: industry_comparison(top_n=100),
        "daily_dragon_tiger": lambda: _match_daily_dragon_tiger(code, trade_date),
        "dragon_tiger": lambda: dragon_tiger_board(code, trade_date=trade_date, look_back=30),
        "lockup": lambda: lockup_expiry(code, trade_date=trade_date, forward_days=90),
        "margin": lambda: margin_trading(code, page_size=30),
        "block_trade": lambda: block_trade(code, page_size=20),
        "holder_count": lambda: holder_num_change(code, page_size=10),
        "dividends": lambda: dividend_history(code, page_size=20),
        "reports": lambda: eastmoney_reports(code, max_pages=1),
        "news": lambda: eastmoney_stock_news(code, page_size=20),
        "announcements": lambda: cninfo_announcements(code, page_size=20),
    }
    for name, fetcher in fetchers.items():
        if historical and name in POINT_IN_TIME_UNAVAILABLE:
            payload[name] = {} if name in {"concepts", "money_flow", "industry_comparison"} else []
            payload["errors"][name] = "POINT_IN_TIME_UNAVAILABLE"
            payload["source_status"][name] = {
                "status": "UNAVAILABLE", "source": SOURCE_NAMES.get(name, name),
                "reason": "POINT_IN_TIME_UNAVAILABLE",
            }
            continue
        try:
            value = fetcher()
            payload[name] = _filter_rows_as_of(value, trade_date) if historical else value
            payload["source_status"][name] = {
                "status": "AVAILABLE", "source": SOURCE_NAMES.get(name, name),
                "observed_at": trade_date,
            }
        except Exception as exc:
            logger.warning(f"Research fetcher {name} failed for {code}: {exc}")
            payload[name] = {} if name in {"concepts", "money_flow", "dragon_tiger", "lockup", "hot_theme", "industry_comparison", "daily_dragon_tiger"} else []
            payload["errors"][name] = str(exc)[:120]
            payload["source_status"][name] = {
                "status": "UNAVAILABLE", "source": SOURCE_NAMES.get(name, name),
                "reason": type(exc).__name__,
            }

    payload["summary"] = _summarise_research_payload(payload)
    if payload["errors"]:
        payload["status"] = "partial"
    set_cached_data(cache_key, payload)
    return payload


def get_cached_stock_research_signals(code: str, trade_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    cache_key = f"stock_research_signals_{code}_{trade_date or 'latest'}"
    return get_cached_data(cache_key, STOCK_RESEARCH_TTL_SECONDS)


def _match_hot_theme(code: str, trade_date: str) -> Dict[str, Any]:
    rows = ths_hot_reason(trade_date)
    for row in rows:
        if str(row.get("code")).zfill(6) == code:
            return {**row, "matched": True, "total": len(rows)}
    return {"matched": False, "total": len(rows)}


def _match_daily_dragon_tiger(code: str, trade_date: str) -> Dict[str, Any]:
    board = daily_dragon_tiger(trade_date)
    for row in board.get("stocks") or []:
        if str(row.get("code")).zfill(6) == code:
            return {"matched": True, "date": board.get("date"), "stock": row, "total_records": board.get("total_records", 0)}
    return {"matched": False, "date": board.get("date"), "total_records": board.get("total_records", 0)}


def apply_research_adjustment(stock: Dict[str, Any], research: Dict[str, Any]) -> None:
    summary = research.get("summary") or {}
    delta = _num(summary.get("score_delta"))
    stock["research_signal_score"] = summary.get("score")
    stock["research_score_delta"] = delta
    stock["research_label"] = summary.get("label")
    stock["research_risk_flags"] = summary.get("risk_flags") or []
    stock["research_opportunity_flags"] = summary.get("opportunity_flags") or []
    stock["research_components"] = summary.get("components") or {}
    if delta:
        stock["final_trade_score"] = round(_num(stock.get("final_trade_score") or stock.get("Score")) + delta, 2)
        stock["final_rank_score"] = round(_num(stock.get("final_rank_score") or stock.get("Score")) + delta, 2)
    if summary.get("risk_flags"):
        blockers = list(stock.get("trade_blockers") or [])
        if delta <= -4 and "研究事件风险，降级观察" not in blockers:
            blockers.append("研究事件风险，降级观察")
        stock["trade_blockers"] = blockers
