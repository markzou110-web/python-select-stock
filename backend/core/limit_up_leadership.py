from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import akshare as ak
import pandas as pd
from sqlalchemy import text

from core.db import get_db_engine
from core.logging_config import logger
from core.config import config

config.setup_no_proxy()


def _value(row: pd.Series, key: str, default: Any = None) -> Any:
    value = row.get(key, default)
    return default if pd.isna(value) else value


def _time_value(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[-6:].zfill(6) if digits else None


def _pool_rows(pool: Optional[pd.DataFrame], status: str, event_date: str, collected_at: datetime) -> List[Dict[str, Any]]:
    if pool is None or pool.empty:
        return []
    rows = []
    for _, row in pool.iterrows():
        code = str(_value(row, "代码", "")).zfill(6)
        if not code:
            continue
        rows.append({
            "event_date": event_date,
            "code": code,
            "name": str(_value(row, "名称", code)),
            "industry": str(_value(row, "所属行业", "未知")),
            "status": status,
            "first_limit_time": _time_value(_value(row, "首次封板时间")),
            "last_limit_time": _time_value(_value(row, "最后封板时间")),
            "break_count": int(_value(row, "炸板次数", 0) or 0),
            "limit_up_streak": int(_value(row, "连板数", 0) or 0),
            "seal_amount": float(_value(row, "封板资金", 0) or 0),
            "turnover": float(_value(row, "换手率", 0) or 0),
            "amount": float(_value(row, "成交额", 0) or 0),
            "first_seen_at": collected_at,
            "last_seen_at": collected_at,
        })
    return rows


def save_limit_up_events(rows: Iterable[Dict[str, Any]], engine=None) -> int:
    rows = list(rows)
    if not rows:
        return 0
    engine = engine or get_db_engine()
    if not engine:
        return 0
    date_expr = ":event_date" if engine.dialect.name == "sqlite" else "CAST(:event_date AS DATE)"
    query = text(f"""
        INSERT INTO limit_up_events (
            event_date, code, name, industry, status, first_limit_time, last_limit_time,
            break_count, limit_up_streak, seal_amount, turnover, amount, first_seen_at, last_seen_at
        ) VALUES (
            {date_expr}, :code, :name, :industry, :status, :first_limit_time, :last_limit_time,
            :break_count, :limit_up_streak, :seal_amount, :turnover, :amount, :first_seen_at, :last_seen_at
        )
        ON CONFLICT (event_date, code) DO UPDATE SET
            name = excluded.name,
            industry = excluded.industry,
            status = excluded.status,
            first_limit_time = COALESCE(limit_up_events.first_limit_time, excluded.first_limit_time),
            last_limit_time = COALESCE(excluded.last_limit_time, limit_up_events.last_limit_time),
            break_count = excluded.break_count,
            limit_up_streak = CASE
                WHEN excluded.limit_up_streak > limit_up_events.limit_up_streak THEN excluded.limit_up_streak
                ELSE limit_up_events.limit_up_streak
            END,
            seal_amount = excluded.seal_amount,
            turnover = excluded.turnover,
            amount = excluded.amount,
            last_seen_at = excluded.last_seen_at
    """)
    with engine.connect() as conn:
        conn.execute(query, rows)
        conn.commit()
    return len(rows)


def collect_limit_up_events(event_date: Optional[str] = None, engine=None, collected_at: Optional[datetime] = None) -> Dict[str, int]:
    collected_at = collected_at or datetime.now()
    event_date = event_date or collected_at.strftime("%Y%m%d")
    compact_date = event_date.replace("-", "")
    db_date = f"{compact_date[:4]}-{compact_date[4:6]}-{compact_date[6:]}"

    pools: Dict[str, Optional[pd.DataFrame]] = {"BROKEN": None, "SEALED": None}
    errors = 0
    for status, fetcher in (("BROKEN", ak.stock_zt_pool_zbgc_em), ("SEALED", ak.stock_zt_pool_em)):
        try:
            pools[status] = fetcher(date=compact_date)
        except Exception as exc:
            errors += 1
            logger.warning(f"{status} pool unavailable for {compact_date}: {exc}")

    rows_by_code: Dict[str, Dict[str, Any]] = {}
    for status in ("BROKEN", "SEALED"):
        for row in _pool_rows(pools[status], status, db_date, collected_at):
            rows_by_code[row["code"]] = row
    saved = save_limit_up_events(rows_by_code.values(), engine=engine)
    return {
        "sealed": 0 if pools["SEALED"] is None else len(pools["SEALED"]),
        "broken": 0 if pools["BROKEN"] is None else len(pools["BROKEN"]),
        "saved": saved,
        "errors": errors,
    }


def load_limit_up_event_map(event_date: str, engine=None) -> Dict[str, Dict[str, Any]]:
    engine = engine or get_db_engine()
    if not engine:
        return {}
    try:
        frame = pd.read_sql(
            text("SELECT * FROM limit_up_events WHERE event_date = :event_date"),
            engine,
            params={"event_date": event_date},
        )
    except Exception as exc:
        logger.warning(f"Unable to load limit-up leadership events: {exc}")
        return {}
    if frame.empty:
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for industry, group in frame.groupby("industry", dropna=False):
        ordered = group.sort_values(["first_limit_time", "status", "break_count"], na_position="last")
        for rank, (_, row) in enumerate(ordered.iterrows(), start=1):
            item = row.to_dict()
            item["limit_up_sector_rank"] = rank
            result[str(row["code"]).zfill(6)] = item
    return result


def apply_limit_up_features(stocks: List[Dict[str, Any]], event_map: Dict[str, Dict[str, Any]]) -> None:
    for stock in stocks:
        event = event_map.get(str(stock.get("代码") or "").zfill(6))
        if not event:
            continue
        for key in (
            "status", "first_limit_time", "last_limit_time", "break_count",
            "limit_up_streak", "seal_amount", "limit_up_sector_rank",
        ):
            target = "limit_up_status" if key == "status" else key
            stock[target] = event.get(key)


def _candidate_codes(event_date: str, engine, limit: int) -> List[str]:
    codes: List[str] = []
    with engine.connect() as conn:
        for table_name, order_by in (
            ("scan_history", "score DESC"),
            ("limit_up_events", "first_limit_time ASC"),
        ):
            rows = conn.execute(
                text(f"SELECT code FROM {table_name} WHERE {'date' if table_name == 'scan_history' else 'event_date'} = :event_date ORDER BY {order_by} LIMIT :limit"),
                {"event_date": event_date, "limit": limit},
            ).fetchall()
            for row in rows:
                code = str(row[0]).zfill(6)
                if code not in codes:
                    codes.append(code)
                if len(codes) >= limit:
                    return codes
    return codes


def save_minute_bars(code: str, frame: pd.DataFrame, engine=None, collected_at: Optional[datetime] = None) -> int:
    if frame is None or frame.empty:
        return 0
    engine = engine or get_db_engine()
    if not engine:
        return 0
    collected_at = collected_at or datetime.now()
    rows = []
    for _, row in frame.iterrows():
        rows.append({
            "code": str(code).zfill(6),
            "bar_time": str(_value(row, "时间", ""))[:19],
            "open": float(_value(row, "开盘", 0) or 0),
            "close": float(_value(row, "收盘", 0) or 0),
            "high": float(_value(row, "最高", 0) or 0),
            "low": float(_value(row, "最低", 0) or 0),
            "volume": float(_value(row, "成交量", 0) or 0),
            "amount": float(_value(row, "成交额", 0) or 0),
            "average_price": float(_value(row, "均价", 0) or 0),
            "created_at": collected_at,
        })
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO intraday_minute_bars (
                code, bar_time, open, close, high, low, volume, amount, average_price, created_at
            ) VALUES (
                :code, :bar_time, :open, :close, :high, :low, :volume, :amount, :average_price, :created_at
            )
            ON CONFLICT (code, bar_time) DO UPDATE SET
                open = excluded.open,
                close = excluded.close,
                high = excluded.high,
                low = excluded.low,
                volume = excluded.volume,
                amount = excluded.amount,
                average_price = excluded.average_price,
                created_at = excluded.created_at
        """), rows)
        conn.commit()
    return len(rows)


def _fetch_minute_bars(code: str, event_date: str, retries: int = 1) -> pd.DataFrame:
    last_exc: Optional[Exception] = None
    for _ in range(max(1, retries + 1)):
        try:
            return ak.stock_zh_a_hist_min_em(
                symbol=code,
                period="1",
                start_date=f"{event_date} 09:25:00",
                end_date=f"{event_date} 15:05:00",
                adjust="",
            )
        except Exception as exc:
            last_exc = exc
            config.setup_no_proxy()
    raise last_exc or RuntimeError("minute bars unavailable")


def _snapshot_minute_frame(codes: List[str], event_date: str) -> pd.DataFrame:
    if event_date != datetime.now().strftime("%Y-%m-%d"):
        return pd.DataFrame()
    from core.data import get_market_snapshot, is_snapshot_stale

    snapshot = get_market_snapshot()
    if snapshot is None or snapshot.empty or is_snapshot_stale(snapshot):
        return pd.DataFrame()

    frame = snapshot[snapshot["code"].isin(codes)].copy()
    if frame.empty:
        return pd.DataFrame()

    fetched_at = snapshot.attrs.get("fetched_at") or datetime.now()
    try:
        bar_time = pd.Timestamp(fetched_at).floor("min").strftime("%Y-%m-%d %H:%M:00")
    except Exception:
        bar_time = datetime.now().strftime("%Y-%m-%d %H:%M:00")

    def numeric_series(column: str, fallback: str = "price") -> pd.Series:
        source = frame[column] if column in frame.columns else frame.get(fallback, pd.Series([0] * len(frame), index=frame.index))
        return pd.to_numeric(source, errors="coerce").fillna(0)

    return pd.DataFrame({
        "代码": frame["code"].astype(str).str.zfill(6),
        "时间": bar_time,
        "开盘": numeric_series("open"),
        "收盘": numeric_series("price"),
        "最高": numeric_series("high"),
        "最低": numeric_series("low"),
        "成交量": numeric_series("vol"),
        "成交额": numeric_series("amount"),
        "均价": numeric_series("price"),
    })


def collect_candidate_minute_bars(event_date: Optional[str] = None, engine=None, max_codes: int = 30) -> Dict[str, int]:
    engine = engine or get_db_engine()
    if not engine:
        return {"codes": 0, "bars": 0, "errors": 1}
    event_date = event_date or datetime.now().strftime("%Y-%m-%d")
    codes = _candidate_codes(event_date, engine, max_codes)
    bars = 0
    snapshot_bars = 0
    eastmoney_bars = 0
    errors = 0
    empty = 0
    snapshot_frame = _snapshot_minute_frame(codes, event_date)
    if not snapshot_frame.empty:
        for code, group in snapshot_frame.groupby("代码"):
            saved = save_minute_bars(str(code), group, engine=engine)
            snapshot_bars += saved
            bars += saved
        return {
            "codes": len(codes),
            "bars": bars,
            "snapshot_bars": snapshot_bars,
            "eastmoney_bars": eastmoney_bars,
            "errors": errors,
            "empty": empty,
            "source_paused": 0,
        }

    for code in codes:
        try:
            frame = _fetch_minute_bars(code, event_date, retries=1)
            saved = save_minute_bars(code, frame, engine=engine)
            if saved == 0:
                empty += 1
            eastmoney_bars += saved
            bars += saved
        except Exception as exc:
            errors += 1
            logger.debug(f"Minute bars unavailable for {code}: {exc}")
    return {
        "codes": len(codes),
        "bars": bars,
        "snapshot_bars": snapshot_bars,
        "eastmoney_bars": eastmoney_bars,
        "errors": errors,
        "empty": empty,
        "source_paused": 0,
    }
