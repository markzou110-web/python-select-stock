from datetime import datetime
from typing import Any, Dict

import akshare as ak
import pandas as pd
import requests

from core.data import get_cached_data, get_stale_cache, set_cached_data
from core.logging_config import logger


GLOBAL_MARKET_CACHE_KEY = "global_market_research_context"
GLOBAL_MARKET_TTL_SECONDS = 600
GLOBAL_INDEX_NAMES = {
    "道琼斯": ("道琼斯",),
    "标普500": ("标普500", "标普 500"),
    "纳斯达克": ("纳斯达克",),
    "恒生指数": ("恒生指数",),
    "恒生科技": ("恒生科技",),
}
TENCENT_GLOBAL_SYMBOLS = {
    "道琼斯": "usDJI",
    "标普500": "usINX",
    "纳斯达克": "usIXIC",
    "恒生指数": "hkHSI",
    "恒生科技": "hkHSTECH",
}


def get_global_market_context(force_refresh: bool = False) -> Dict[str, Any]:
    if not force_refresh:
        cached = get_cached_data(GLOBAL_MARKET_CACHE_KEY, GLOBAL_MARKET_TTL_SECONDS)
        if cached is not None:
            return {**cached, "cache_hit": True}
    try:
        frame = ak.index_global_spot_em()
        if frame is None or frame.empty:
            raise ValueError("empty global index response")
        items = []
        for label, aliases in GLOBAL_INDEX_NAMES.items():
            matched = frame[frame["名称"].astype(str).apply(lambda value: any(alias in value for alias in aliases))]
            if matched.empty:
                continue
            row = matched.iloc[0]
            items.append({
                "name": label,
                "source_name": str(row.get("名称") or label),
                "price": _safe_float(row.get("最新价")),
                "pct": _safe_float(row.get("涨跌幅")),
                "market_time": str(row.get("最新行情时间") or ""),
            })
        payload = {
            "status": "ok" if items else "partial",
            "items": items,
            "updated_at": datetime.now().isoformat(),
            "cache_hit": False,
            "source": "AKShare index_global_spot_em / Eastmoney",
        }
        set_cached_data(GLOBAL_MARKET_CACHE_KEY, payload)
        return payload
    except Exception as exc:
        logger.warning(f"Eastmoney global market context unavailable, trying Tencent: {exc}")
        try:
            response = requests.get(
                f"http://qt.gtimg.cn/q={','.join(TENCENT_GLOBAL_SYMBOLS.values())}",
                timeout=8,
                proxies={"http": None, "https": None},
            )
            response.raise_for_status()
            response.encoding = "gbk"
            symbol_to_name = {symbol: name for name, symbol in TENCENT_GLOBAL_SYMBOLS.items()}
            items = []
            for line in response.text.split(";"):
                if '="' not in line:
                    continue
                symbol = line.split("=", 1)[0].replace("v_", "").strip()
                if symbol not in symbol_to_name:
                    continue
                values = line.split('"', 2)[1].split("~")
                if len(values) < 33:
                    continue
                items.append({
                    "name": symbol_to_name[symbol],
                    "source_name": values[1] or symbol_to_name[symbol],
                    "price": _safe_float(values[3]),
                    "pct": _safe_float(values[32]),
                    "market_time": values[30] if len(values) > 30 else "",
                })
            if not items:
                raise ValueError("empty Tencent global index response")
            payload = {
                "status": "ok",
                "items": items,
                "updated_at": datetime.now().isoformat(),
                "cache_hit": False,
                "source": "Tencent qt.gtimg.cn",
            }
            set_cached_data(GLOBAL_MARKET_CACHE_KEY, payload)
            return payload
        except Exception as fallback_exc:
            stale = get_stale_cache(GLOBAL_MARKET_CACHE_KEY)
            if stale is not None:
                return {**stale, "status": "stale", "cache_hit": True, "cache_stale": True}
            logger.warning(f"Global market research context unavailable: {fallback_exc}")
        return {
            "status": "unavailable",
            "items": [],
            "updated_at": datetime.now().isoformat(),
            "cache_hit": False,
            "source": "AKShare index_global_spot_em / Eastmoney",
            "error": str(exc)[:160],
        }


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
        return round(number, 3) if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def build_ai_research_context(
    *,
    domestic_indices: Dict[str, Any],
    global_market: Dict[str, Any],
    daily_report: Dict[str, Any],
    strategy_health: Dict[str, Any],
    research_radar: Dict[str, Any],
) -> Dict[str, Any]:
    radar_items = []
    for item in (research_radar.get("items") or [])[:8]:
        radar_items.append({
            "code": item.get("code"),
            "name": item.get("name"),
            "industry": item.get("industry"),
            "scopes": item.get("scopes") or [],
            "evidence": (item.get("evidence") or [])[:4],
        })
    return {
        "as_of": datetime.now().isoformat(),
        "contract_version": "ai-research-context-v1",
        "market": {
            "domestic_indices": domestic_indices,
            "global_indices": global_market,
        },
        "decision": {
            "scan_date": daily_report.get("scan_date"),
            "summary": daily_report.get("summary") or {},
            "top_candidates": (daily_report.get("top_candidates") or [])[:8],
            "sector_push_gaps": (daily_report.get("sector_push_gaps") or [])[:5],
            "next_actions": daily_report.get("next_actions") or [],
        },
        "strategy_health": strategy_health.get("strategies") or {},
        "candidate_evidence": {
            "summary": research_radar.get("summary") or {},
            "items": radar_items,
        },
        "analysis_framework": [
            "先核对数据日期与数据完整性",
            "区分结构评级、交易资格和研究证据",
            "按市场环境、板块阶段、个股角色和确认价解释",
            "资讯只作为风险或催化证据，不直接生成买点",
            "结论必须列出确认条件、失效条件和仍缺失的数据",
        ],
        "policy": "只读研究上下文，不修改策略参数、评分、A级或交易资格",
    }
