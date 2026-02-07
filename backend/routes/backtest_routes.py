from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from core.backtest import BacktestEngine
from core.db import get_db_engine

router = APIRouter()

class BacktestRequest(BaseModel):
    code: str
    start_date: str # YYYY-MM-DD
    end_date: str   # YYYY-MM-DD
    strategy_params: dict

@router.post("/api/backtest/run")
def run_backtest(req: BacktestRequest):
    try:
        engine = get_db_engine()
        bt = BacktestEngine(engine)
        
        results = bt.run(
            req.code, 
            req.start_date, 
            req.end_date, 
            req.strategy_params
        )
        
        if "error" in results:
            raise HTTPException(status_code=400, detail=results["error"])
            
        return results
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
