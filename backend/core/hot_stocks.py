"""Market popularity ranking and lightweight chart data for the hot-stocks view."""

from datetime import datetime, timedelta
import math
import re
from typing import Any, Dict, List, Literal, Mapping, Tuple

import akshare as ak
import pandas as pd
import requests
from sqlalchemy import text

from core.data import get_cached_data, get_stale_cache, set_cached_data
from core.db import get_db_engine, validate_stock_code
from core.direct_sources import UA, prefixed_code
from core.logging_config import logger

RankPeriod = Literal["hour", "day"]
ChartPeriod = Literal["minute", "day"]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if pd.notna(number) else default
    except (TypeError, ValueError):
        return default


def _normalise_code(value: Any) -> str:
    match = re.search(r"(\d{6})$", str(value or "").strip())
    return match.group(1) if match else ""


def _load_local_metadata(codes: List[str]) -> Dict[str, Dict[str, Any]]:
    if not codes:
        return {}
    params = {f"code_{index}": code for index, code in enumerate(codes)}
    placeholders = ", ".join(f":code_{index}" for index in range(len(codes)))
    query = text(f"""
        SELECT s.code, s.name, s.industry
        FROM stock_basic s
        WHERE s.code IN ({placeholders})
    """)
    try:
        with get_db_engine().connect() as connection:
            rows = connection.execute(query, params).mappings().all()
    except Exception as exc:
        logger.warning(f"Hot stock local metadata unavailable: {exc}")
        return {}
    return {
        str(row["code"]).zfill(6): {
            "name": row.get("name") or "",
            "industry": row.get("industry") or "",
            "amount": 0.0,
        }
        for row in rows
    }


