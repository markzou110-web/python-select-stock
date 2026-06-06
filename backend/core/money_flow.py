import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd

from core.data import get_cached_data, get_stale_cache, set_cached_data
from core.db import validate_stock_code
from core.logging_config import logger


STOCK_FLOW_TTL_SECONDS = int(os.getenv("MONEY_FLOW_STOCK_TTL_SECONDS", "1800"))
RANK_FLOW_TTL_SECONDS = int(os.getenv("MONEY_FLOW_RANK_TTL_SECONDS", "1800"))
MIN_REQUEST_INTERVAL_SECONDS = float(os.getenv("MONEY_FLOW_MIN_INTERVAL_SECONDS", "2.0"))

_request_lock = threading.Lock()
_last_request_at = 0.0


def _market_for_code(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()
            if value in {"", "-", "--"}:
                return default
        return float(value)
    except Exception:
        return default


def _money_text_to_yuan(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if text in {"", "-", "--"}:
        return default
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 100000000.0
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10000.0
        text = text[:-1]
    return _safe_float(text, default) * multiplier


def _pick_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for col in candidates:
        if col in df.columns:
            return col
    for candidate in candidates:
        for col in df.columns:
            if candidate in str(col):
                return col
    return None


def _throttled_call(func, *args, **kwargs):
    global _last_request_at
    with _request_lock:
        elapsed = time.time() - _last_request_at
        wait = MIN_REQUEST_INTERVAL_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)
        result = func(*args, **kwargs)
        _last_request_at = time.time()
        return result


def _normalise_stock_flow(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    date_col = _pick_column(df, ["日期", "date"])
    close_col = _pick_column(df, ["收盘价", "收盘", "close"])
    pct_col = _pick_column(df, ["涨跌幅", "pct"])
    main_col = _pick_column(df, ["主力净流入-净额", "主力净流入净额", "主力净流入"])
    main_ratio_col = _pick_column(df, ["主力净流入-净占比", "主力净占比"])
    super_col = _pick_column(df, ["超大单净流入-净额", "超大单净流入"])
    big_col = _pick_column(df, ["大单净流入-净额", "大单净流入"])
    medium_col = _pick_column(df, ["中单净流入-净额", "中单净流入"])
    small_col = _pick_column(df, ["小单净流入-净额", "小单净流入"])

    rows = []
    for _, row in df.iterrows():
        rows.append({
            "date": str(row.get(date_col, ""))[:10] if date_col else "",
            "close": round(_safe_float(row.get(close_col)), 2) if close_col else None,
            "pct": round(_safe_float(row.get(pct_col)), 2) if pct_col else None,
            "main_net_inflow": round(_safe_float(row.get(main_col)), 2) if main_col else 0.0,
            "main_net_ratio": round(_safe_float(row.get(main_ratio_col)), 2) if main_ratio_col else 0.0,
            "super_net_inflow": round(_safe_float(row.get(super_col)), 2) if super_col else 0.0,
            "big_net_inflow": round(_safe_float(row.get(big_col)), 2) if big_col else 0.0,
            "medium_net_inflow": round(_safe_float(row.get(medium_col)), 2) if medium_col else 0.0,
            "small_net_inflow": round(_safe_float(row.get(small_col)), 2) if small_col else 0.0,
        })
    return pd.DataFrame(rows)


def _amount_yi(value: float) -> float:
    return round(value / 100000000, 2)


def _consecutive_direction(values: List[float]) -> Dict[str, Any]:
    direction = "flat"
    count = 0
    for value in reversed(values):
        if value > 0:
            current = "inflow"
        elif value < 0:
            current = "outflow"
        else:
            current = "flat"
        if count == 0:
            direction = current
            count = 1 if current != "flat" else 0
        elif current == direction:
            count += 1
        else:
            break
    return {"direction": direction, "days": count}


def _summarise_stock_flow(code: str, df: pd.DataFrame, source: str, cache_hit: bool) -> Dict[str, Any]:
    records = df.tail(30).where(pd.notna(df), None).to_dict("records")
    if df.empty:
        return {
            "status": "empty",
            "code": code,
            "source": source,
            "cache_hit": cache_hit,
            "latest": {},
            "history": [],
            "summary": {},
            "updated_at": datetime.now().isoformat(),
        }

    latest = df.iloc[-1].to_dict()
    main_values = [_safe_float(v) for v in df["main_net_inflow"].tolist()]
    last3 = main_values[-3:]
    last5 = main_values[-5:]
    consecutive = _consecutive_direction(main_values)
    summary = {
        "main_net_3d": round(sum(last3), 2),
        "main_net_5d": round(sum(last5), 2),
        "main_net_3d_yi": _amount_yi(sum(last3)),
        "main_net_5d_yi": _amount_yi(sum(last5)),
        "consecutive_direction": consecutive["direction"],
        "consecutive_days": consecutive["days"],
        "bias": "inflow" if latest.get("main_net_inflow", 0) > 0 else ("outflow" if latest.get("main_net_inflow", 0) < 0 else "neutral"),
    }
    latest["main_net_inflow_yi"] = _amount_yi(_safe_float(latest.get("main_net_inflow")))
    latest["super_net_inflow_yi"] = _amount_yi(_safe_float(latest.get("super_net_inflow")))
    latest["big_net_inflow_yi"] = _amount_yi(_safe_float(latest.get("big_net_inflow")))
    return {
        "status": "ok",
        "code": code,
        "source": source,
        "cache_hit": cache_hit,
        "latest": latest,
        "summary": summary,
        "history": records,
        "rate_limit": {
            "ttl_seconds": STOCK_FLOW_TTL_SECONDS,
            "min_request_interval_seconds": MIN_REQUEST_INTERVAL_SECONDS,
        },
        "updated_at": datetime.now().isoformat(),
    }


def get_stock_money_flow(code: str, force_refresh: bool = False) -> Dict[str, Any]:
    if not validate_stock_code(code):
        return {"status": "error", "code": code, "error": "Invalid stock code"}

    cache_key = f"money_flow_stock_{code}"
    if not force_refresh:
        cached = get_cached_data(cache_key, STOCK_FLOW_TTL_SECONDS)
        if cached is not None:
            cached = {**cached, "cache_hit": True}
            return cached

    try:
        raw = _throttled_call(ak.stock_individual_fund_flow, stock=code, market=_market_for_code(code))
        df = _normalise_stock_flow(raw)
        payload = _summarise_stock_flow(code, df, "eastmoney_akshare", cache_hit=False)
        payload["flow_metric"] = "main_net_inflow"
        payload["metric_label"] = "主力净流入"
        payload["supports_order_breakdown"] = True
        set_cached_data(cache_key, payload)
        return payload
    except Exception as exc:
        logger.warning(f"Money flow fetch failed for {code}: {exc}")
        rank = get_money_flow_rank(indicator="今日", limit=6000, force_refresh=force_refresh)
        match = next((item for item in rank.get("items", []) if str(item.get("code", "")).zfill(6) == code), None)
        if match:
            main_net = _money_text_to_yuan(match.get("main_net_inflow"))
            df = pd.DataFrame([{
                "date": datetime.now().strftime("%Y-%m-%d"),
                "close": None,
                "pct": match.get("pct"),
                "main_net_inflow": main_net,
                "main_net_ratio": match.get("main_net_ratio") or 0,
                "super_net_inflow": 0.0,
                "big_net_inflow": 0.0,
                "medium_net_inflow": 0.0,
                "small_net_inflow": 0.0,
            }])
            payload = _summarise_stock_flow(code, df, f"{rank.get('source', 'rank')}_rank_fallback", cache_hit=False)
            payload["rank_fallback"] = True
            payload["source_status"] = rank.get("status")
            payload["flow_metric"] = match.get("flow_metric") or "main_net_inflow"
            payload["metric_label"] = match.get("metric_label") or "主力净流入"
            payload["supports_order_breakdown"] = payload["flow_metric"] == "main_net_inflow"
            set_cached_data(cache_key, payload)
            return payload
        stale = get_stale_cache(cache_key)
        if stale is not None:
            return {**stale, "status": "stale", "cache_hit": True, "error": str(exc)[:120]}
        return {
            "status": "degraded",
            "code": code,
            "source": "eastmoney_akshare",
            "cache_hit": False,
            "latest": {},
            "summary": {},
            "history": [],
            "error": str(exc)[:120],
            "updated_at": datetime.now().isoformat(),
        }


def _normalise_rank_rows(
    df: pd.DataFrame,
    limit: int,
    source: str,
    flow_metric: str,
    metric_label: str,
) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []
    code_col = _pick_column(df, ["代码", "股票代码", "code"])
    name_col = _pick_column(df, ["名称", "股票简称", "name"])
    pct_col = _pick_column(df, ["涨跌幅", "涨跌幅%"])
    main_col = _pick_column(df, ["主力净流入-净额", "主力净流入净额", "主力净流入", "资金流入净额", "净额"])
    ratio_col = _pick_column(df, ["主力净流入-净占比", "主力净占比"])
    rows = []
    for _, row in df.head(limit).iterrows():
        main_net = _money_text_to_yuan(row.get(main_col)) if main_col else 0.0
        rows.append({
            "code": str(row.get(code_col, "")).zfill(6) if code_col else "",
            "name": row.get(name_col, "") if name_col else "",
            "pct": round(_safe_float(row.get(pct_col)), 2) if pct_col else None,
            "main_net_inflow": round(main_net, 2),
            "main_net_inflow_yi": _amount_yi(main_net),
            "main_net_ratio": round(_safe_float(row.get(ratio_col)), 2) if ratio_col else None,
            "source": source,
            "flow_metric": flow_metric,
            "metric_label": metric_label,
        })
    return rows


def get_money_flow_rank(indicator: str = "今日", limit: int = 30, force_refresh: bool = False) -> Dict[str, Any]:
    indicator = indicator if indicator in {"今日", "3日", "5日", "10日"} else "今日"
    limit = max(1, min(int(limit), 6000))
    cache_key = f"money_flow_rank_{indicator}_{limit}"
    if not force_refresh:
        cached = get_cached_data(cache_key, RANK_FLOW_TTL_SECONDS)
        if cached is not None:
            return {**cached, "cache_hit": True}
    try:
        raw = _throttled_call(ak.stock_individual_fund_flow_rank, indicator=indicator)
        payload = {
            "status": "ok",
            "indicator": indicator,
            "source": "eastmoney_akshare",
            "cache_hit": False,
            "items": _normalise_rank_rows(
                raw,
                limit,
                source="eastmoney_akshare",
                flow_metric="main_net_inflow",
                metric_label="主力净流入",
            ),
            "updated_at": datetime.now().isoformat(),
        }
        set_cached_data(cache_key, payload)
        return payload
    except Exception as exc:
        logger.warning(f"Eastmoney money flow rank failed, trying THS fallback: {exc}")
        try:
            ths_symbol = {
                "今日": "即时",
                "3日": "3日排行",
                "5日": "5日排行",
                "10日": "10日排行",
            }[indicator]
            raw = _throttled_call(ak.stock_fund_flow_individual, symbol=ths_symbol)
            payload = {
                "status": "ok",
                "indicator": indicator,
                "source": "ths_akshare",
                "cache_hit": False,
                "items": _normalise_rank_rows(
                    raw,
                    limit,
                    source="ths_akshare",
                    flow_metric="net_inflow",
                    metric_label="资金净额",
                ),
                "updated_at": datetime.now().isoformat(),
            }
            set_cached_data(cache_key, payload)
            return payload
        except Exception as fallback_exc:
            logger.warning(f"THS money flow rank fallback failed: {fallback_exc}")
        stale = get_stale_cache(cache_key)
        if stale is not None:
            return {**stale, "status": "stale", "cache_hit": True, "error": str(exc)[:120]}
        return {"status": "degraded", "indicator": indicator, "items": [], "error": str(exc)[:120]}


def get_sector_money_flow_rank(
    indicator: str = "今日",
    sector_type: str = "行业资金流",
    limit: int = 30,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    indicator = indicator if indicator in {"今日", "5日", "10日"} else "今日"
    sector_type = sector_type if sector_type in {"行业资金流", "概念资金流", "地域资金流"} else "行业资金流"
    limit = max(1, min(int(limit), 100))
    cache_key = f"money_flow_sector_{indicator}_{sector_type}_{limit}"
    if not force_refresh:
        cached = get_cached_data(cache_key, RANK_FLOW_TTL_SECONDS)
        if cached is not None:
            return {**cached, "cache_hit": True}
    try:
        raw = _throttled_call(ak.stock_sector_fund_flow_rank, indicator=indicator, sector_type=sector_type)
        payload = {
            "status": "ok",
            "indicator": indicator,
            "sector_type": sector_type,
            "source": "eastmoney_akshare",
            "cache_hit": False,
            "items": _normalise_rank_rows(
                raw,
                limit,
                source="eastmoney_akshare",
                flow_metric="sector_main_net_inflow",
                metric_label="板块主力净流入",
            ),
            "updated_at": datetime.now().isoformat(),
        }
        set_cached_data(cache_key, payload)
        return payload
    except Exception as exc:
        stale = get_stale_cache(cache_key)
        if stale is not None:
            return {**stale, "status": "stale", "cache_hit": True, "error": str(exc)[:120]}
        return {"status": "degraded", "indicator": indicator, "sector_type": sector_type, "items": [], "error": str(exc)[:120]}
