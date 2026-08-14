"""
Direct market data helpers adapted from simonlin1212/a-stock-data.

These functions avoid third-party wrapper APIs for small, well-scoped data
fetches. Keep them as adapters: callers should normalise data before feeding it
into strategy logic.
"""
import os
import json
import random
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
import pandas as pd

from core.logging_config import logger


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_API = "https://reportapi.eastmoney.com/report/list"
THS_HOT_URL = "http://zx.10jqka.com.cn/event/api/getharden/date/{date}/orderby/date/orderway/desc/charset/GBK/"
EM_MIN_INTERVAL = float(os.getenv("EASTMONEY_MIN_INTERVAL_SECONDS", "1.0"))
EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})

_em_lock = threading.Lock()
_em_last_call = 0.0
_cninfo_orgid_map: Dict[str, str] = {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        text = str(value).replace(",", "").strip()
        if text in {"", "-", "--"}:
            return default
        return float(text)
    except Exception:
        return default


def _is_stock_code(code: str) -> bool:
    return bool(re.match(r"^\d{6}$", str(code)))


def market_id_for_code(code: str) -> int:
    return 1 if str(code).startswith(("6", "9")) else 0


def prefixed_code(code: str) -> str:
    code = str(code).strip()
    if code.startswith(("4", "8", "920")):
        return f"bj{code}"
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def em_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    **kwargs: Any,
) -> requests.Response:
    """Eastmoney request entry with serial throttling and session reuse."""
    global _em_last_call
    with _em_lock:
        wait = EM_MIN_INTERVAL - (time.time() - _em_last_call)
        if wait > 0:
            time.sleep(wait + random.uniform(0.1, 0.5))
        try:
            return EM_SESSION.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
        finally:
            _em_last_call = time.time()


