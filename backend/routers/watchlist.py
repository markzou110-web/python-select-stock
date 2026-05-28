from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from typing import Dict, Any
from datetime import datetime
import pandas as pd

from core.db import get_db_engine, validate_stock_code
from core.logging_config import logger
from core.risk_constants import FIXED_STOP_LOSS_RATIO, TAKE_PROFIT_RATIO

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


def _latest_prices(engine, codes):
    if not codes:
        return {}
    placeholders = ",".join([f":code_{i}" for i in range(len(codes))])
    params = {f"code_{i}": c for i, c in enumerate(codes)}
    try:
        df = pd.read_sql(text(f"""
            SELECT DISTINCT ON (code) code, close AS latest_price, date AS latest_date
            FROM daily_k
            WHERE code IN ({placeholders})
            ORDER BY code, date DESC
        """), engine, params=params)
        return {
            row["code"]: {"price": float(row["latest_price"]), "date": str(row["latest_date"])}
            for _, row in df.iterrows()
        }
    except Exception as exc:
        logger.warning(f"Watchlist latest price fetch failed: {exc}")
        return {}


@router.get("/list")
def list_watchlist(status: str = "WATCHING") -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return {"items": [], "stats": {}}

    try:
        if status == "ALL":
            df = pd.read_sql(text("SELECT * FROM watchlist ORDER BY updated_at DESC, created_at DESC"), engine)
        else:
            df = pd.read_sql(
                text("SELECT * FROM watchlist WHERE status = :status ORDER BY updated_at DESC, created_at DESC"),
                engine,
                params={"status": status},
            )
        if df.empty:
            return {"items": [], "stats": {"total": 0, "triggered": 0, "avg_pl_pct": 0}}

        price_map = _latest_prices(engine, df["code"].unique().tolist())
        items = []
        triggered = 0
        pl_values = []
        for _, row in df.iterrows():
            latest = price_map.get(row["code"], {})
            current_price = latest.get("price", float(row["watch_price"] or 0))
            watch_price = float(row["watch_price"] or current_price or 0)
            pl_pct = ((current_price - watch_price) / watch_price * 100) if watch_price > 0 else 0
            target_price = row.get("target_price")
            stop_price = row.get("stop_price")
            target_hit = target_price is not None and current_price >= float(target_price)
            stop_hit = stop_price is not None and current_price <= float(stop_price)
            if target_hit or stop_hit:
                triggered += 1
            pl_values.append(pl_pct)
            items.append({
                "id": int(row["id"]),
                "code": row["code"],
                "name": row["name"],
                "industry": row.get("industry") or "未知",
                "source": row.get("source") or "manual",
                "strategy_type": row.get("strategy_type") or "squeeze",
                "watch_price": round(watch_price, 2),
                "current_price": round(current_price, 2),
                "pl_pct": round(pl_pct, 2),
                "target_price": round(float(target_price), 2) if target_price is not None else None,
                "stop_price": round(float(stop_price), 2) if stop_price is not None else None,
                "target_hit": target_hit,
                "stop_hit": stop_hit,
                "status": row.get("status") or "WATCHING",
                "reason": row.get("reason") or "",
                "invalidation": row.get("invalidation") or "",
                "created_at": row["created_at"].isoformat() if row.get("created_at") is not None else "",
                "latest_date": latest.get("date"),
            })

        return {
            "items": items,
            "stats": {
                "total": len(items),
                "triggered": triggered,
                "avg_pl_pct": round(sum(pl_values) / len(pl_values), 2) if pl_values else 0,
            },
        }
    except Exception as exc:
        logger.error(f"List watchlist error: {exc}")
        return {"items": [], "stats": {}}


@router.post("/add")
def add_watchlist_item(data: Dict[str, Any]) -> Dict[str, Any]:
    code = str(data.get("code", "")).strip()
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code")

    price = float(data.get("watch_price") or data.get("price") or 0)
    if price <= 0:
        raise HTTPException(status_code=400, detail="watch_price must be positive")

    engine = get_db_engine()
    if not engine:
        return {"status": "error", "detail": "Database unavailable"}

    target_price = data.get("target_price")
    stop_price = data.get("stop_price")
    if target_price is None:
        target_price = round(price * TAKE_PROFIT_RATIO, 2)
    if stop_price is None:
        stop_price = round(price * FIXED_STOP_LOSS_RATIO, 2)

    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO watchlist (
                    code, name, industry, source, strategy_type, watch_price,
                    target_price, stop_price, status, reason, invalidation, created_at, updated_at
                ) VALUES (
                    :code, :name, :industry, :source, :strategy_type, :watch_price,
                    :target_price, :stop_price, 'WATCHING', :reason, :invalidation, :created_at, :updated_at
                )
            """), {
                "code": code,
                "name": data.get("name") or code,
                "industry": data.get("industry") or "未知",
                "source": data.get("source") or "scan",
                "strategy_type": data.get("strategy_type") or "squeeze",
                "watch_price": price,
                "target_price": float(target_price) if target_price is not None else None,
                "stop_price": float(stop_price) if stop_price is not None else None,
                "reason": data.get("reason") or "",
                "invalidation": data.get("invalidation") or "",
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            })
            conn.commit()
        return {"status": "success"}
    except Exception as exc:
        logger.error(f"Add watchlist error: {exc}")
        return {"status": "error", "detail": str(exc)}


@router.post("/archive/{item_id}")
def archive_watchlist_item(item_id: int) -> Dict[str, str]:
    return _set_status(item_id, "ARCHIVED")


@router.post("/activate/{item_id}")
def activate_watchlist_item(item_id: int) -> Dict[str, str]:
    return _set_status(item_id, "WATCHING")


@router.delete("/remove/{item_id}")
def remove_watchlist_item(item_id: int) -> Dict[str, str]:
    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM watchlist WHERE id = :id"), {"id": item_id})
            conn.commit()
        return {"status": "success"}
    except Exception as exc:
        logger.error(f"Remove watchlist error: {exc}")
        return {"status": "error"}


def _set_status(item_id: int, status: str) -> Dict[str, str]:
    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE watchlist SET status = :status, updated_at = :updated_at WHERE id = :id"),
                {"status": status, "updated_at": datetime.now(), "id": item_id},
            )
            conn.commit()
        return {"status": "success"}
    except Exception as exc:
        logger.error(f"Set watchlist status error: {exc}")
        return {"status": "error"}
