"""龙虎榜日度记录采集（借鉴 easy-stock 行情总览）。

复用 direct_sources.daily_dragon_tiger（东财数据中心，字段已归一为万元），
把最近几个交易日的全市场龙虎榜落进 lhb_records，供 ReviewCenter 做持仓/
候选股的"近 3 日是否上榜、净买多少"只读参考。不进策略判定。

交易日取 daily_k 最近 DISTINCT 日期（EOD 同步后即真实交易日），daily_k 为空
时回退最近 N 个自然日（非交易日调用返回 0 条，无害）。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text

from core.db import get_db_engine, save_lhb_records
from core.logging_config import logger


def _recent_trade_dates(engine, limit: int) -> List[str]:
    try:
        frame = pd.read_sql(
            text("SELECT DISTINCT date FROM daily_k ORDER BY date DESC LIMIT :limit"),
            engine,
            params={"limit": int(limit)},
        )
        return [str(value)[:10] for value in frame["date"].tolist()]
    except Exception as exc:
        logger.warning(f"lhb trade dates from daily_k unavailable: {exc}")
        return []


def _recent_calendar_dates(limit: int) -> List[str]:
    today = datetime.now().date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(int(limit))]


def collect_lhb_records(engine=None, days: int = 3, fetcher=None) -> Dict[str, int]:
    """拉取并落库最近几个交易日的龙虎榜记录；fetcher 可注入便于测试。"""
    engine = engine or get_db_engine()
    if engine is None:
        return {"saved": 0, "days": 0, "errors": 1}
    fetcher = fetcher or daily_dragon_tiger_fetch
    trade_dates = _recent_trade_dates(engine, days) or _recent_calendar_dates(days)

    total = 0
    errors = 0
    for trade_date in trade_dates:
        try:
            payload = fetcher(trade_date)
        except Exception as exc:
            errors += 1
            logger.warning(f"lhb fetch failed for {trade_date}: {exc}")
            continue
        stocks = (payload or {}).get("stocks") or []
        rows = [
            {
                "event_date": trade_date,
                "code": str(stock.get("code") or "").zfill(6),
                "name": stock.get("name"),
                "reason": stock.get("reason"),
                "net_buy_wan": stock.get("net_buy_wan"),
                "buy_wan": stock.get("buy_wan"),
                "sell_wan": stock.get("sell_wan"),
                "pct_chg": stock.get("change_pct"),
            }
            for stock in stocks
            if str(stock.get("code") or "").strip()
        ]
        total += save_lhb_records(rows, engine=engine)
    return {"saved": total, "days": len(trade_dates), "errors": errors}


def daily_dragon_tiger_fetch(trade_date: str) -> Dict[str, Any]:
    """隔离直接数据源依赖，便于测试注入。"""
    from core.direct_sources import daily_dragon_tiger

    return daily_dragon_tiger(trade_date)