def normalize_hot_rank_frame(
    frame: pd.DataFrame,
    period: RankPeriod,
    limit: int,
    metadata: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert the provider frame to the stable frontend contract."""
    if frame is None or frame.empty:
        return []

    items: List[Dict[str, Any]] = []
    for _, row in frame.head(limit).iterrows():
        code = _normalise_code(row.get("代码"))
        if not validate_stock_code(code):
            continue
        local = metadata.get(code, {})
        source_rank = int(_safe_float(row.get("当前排名"), len(items) + 1))
        rank_change = int(_safe_float(row.get("排名较昨日变动"))) if period == "hour" else 0
        supplied_heat_label = str(row.get("热度标签") or "").strip()
        if supplied_heat_label:
            heat_label = supplied_heat_label
        elif period == "day":
            heat_label = f"人气第 {source_rank} 名"
        elif rank_change > 0:
            heat_label = f"热度上升 {rank_change} 位"
        elif rank_change < 0:
            heat_label = f"热度回落 {abs(rank_change)} 位"
        else:
            heat_label = "热度持平"
        industry = str(local.get("industry") or "").strip()
        amount = _safe_float(local.get("amount"))
        items.append({
            "rank": len(items) + 1,
            "source_rank": source_rank,
            "rank_change": rank_change,
            "code": code,
            "name": str(row.get("股票名称") or local.get("name") or code),
            "price": round(_safe_float(row.get("最新价")), 3),
            "pct": round(_safe_float(row.get("涨跌幅")), 2),
            "heat_label": heat_label,
            "concepts": [industry] if industry and industry != "未知" else [],
            "amount_yi": round(amount / 100_000_000, 2) if amount > 0 else 0.0,
        })
    return items


def _load_local_activity_ranking(period: RankPeriod, limit: int) -> Tuple[List[Dict[str, Any]], str]:
    """Use the latest local trading day as an explicitly labelled fallback, never as fake popularity data."""
    query = text("""
        WITH recent_dates AS (
            SELECT DISTINCT date FROM daily_k ORDER BY date DESC LIMIT 2
        )
        SELECT d.code, s.name, s.industry, d.date, d.close, d.vol, p.close AS previous_close
        FROM daily_k d
        LEFT JOIN stock_basic s ON s.code = d.code
        LEFT JOIN daily_k p ON p.code = d.code
            AND p.date = (SELECT MIN(date) FROM recent_dates)
        WHERE d.date = (SELECT MAX(date) FROM recent_dates)
    """)
    try:
        with get_db_engine().connect() as connection:
            local = pd.read_sql(query, connection)
    except Exception as exc:
        logger.warning(f"Local hot stock fallback unavailable: {exc}")
        return [], ""
    if local.empty:
        return [], ""

    local["close"] = pd.to_numeric(local["close"], errors="coerce")
    local["previous_close"] = pd.to_numeric(local["previous_close"], errors="coerce")
    local["activity"] = (pd.to_numeric(local["vol"], errors="coerce").fillna(0).clip(lower=0) * local["close"]).fillna(0)
    local["pct"] = ((local["close"] / local["previous_close"] - 1) * 100).replace([float("inf"), float("-inf")], pd.NA).fillna(0)
    if period == "hour":
        local["activity_score"] = local["activity"].map(math.log1p) * (1 + local["pct"].abs() / 10)
    else:
        local["activity_score"] = local["activity"]
    local = local.dropna(subset=["close"]).sort_values("activity_score", ascending=False).head(limit).reset_index(drop=True)
    frame = pd.DataFrame({
        "当前排名": local.index + 1,
        "代码": local["code"],
        "股票名称": local["name"],
        "最新价": local["close"],
        "涨跌幅": local["pct"],
        "热度标签": [f"活跃第 {index + 1} 名" for index in local.index],
    })
    metadata = {
        str(row["code"]).zfill(6): {
            "name": row.get("name") or "",
            "industry": row.get("industry") or "",
            "amount": 0.0,
        }
        for _, row in local.iterrows()
    }
    data_date = str(local["date"].iloc[0])[:10] if not local.empty else ""
    return normalize_hot_rank_frame(frame, period, limit, metadata), data_date


def get_hot_stock_ranking(period: RankPeriod = "hour", limit: int = 30, force: bool = False) -> Dict[str, Any]:
    """Return a cached Eastmoney popularity/rising ranking enriched with local metadata."""
    safe_limit = max(1, min(int(limit), 100))
    cache_key = f"hot_stocks:ranking:{period}:{safe_limit}"
    stale = get_stale_cache(cache_key)
    if not force:
        cached = get_cached_data(cache_key, 90)
        if cached:
            return {**cached, "cache_hit": True}

    try:
        frame = ak.stock_hot_up_em() if period == "hour" else ak.stock_hot_rank_em()
        if frame is None or frame.empty:
            raise RuntimeError("热度源返回空榜单")
        codes = [_normalise_code(value) for value in frame.get("代码", pd.Series(dtype=str)).head(safe_limit)]
        items = normalize_hot_rank_frame(
            frame,
            period=period,
            limit=safe_limit,
            metadata=_load_local_metadata([code for code in codes if code]),
        )
        if not items:
            raise RuntimeError("热度榜没有有效股票代码")
        payload = {
            "status": "ok",
            "period": period,
            "items": items,
            "source": "eastmoney_hot_rank",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "cache_hit": False,
            "degraded": False,
            "note": "小时榜采用人气飙升口径，日榜采用综合人气口径；热度不等同于买入信号。",
        }
        set_cached_data(cache_key, payload)
        return payload
    except Exception as exc:
        logger.warning(f"Hot stock ranking fetch failed ({period}): {exc}")
        if stale:
            return {
                **stale,
                "status": "degraded",
                "cache_hit": True,
                "degraded": True,
                "note": "实时热度源暂不可用，当前展示最近一次成功结果。",
            }
        local_items, data_date = _load_local_activity_ranking(period, safe_limit)
        if local_items:
            return {
                "status": "degraded",
                "period": period,
                "items": local_items,
                "source": "local_market_activity_proxy",
                "data_date": data_date,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "cache_hit": False,
                "degraded": True,
                "note": f"实时人气源暂不可用，当前按 {data_date} 成交活跃度降级展示；不等同于人气排名或买入信号。",
            }
        return {
            "status": "degraded",
            "period": period,
            "items": [],
            "source": "eastmoney_hot_rank",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "cache_hit": False,
            "degraded": True,
            "note": "实时热度源暂不可用，请稍后刷新。",
        }


def normalize_chart_frame(frame: pd.DataFrame, period: ChartPeriod) -> Tuple[List[Dict[str, Any]], str]:
    """Normalize local or provider OHLCV data and keep only the latest minute session."""
    if frame is None or frame.empty:
        return [], ""
    aliases = {
        "bar_time": "时间",
        "date": "时间",
        "open": "开盘",
        "close": "收盘",
        "high": "最高",
        "low": "最低",
        "volume": "成交量",
        "vol": "成交量",
    }
    working = frame.rename(columns={key: value for key, value in aliases.items() if key in frame.columns}).copy()
    required = ["时间", "开盘", "收盘", "最高", "最低"]
    if any(column not in working.columns for column in required):
        return [], ""
    working["时间"] = pd.to_datetime(working["时间"], errors="coerce")
    for column in ["开盘", "收盘", "最高", "最低", "成交量"]:
        if column not in working.columns:
            working[column] = 0
        working[column] = pd.to_numeric(working[column], errors="coerce")
    working = working.dropna(subset=required).sort_values("时间")
    if working.empty:
        return [], ""
    data_date = working["时间"].iloc[-1].strftime("%Y-%m-%d")
    if period == "minute":
        working = working[working["时间"].dt.strftime("%Y-%m-%d") == data_date]
    time_format = "%Y-%m-%d %H:%M:%S" if period == "minute" else "%Y-%m-%d"
    points = [{
        "time": row["时间"].strftime(time_format),
        "open": round(float(row["开盘"]), 3),
        "close": round(float(row["收盘"]), 3),
        "high": round(float(row["最高"]), 3),
        "low": round(float(row["最低"]), 3),
        "volume": float(row["成交量"]) if pd.notna(row["成交量"]) else 0.0,
    } for _, row in working.iterrows()]
    return points, data_date


def normalize_tencent_minute_payload(payload: Mapping[str, Any], code: str) -> pd.DataFrame:
    """Convert Tencent's current-session minute payload to the common frame shape."""
    key = prefixed_code(code)
    node = ((payload.get("data") or {}).get(key) or {}).get("data") or {}
    date_text = str(node.get("date") or "")
    if len(date_text) != 8:
        return pd.DataFrame()
    date_label = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}"
    rows = []
    previous_volume = 0.0
    for raw in node.get("data") or []:
        parts = str(raw).split()
        if len(parts) < 3 or len(parts[0]) != 4:
            continue
        price = _safe_float(parts[1])
        cumulative_volume = _safe_float(parts[2])
        if price <= 0:
            continue
        rows.append({
            "时间": f"{date_label} {parts[0][:2]}:{parts[0][2:]}:00",
            "开盘": price,
            "收盘": price,
            "最高": price,
            "最低": price,
            "成交量": max(0.0, cumulative_volume - previous_volume),
        })
        previous_volume = cumulative_volume
    return pd.DataFrame(rows)