def eastmoney_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
) -> List[Dict[str, Any]]:
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    response = em_get(DATACENTER_URL, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()
    return ((payload.get("result") or {}).get("data") or [])


def tencent_quote(codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch real-time Tencent quotes for A-share stocks, indices, and ETFs.

    Returns a dict keyed by the 6-digit code. Units follow Tencent's payload:
    amount_wan is in ten-thousand yuan, market caps are in 100 million yuan.
    """
    clean_codes = [str(code).strip() for code in codes if _is_stock_code(str(code).strip())]
    if not clean_codes:
        return {}

    url = TENCENT_QUOTE_URL + ",".join(prefixed_code(code) for code in clean_codes)
    response = requests.get(url, headers={"User-Agent": UA}, timeout=10)
    response.raise_for_status()
    response.encoding = "gbk"

    result: Dict[str, Dict[str, Any]] = {}
    for line in response.text.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=", 1)[0].split("_")[-1]
        values = line.split('"', 2)[1].split("~")
        if len(values) < 53:
            continue
        code = key[2:]
        result[code] = {
            "name": values[1],
            "price": _safe_float(values[3]),
            "last_close": _safe_float(values[4]),
            "open": _safe_float(values[5]),
            "vol": _safe_float(values[6]),
            "quote_time": values[30] if len(values) > 30 else "",
            "change_amt": _safe_float(values[31]),
            "change_pct": _safe_float(values[32]),
            "high": _safe_float(values[33]),
            "low": _safe_float(values[34]),
            "amount_wan": _safe_float(values[37]),
            "turnover_pct": _safe_float(values[38]),
            "pe_ttm": _safe_float(values[39]),
            "amplitude_pct": _safe_float(values[43]),
            "mcap_yi": _safe_float(values[44]),
            "float_mcap_yi": _safe_float(values[45]),
            "pb": _safe_float(values[46]),
            "limit_up": _safe_float(values[47]),
            "limit_down": _safe_float(values[48]),
            "vol_ratio": _safe_float(values[49]),
            "pe_static": _safe_float(values[52]),
        }
    return result


def eastmoney_concept_blocks(code: str) -> Dict[str, Any]:
    """Fetch all Eastmoney sector/concept memberships for a stock."""
    code = str(code).strip()
    if not _is_stock_code(code):
        return {"total": 0, "boards": [], "concept_tags": []}

    params = {
        "fltt": "2",
        "invt": "2",
        "secid": f"{market_id_for_code(code)}.{code}",
        "spt": "3",
        "pi": "0",
        "pz": "200",
        "po": "1",
        "fields": "f12,f14,f3,f128",
    }
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    try:
        response = em_get(
            "https://push2.eastmoney.com/api/qt/slist/get",
            params=params,
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(f"Eastmoney concept block fetch failed for {code}: {exc}")
        return {"total": 0, "boards": [], "concept_tags": []}

    diff = (payload.get("data") or {}).get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    boards = [
        {
            "name": item.get("f14", ""),
            "code": item.get("f12", ""),
            "change_pct": _safe_float(item.get("f3")),
            "lead_stock": item.get("f128", ""),
        }
        for item in items
        if isinstance(item, dict)
    ]
    return {
        "total": len(boards),
        "boards": boards,
        "concept_tags": [board["name"] for board in boards if board.get("name")],
    }


def ths_hot_reason(trade_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch THS strong-stock theme attribution for a trading date."""
    trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
    try:
        response = requests.get(
            THS_HOT_URL.format(date=trade_date),
            headers={"User-Agent": UA},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(f"THS hot reason fetch failed for {trade_date}: {exc}")
        return []

    if payload.get("errocode", 0) != 0:
        logger.warning(f"THS hot reason returned error for {trade_date}: {payload.get('errormsg', '')}")
        return []

    rows = []
    for row in payload.get("data") or []:
        code = str(row.get("code") or "").zfill(6)
        if not _is_stock_code(code):
            continue
        rows.append({
            "code": code,
            "name": row.get("name", ""),
            "reason": row.get("reason", ""),
            "close": _safe_float(row.get("close")),
            "change_pct": _safe_float(row.get("zhangfu")),
            "turnover_pct": _safe_float(row.get("huanshou")),
            "amount": _safe_float(row.get("chengjiaoe")),
            "volume": _safe_float(row.get("chengjiaoliang")),
            "large_order_net": _safe_float(row.get("ddejingliang")),
            "market": row.get("market", ""),
        })
    return rows


def eastmoney_fund_flow_minute(code: str) -> List[Dict[str, Any]]:
    """Fetch intraday minute-level Eastmoney fund-flow rows, amount unit is yuan."""
    code = str(code).strip()
    if not _is_stock_code(code):
        return []

    params = {
        "secid": f"{market_id_for_code(code)}.{code}",
        "klt": "1",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }
    try:
        response = em_get(
            "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
            params=params,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(f"Eastmoney minute fund-flow fetch failed for {code}: {exc}")
        return []

    rows = []
    for line in (payload.get("data") or {}).get("klines", []) or []:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        rows.append({
            "time": parts[0],
            "main_net": _safe_float(parts[1]),
            "small_net": _safe_float(parts[2]),
            "mid_net": _safe_float(parts[3]),
            "large_net": _safe_float(parts[4]),
            "super_net": _safe_float(parts[5]),
        })
    return rows


def industry_comparison(top_n: int = 20) -> Dict[str, Any]:
    """Fetch Eastmoney industry board ranking."""
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
    }
    try:
        response = em_get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params=params,
            headers={"User-Agent": UA},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(f"Eastmoney industry comparison fetch failed: {exc}")
        return {"top": [], "bottom": [], "total": 0}

    diff = (payload.get("data") or {}).get("diff") or []
    items = diff.values() if isinstance(diff, dict) else diff
    rows = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        rows.append({
            "rank": index + 1,
            "name": item.get("f14", ""),
            "code": item.get("f12", ""),
            "change_pct": _safe_float(item.get("f3")),
            "up_count": int(_safe_float(item.get("f104"))),
            "down_count": int(_safe_float(item.get("f105"))),
            "lead_stock": item.get("f140") or item.get("f128", ""),
            "leader_change_pct": _safe_float(item.get("f136")),
        })
    return {
        "top": rows[:top_n],
        "bottom": rows[-top_n:] if top_n else [],
        "total": len(rows),
    }


def daily_dragon_tiger(trade_date: Optional[str] = None, min_net_buy_wan: Optional[float] = None) -> Dict[str, Any]:
    """Fetch daily all-market dragon-tiger board summary."""
    trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
    try:
        data = eastmoney_datacenter(
            "RPT_DAILYBILLBOARD_DETAILSNEW",
            filter_str=f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
            page_size=500,
            sort_columns="BILLBOARD_NET_AMT",
            sort_types="-1",
        )
    except Exception as exc:
        logger.warning(f"Daily dragon tiger fetch failed for {trade_date}: {exc}")
        return {"date": trade_date, "total_records": 0, "stocks": [], "note": str(exc)[:120]}

    stocks = []
    for row in data:
        net_buy_wan = _safe_float(row.get("BILLBOARD_NET_AMT")) / 10000
        if min_net_buy_wan is not None and net_buy_wan < min_net_buy_wan:
            continue
        stocks.append({
            "code": row.get("SECURITY_CODE", ""),
            "name": row.get("SECURITY_NAME_ABBR", ""),
            "reason": row.get("EXPLANATION", ""),
            "close": _safe_float(row.get("CLOSE_PRICE")),
            "change_pct": round(_safe_float(row.get("CHANGE_RATE")), 2),
            "net_buy_wan": round(net_buy_wan, 1),
            "buy_wan": round(_safe_float(row.get("BILLBOARD_BUY_AMT")) / 10000, 1),
            "sell_wan": round(_safe_float(row.get("BILLBOARD_SELL_AMT")) / 10000, 1),
            "turnover_pct": round(_safe_float(row.get("TURNOVERRATE")), 2),
        })
    actual_date = str(data[0].get("TRADE_DATE", ""))[:10] if data else trade_date
    return {"date": actual_date, "total_records": len(stocks), "stocks": stocks}


def dragon_tiger_board(code: str, trade_date: Optional[str] = None, look_back: int = 30) -> Dict[str, Any]:
    code = str(code).strip()
    if not _is_stock_code(code):
        return {"records": [], "seats": {"buy": [], "sell": []}, "institution": {}}
    trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
    start = datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)
    start_str = start.strftime("%Y-%m-%d")

    try:
        data = eastmoney_datacenter(
            "RPT_DAILYBILLBOARD_DETAILSNEW",
            filter_str=f"(TRADE_DATE>='{start_str}')(TRADE_DATE<='{trade_date}')(SECURITY_CODE=\"{code}\")",
            page_size=50,
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
    except Exception as exc:
        logger.warning(f"Dragon tiger board fetch failed for {code}: {exc}")
        return {"records": [], "seats": {"buy": [], "sell": []}, "institution": {}}

    records = [{
        "date": str(row.get("TRADE_DATE", ""))[:10],
        "reason": row.get("EXPLANATION", ""),
        "net_buy_wan": round(_safe_float(row.get("BILLBOARD_NET_AMT")) / 10000, 1),
        "turnover": round(_safe_float(row.get("TURNOVERRATE")), 2),
    } for row in data]

    seats = {"buy": [], "sell": []}
    institution = {"buy_amt_wan": 0.0, "sell_amt_wan": 0.0, "net_amt_wan": 0.0}
    if not records:
        return {"records": records, "seats": seats, "institution": institution}

    latest_date = records[0]["date"]
    detail_sets = []
    for side, report_name, sort_column in (
        ("buy", "RPT_BILLBOARD_DAILYDETAILSBUY", "BUY"),
        ("sell", "RPT_BILLBOARD_DAILYDETAILSSELL", "SELL"),
    ):
        try:
            detail_data = eastmoney_datacenter(
                report_name,
                filter_str=f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")",
                page_size=10,
                sort_columns=sort_column,
                sort_types="-1",
            )
        except Exception as exc:
            logger.warning(f"Dragon tiger {side} seats fetch failed for {code}: {exc}")
            detail_data = []
        detail_sets.append((side, detail_data))
        for row in detail_data[:5]:
            seats[side].append({
                "name": row.get("OPERATEDEPT_NAME", ""),
                "buy_amt_wan": round(_safe_float(row.get("BUY")) / 10000, 1),
                "sell_amt_wan": round(_safe_float(row.get("SELL")) / 10000, 1),
                "net_wan": round(_safe_float(row.get("NET")) / 10000, 1),
            })

    for side, detail_data in detail_sets:
        for row in detail_data:
            if str(row.get("OPERATEDEPT_CODE", "")) == "0":
                if side == "buy":
                    institution["buy_amt_wan"] += _safe_float(row.get("BUY")) / 10000
                else:
                    institution["sell_amt_wan"] += _safe_float(row.get("SELL")) / 10000
    institution["buy_amt_wan"] = round(institution["buy_amt_wan"], 1)
    institution["sell_amt_wan"] = round(institution["sell_amt_wan"], 1)
    institution["net_amt_wan"] = round(institution["buy_amt_wan"] - institution["sell_amt_wan"], 1)
    return {"records": records, "seats": seats, "institution": institution}


def lockup_expiry(code: str, trade_date: Optional[str] = None, forward_days: int = 90) -> Dict[str, List[Dict[str, Any]]]:
    code = str(code).strip()
    if not _is_stock_code(code):
        return {"history": [], "upcoming": []}
    trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
    try:
        history_data = eastmoney_datacenter(
            "RPT_LIFT_STAGE",
            filter_str=f"(SECURITY_CODE=\"{code}\")",
            page_size=15,
            sort_columns="FREE_DATE",
            sort_types="-1",
        )
        end_str = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=forward_days)).strftime("%Y-%m-%d")
        upcoming_data = eastmoney_datacenter(
            "RPT_LIFT_STAGE",
            filter_str=f"(SECURITY_CODE=\"{code}\")(FREE_DATE>='{trade_date}')(FREE_DATE<='{end_str}')",
            page_size=20,
            sort_columns="FREE_DATE",
            sort_types="1",
        )
    except Exception as exc:
        logger.warning(f"Lockup expiry fetch failed for {code}: {exc}")
        return {"history": [], "upcoming": []}

    def normalise(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "date": str(row.get("FREE_DATE", ""))[:10],
            "type": row.get("LIMITED_STOCK_TYPE", ""),
            "shares": _safe_float(row.get("FREE_SHARES_NUM")),
            "ratio": _safe_float(row.get("FREE_RATIO")),
        }

    return {
        "history": [normalise(row) for row in history_data],
        "upcoming": [normalise(row) for row in upcoming_data],
    }


def margin_trading(code: str, page_size: int = 30) -> List[Dict[str, Any]]:
    try:
        data = eastmoney_datacenter(
            "RPTA_WEB_RZRQ_GGMX",
            filter_str=f'(SCODE="{code}")',
            page_size=page_size,
            sort_columns="DATE",
            sort_types="-1",
        )
    except Exception as exc:
        logger.warning(f"Margin trading fetch failed for {code}: {exc}")
        return []
    return [{
        "date": str(row.get("DATE", ""))[:10],
        "rzye": _safe_float(row.get("RZYE")),
        "rzmre": _safe_float(row.get("RZMRE")),
        "rzche": _safe_float(row.get("RZCHE")),
        "rqye": _safe_float(row.get("RQYE")),
        "rqmcl": _safe_float(row.get("RQMCL")),
        "rqchl": _safe_float(row.get("RQCHL")),
        "rzrqye": _safe_float(row.get("RZRQYE")),
    } for row in data]


def block_trade(code: str, page_size: int = 20) -> List[Dict[str, Any]]:
    try:
        data = eastmoney_datacenter(
            "RPT_DATA_BLOCKTRADE",
            filter_str=f'(SECURITY_CODE="{code}")',
            page_size=page_size,
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
    except Exception as exc:
        logger.warning(f"Block trade fetch failed for {code}: {exc}")
        return []
    rows = []
    for row in data:
        close = _safe_float(row.get("CLOSE_PRICE"))
        price = _safe_float(row.get("DEAL_PRICE"))
        rows.append({
            "date": str(row.get("TRADE_DATE", ""))[:10],
            "price": price,
            "close": close,
            "premium_pct": round((price / close - 1) * 100, 2) if close else 0.0,
            "vol": _safe_float(row.get("DEAL_VOLUME")),
            "amount": _safe_float(row.get("DEAL_AMT")),
            "buyer": row.get("BUYER_NAME", ""),
            "seller": row.get("SELLER_NAME", ""),
        })
    return rows


def holder_num_change(code: str, page_size: int = 10) -> List[Dict[str, Any]]:
    try:
        data = eastmoney_datacenter(
            "RPT_HOLDERNUMLATEST",
            filter_str=f'(SECURITY_CODE="{code}")',
            page_size=page_size,
            sort_columns="END_DATE",
            sort_types="-1",
        )
    except Exception as exc:
        logger.warning(f"Holder number fetch failed for {code}: {exc}")
        return []
    return [{
        "date": str(row.get("END_DATE", ""))[:10],
        "holder_num": _safe_float(row.get("HOLDER_NUM")),
        "change_num": _safe_float(row.get("HOLDER_NUM_CHANGE")),
        "change_ratio": _safe_float(row.get("HOLDER_NUM_RATIO")),
        "avg_shares": _safe_float(row.get("AVG_FREE_SHARES")),
    } for row in data]


def dividend_history(code: str, page_size: int = 20) -> List[Dict[str, Any]]:
    try:
        data = eastmoney_datacenter(
            "RPT_SHAREBONUS_DET",
            filter_str=f'(SECURITY_CODE="{code}")',
            page_size=page_size,
            sort_columns="EX_DIVIDEND_DATE",
            sort_types="-1",
        )
    except Exception as exc:
        logger.warning(f"Dividend history fetch failed for {code}: {exc}")
        return []
    return [{
        "date": str(row.get("EX_DIVIDEND_DATE", ""))[:10],
        "bonus_rmb": _safe_float(row.get("PRETAX_BONUS_RMB")),
        "transfer_ratio": _safe_float(row.get("TRANSFER_RATIO")),
        "bonus_ratio": _safe_float(row.get("BONUS_RATIO")),
        "plan": row.get("ASSIGN_PROGRESS", ""),
    } for row in data]


def eastmoney_reports(code: str, max_pages: int = 1) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*",
            "pageSize": "20",
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": "2000-01-01",
            "endTime": "2030-01-01",
            "pageNo": str(page),
            "fields": "",
            "qType": "0",
            "orgCode": "",
            "code": code,
            "rcode": "",
            "p": str(page),
            "pageNum": str(page),
            "pageNumber": str(page),
        }
        try:
            response = em_get(REPORT_API, params=params, headers={"Referer": "https://data.eastmoney.com/"}, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning(f"Eastmoney reports fetch failed for {code}: {exc}")
            break
        rows = payload.get("data") or []
        if not rows:
            break
        records.extend(rows)
        if page >= int(payload.get("TotalPage") or 1):
            break
    return [{
        "title": row.get("title", ""),
        "publish_date": str(row.get("publishDate", ""))[:10],
        "org": row.get("orgSName", ""),
        "rating": row.get("emRatingName", ""),
        "eps_this_year": _safe_float(row.get("predictThisYearEps")),
        "eps_next_year": _safe_float(row.get("predictNextYearEps")),
        "info_code": row.get("infoCode", ""),
    } for row in records]


def eastmoney_stock_news(code: str, page_size: int = 20) -> List[Dict[str, Any]]:
    params = {
        "cb": "jQuery_news",
        "param": json.dumps({
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": page_size,
                    "preTag": "",
                    "postTag": "",
                }
            },
        }, separators=(",", ":")),
    }
    try:
        response = em_get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params=params,
            headers={"User-Agent": UA, "Referer": "https://so.eastmoney.com/"},
            timeout=15,
        )
        text = response.text
        payload = json.loads(text[text.index("(") + 1:text.rindex(")")])
    except Exception as exc:
        logger.warning(f"Eastmoney stock news fetch failed for {code}: {exc}")
        return []
    rows = []
    for item in (payload.get("result") or {}).get("cmsArticleWebOld", []) or []:
        rows.append({
            "title": re.sub(r"<[^>]+>", "", item.get("title", "")),
            "content": re.sub(r"<[^>]+>", "", item.get("content", ""))[:200],
            "time": item.get("date", ""),
            "source": item.get("mediaName", ""),
            "url": item.get("url", ""),
        })
    return rows


