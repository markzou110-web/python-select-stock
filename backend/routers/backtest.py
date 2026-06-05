from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from core.backtest_lab import run_single_stock_backtest
from core.db import get_db_engine, load_from_db, validate_stock_code
from core.indicators import calculate_indicators, calculate_pine_indicators

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.post("/single")
def run_single_backtest(payload: Dict[str, Any]):
    code = str(payload.get("code", "")).strip()
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code")

    engine = get_db_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="Database unavailable")

    strategy_type = payload.get("strategy_type") or "squeeze"
    if strategy_type not in {"squeeze", "pine", "consensus", "tv_zp"}:
        raise HTTPException(status_code=400, detail="Unsupported strategy_type")

    end_date: Optional[str] = payload.get("end_date")
    start_date: Optional[str] = payload.get("start_date")
    if not start_date:
        days = int(payload.get("days") or 720)
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    df = load_from_db(code, start_date, engine)
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data for this stock")

    if end_date:
        df = df[df["日期"].astype(str).str[:10] <= end_date]
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data in selected range")

    enable_pine = strategy_type in {"pine", "tv_zp"}
    df = calculate_indicators(df, enable_pine_indicators=enable_pine)
    if enable_pine and "RF_Upward" not in df.columns:
        df = calculate_pine_indicators(df)

    result = run_single_stock_backtest(
        df,
        strategy_type=strategy_type,
        params={
            "threshold": payload.get("threshold", 0.12),
            "vol_multiplier": payload.get("vol_multiplier", 1.5),
            "rsi_min": payload.get("rsi_min", 55),
            "pine_min_signals": payload.get("pine_min_signals", 3),
            "stop_loss_pct": payload.get("stop_loss_pct", -8.0),
            "max_hold_days": payload.get("max_hold_days", 10),
            "trailing_multiplier": payload.get("trailing_multiplier", 2.2),
            "time_stop_days": payload.get("time_stop_days"),
            "capital": payload.get("capital", 100000),
            "entry_mode": payload.get("entry_mode", "signal_close"),
            "max_open_gap_pct": payload.get("max_open_gap_pct", 3.0),
            "limit_up_gap_pct": payload.get("limit_up_gap_pct", 9.5),
            "slippage_bps": payload.get("slippage_bps", 5.0),
            "position_pct": payload.get("position_pct", 1.0),
            "lot_size": payload.get("lot_size", 100),
            "skip_adjustment_gaps": payload.get("skip_adjustment_gaps", True),
            "adjustment_gap_pct": payload.get("adjustment_gap_pct", 20.0),
        },
    )
    result["meta"] = {
        "code": code,
        "strategy_type": strategy_type,
        "start_date": start_date,
        "end_date": end_date,
        "data_points": int(len(df)),
    }
    return result