def _load_tencent_minute(code: str) -> pd.DataFrame:
    key = prefixed_code(code)
    response = requests.get(
        "https://web.ifzq.gtimg.cn/appstock/app/minute/query",
        params={"code": key},
        headers={"User-Agent": UA},
        timeout=12,
    )
    response.raise_for_status()
    return normalize_tencent_minute_payload(response.json(), code)


def _load_local_chart(code: str, period: ChartPeriod) -> pd.DataFrame:
    if period == "minute":
        query = text("""
            SELECT bar_time, open, close, high, low, volume
            FROM intraday_minute_bars
            WHERE code = :code
            ORDER BY bar_time DESC
            LIMIT 500
        """)
    else:
        query = text("""
            SELECT date, open, close, high, low, vol
            FROM daily_k
            WHERE code = :code
            ORDER BY date DESC
            LIMIT 180
        """)
    try:
        with get_db_engine().connect() as connection:
            return pd.read_sql(query, connection, params={"code": code})
    except Exception as exc:
        logger.warning(f"Hot stock local {period} chart unavailable for {code}: {exc}")
        return pd.DataFrame()


def _previous_close(code: str, data_date: str, fallback: float) -> float:
    if not data_date:
        return fallback
    try:
        with get_db_engine().connect() as connection:
            value = connection.execute(text("""
                SELECT close FROM daily_k
                WHERE code = :code AND date < :data_date
                ORDER BY date DESC LIMIT 1
            """), {"code": code, "data_date": data_date}).scalar()
        return round(_safe_float(value, fallback), 3)
    except Exception:
        return fallback


def get_hot_stock_chart(code: str, period: ChartPeriod = "minute", force: bool = False) -> Dict[str, Any]:
    """Return intraday area-chart or daily candlestick data without persisting external data."""
    code = str(code).strip()
    if not validate_stock_code(code):
        raise ValueError("股票代码必须为 6 位数字")
    cache_key = f"hot_stocks:chart:{period}:{code}"
    stale = get_stale_cache(cache_key)
    ttl = 60 if period == "minute" else 600
    if not force:
        cached = get_cached_data(cache_key, ttl)
        if cached:
            return {**cached, "cache_hit": True}

    source = "local_database"
    points: List[Dict[str, Any]] = []
    data_date = ""
    if period == "minute":
        source = "tencent_minute"
        try:
            frame = _load_tencent_minute(code)
            points, data_date = normalize_chart_frame(frame, period)
        except Exception as exc:
            logger.warning(f"Hot stock Tencent minute chart fetch failed for {code}: {exc}")
    else:
        frame = _load_local_chart(code, period)
        points, data_date = normalize_chart_frame(frame, period)
    if period == "minute" and not points:
        source = "local_database"
        frame = _load_local_chart(code, period)
        points, data_date = normalize_chart_frame(frame, period)
    if period == "minute" and not points:
        source = "eastmoney_minute"
        try:
            now = datetime.now()
            frame = ak.stock_zh_a_hist_min_em(
                symbol=code,
                start_date=(now - timedelta(days=7)).strftime("%Y-%m-%d 09:30:00"),
                end_date=(now + timedelta(days=1)).strftime("%Y-%m-%d 15:01:00"),
                period="1",
                adjust="",
            )
            points, data_date = normalize_chart_frame(frame, period)
        except Exception as exc:
            logger.warning(f"Hot stock minute chart fetch failed for {code}: {exc}")

    if not points and stale:
        return {**stale, "status": "degraded", "cache_hit": True, "degraded": True}

    fallback_close = points[0]["open"] if points else 0.0
    payload = {
        "status": "ok" if points else "degraded",
        "code": code,
        "period": period,
        "points": points,
        "data_date": data_date,
        "previous_close": _previous_close(code, data_date, fallback_close),
        "source": source,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "cache_hit": False,
        "degraded": not bool(points),
    }
    if points:
        set_cached_data(cache_key, payload)
    return payload
