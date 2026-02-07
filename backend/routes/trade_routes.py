from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from datetime import datetime
from core.db import get_db_engine
from models.api_models import PaperTradeCreate

router = APIRouter(prefix="/api/trade", tags=["trade"])

@router.post("/add")
def add_paper_trade(trade: PaperTradeCreate):
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO paper_trades (code, name, price, date)
                VALUES (:code, :name, :price, :date)
            """), {
                "code": trade.code,
                "name": trade.name,
                "price": trade.price,
                "date": datetime.now().strftime("%Y-%m-%d")
            })
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/list")
def list_paper_trades():
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT * FROM paper_trades ORDER BY id DESC"))
            trades = [dict(row) for row in result]
        return trades
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{trade_id}")
def remove_paper_trade(trade_id: int):
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM paper_trades WHERE id = :id"), {"id": trade_id})
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