def _cninfo_ts_to_date(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d")
    return str(value)[:10] if value else ""


def _cninfo_orgid(code: str) -> str:
    global _cninfo_orgid_map
    if not _cninfo_orgid_map:
        try:
            response = requests.get(
                "http://www.cninfo.com.cn/new/data/szse_stock.json",
                headers={"User-Agent": UA},
                timeout=15,
            )
            response.raise_for_status()
            _cninfo_orgid_map = {
                item["code"]: item["orgId"]
                for item in response.json().get("stockList", [])
                if item.get("code") and item.get("orgId")
            }
        except Exception as exc:
            logger.warning(f"Cninfo orgId map fetch failed, using fallback: {exc}")
    if code in _cninfo_orgid_map:
        return _cninfo_orgid_map[code]
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("4", "8", "920")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def cninfo_announcements(code: str, page_size: int = 20) -> List[Dict[str, Any]]:
    code = str(code).strip()
    if not _is_stock_code(code):
        return []
    payload = {
        "stock": f"{code},{_cninfo_orgid(code)}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    }
    try:
        response = requests.post(
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
            data=payload,
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.warning(f"Cninfo announcements fetch failed for {code}: {exc}")
        return []
    return [{
        "title": item.get("announcementTitle", ""),
        "type": item.get("announcementTypeName", ""),
        "date": _cninfo_ts_to_date(item.get("announcementTime")),
        "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={item.get('announcementId', '')}",
    } for item in data.get("announcements", []) or []]


def stock_fund_flow_120d(code: str) -> List[Dict[str, Any]]:
    """Fetch recent daily Eastmoney stock fund-flow rows, amount unit is yuan."""
    code = str(code).strip()
    if not _is_stock_code(code):
        return []

    params = {
        "secid": f"{market_id_for_code(code)}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }
    try:
        response = em_get(
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            params=params,
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(f"Eastmoney fund-flow fetch failed for {code}: {exc}")
        return []

    rows = []
    for line in (payload.get("data") or {}).get("klines", []) or []:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        rows.append({
            "date": parts[0],
            "main_net": _safe_float(parts[1]),
            "small_net": _safe_float(parts[2]),
            "mid_net": _safe_float(parts[3]),
            "large_net": _safe_float(parts[4]),
            "super_net": _safe_float(parts[5]),
        })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# P2：全市场实时快照直连源（供 core/data.py:get_market_snapshot 容灾链调用）
# 设计目标：绕过 akshare wrapper 层的封禁，并补充独立提供商降低单源风险。
# 返回 DataFrame 必须对齐 11 列：
#   code/name/price/open/high/low/pct_chg/vol/turnover/mkt_cap/pe
# ─────────────────────────────────────────────────────────────────────────────

# 东财 push2/clist 字段映射（依据 akshare/stock_feature/stock_hist_em.py:37-70）
# f2=最新价 f3=涨跌幅 f5=成交量(手) f8=换手率 f9=市盈率-动态 f12=代码 f14=名称
# f15=最高 f16=最低 f17=今开 f20=总市值(元)
_EM_SNAPSHOT_FIELDS = "f2,f3,f5,f8,f9,f12,f14,f15,f16,f17,f20"
# 全 A 股市场过滤（沪深主板/创业板/科创板/北交所），与 akshare stock_zh_a_spot_em 一致
_EM_SNAPSHOT_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"


def snapshot_from_eastmoney() -> pd.DataFrame:
    """直连东财 push2/clist 抓全市场实时快照（绕过 akshare wrapper 封禁）。

    复用 em_get 节流器（EM_MIN_INTERVAL + session 复用），单次分页(pz=6000)返回全市场。
    返回对齐快照 11 列的 DataFrame；失败抛异常交由 resilient_fetch 降级。
    """
    time.sleep(random.uniform(0.1, 0.5))
    params = {
        "pn": "1",
        "pz": "6000",  # 单页取全市场(~5200只)，避免翻页往返
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": _EM_SNAPSHOT_FS,
        "fields": _EM_SNAPSHOT_FIELDS,
    }
    response = em_get(
        "https://82.push2.eastmoney.com/api/qt/clist/get",
        params=params,
        headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    diff = (payload.get("data") or {}).get("diff") or []
    items = diff.values() if isinstance(diff, dict) else diff

    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("f12", "")).strip()
        if not code:
            continue
        rows.append({
            "code": code,
            "name": item.get("f14", ""),
            "price": _safe_float(item.get("f2")) or None,
            "open": _safe_float(item.get("f17")) or None,
            "high": _safe_float(item.get("f15")) or None,
            "low": _safe_float(item.get("f16")) or None,
            "pct_chg": _safe_float(item.get("f3")) or 0.0,
            "vol": _safe_float(item.get("f5")) or 0.0,
            "turnover": _safe_float(item.get("f8")) or None,
            "mkt_cap": _safe_float(item.get("f20")) or None,
            "pe": _safe_float(item.get("f9")) or None,
        })
    if not rows:
        raise ValueError("Eastmoney direct returned no rows")
    # 完整性自检:东财被限流时常返回截断的 100 行(应 ~5500)。
    # 抛异常交由 resilient_fetch 降级到腾讯/新浪，避免残缺数据"假成功"。
    if len(rows) < 4000:
        raise ValueError(
            f"Eastmoney direct returned incomplete data: {len(rows)} rows (expected ~5000+, likely rate-limited)"
        )
    logger.info(f"Eastmoney direct snapshot fetched: {len(rows)} rows.")
    return pd.DataFrame(rows)


# 新浪 hq.sinajs.cn 批量上限（单 URL 约容纳 ~800 只，保守取 600）
_SINA_BATCH_SIZE = 600


def _sina_prefix(code: str) -> str:
    """沪市→sh，深市→sz，北交所→bj。前缀规则对齐 data.py 的 format_tencent_code。
    注意：920 开头是北交所新代码段（不是沪市 9 字头），必须先于单字符 9 判断。"""
    if code.startswith(("4", "8", "920")):
        return f"bj{code}"
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def snapshot_from_sina(codes: List[str]) -> pd.DataFrame:
    """直连新浪 hq.sinajs.cn 抓实时快照（绕过 akshare 的 py_mini_racer V8 段错误）。

    返回格式 `var hq_str_sh600519="贵州茅台,昨收,今开,现价,...";`（GBK 编码，逗号分隔）。
    用标准库 re 解析，无需 demjson3 依赖。分批并发复用 ThreadPoolExecutor。
    失败抛异常交由 resilient_fetch 降级。
    """
    if not codes:
        raise ValueError("Sina direct: empty code list")
    time.sleep(random.uniform(0.1, 0.5))

    def fetch_sina_batch(batch_codes):
        symbols = ",".join(_sina_prefix(c) for c in batch_codes)
        url = f"http://hq.sinajs.cn/list={symbols}"
        try:
            r = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
            if r.status_code != 200:
                return []
            r.encoding = "gbk"
            results = []
            # 每行形如：var hq_str_sh600519="名称,今开,昨收,现价,最高,最低,...,成交量(股),成交额(元),...";
            for line in r.text.strip().split("\n"):
                m = re.match(r'var hq_str_\w+="([^"]*)"', line.strip())
                if not m:
                    continue
                parts = m.group(1).split(",")
                # 新浪 A 股字段：0=名称 1=今开 2=昨收 3=现价 4=最高 5=最低
                # 8=成交量(股) 9=成交额(元)。换手率/市值/PE 此接口不提供，留 None。
                if len(parts) < 10:
                    continue
                try:
                    price = float(parts[3]) if parts[3] else None
                    if not price:
                        continue
                    code_m = re.search(r'(\d{6})', line)
                    if not code_m:
                        continue
                    code = code_m.group(1)
                    yclose = float(parts[2]) if parts[2] else price
                    pct_chg = round((price - yclose) / yclose * 100, 2) if yclose else 0.0
                    results.append({
                        "code": code,
                        "name": parts[0],
                        "price": price,
                        "open": float(parts[1]) if parts[1] else None,
                        "high": float(parts[4]) if parts[4] else None,
                        "low": float(parts[5]) if parts[5] else None,
                        "pct_chg": pct_chg,
                        "vol": float(parts[8]) / 100.0 if parts[8] else 0.0,  # 股→手
                        "quote_time": (
                            f"{parts[30]} {parts[31]}"
                            if len(parts) > 31 and parts[30]
                            else ""
                        ),
                        "turnover": None,
                        "mkt_cap": None,
                        "pe": None,
                    })
                except (ValueError, AttributeError):
                    continue
            return results
        except Exception as e:
            logger.debug(f"Sina batch fetch failed: {e}")
            return []

    from concurrent.futures import ThreadPoolExecutor, as_completed
    batches = [codes[i:i + _SINA_BATCH_SIZE] for i in range(0, len(codes), _SINA_BATCH_SIZE)]
    all_results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_sina_batch, b): b for b in batches}
        for future in as_completed(futures):
            res = future.result()
            if res:
                all_results.extend(res)

    if not all_results:
        raise ValueError("Sina direct returned no rows")
    logger.info(f"Sina direct snapshot fetched: {len(all_results)} rows.")
    return pd.DataFrame(all_results)
