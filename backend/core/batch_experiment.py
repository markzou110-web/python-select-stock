from datetime import datetime, timedelta
from typing import Any, Dict, List

from core.backtest_lab import run_single_stock_backtest
from core.db import load_from_db
from core.indicators import calculate_indicators, calculate_pine_indicators


def run_batch_experiment(engine, codes: List[str], strategy_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    days = min(max(int(payload.get("days") or 720), 120), 1500)
    start_date = payload.get("start_date") or (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = payload.get("end_date")
    params = {
        "threshold": payload.get("threshold", 0.12),
        "vol_multiplier": payload.get("vol_multiplier", 1.5),
        "rsi_min": payload.get("rsi_min", 55),
        "pine_min_signals": payload.get("pine_min_signals", 3),
        "stop_loss_pct": payload.get("stop_loss_pct", -8.0),
        "max_hold_days": payload.get("max_hold_days", 10),
        "entry_mode": payload.get("entry_mode", "next_open_confirm"),
        "max_open_gap_pct": payload.get("max_open_gap_pct", 3.0),
        "slippage_bps": payload.get("slippage_bps", 5.0),
        "position_pct": payload.get("position_pct", 1.0),
    }
    rows = []
    for code in codes:
        df = load_from_db(code, start_date, engine)
        if end_date and not df.empty:
            df = df[df["日期"].astype(str).str[:10] <= end_date]
        if df.empty:
            rows.append({"code": code, "status": "NO_DATA", "signal_count": 0})
            continue
        enable_pine = strategy_type in {"pine", "tv_zp"}
        df = calculate_indicators(df, enable_pine_indicators=enable_pine)
        if enable_pine and "RF_Upward" not in df.columns:
            df = calculate_pine_indicators(df)
        result = run_single_stock_backtest(df, strategy_type=strategy_type, params=params)
        summary = result["summary"]
        rows.append({
            "code": code,
            "status": "OK",
            "signal_count": summary["signal_count"],
            "win_rate": summary["win_rate"],
            "avg_return": summary["avg_return"],
            "total_return": summary["total_return"],
            "max_drawdown": summary["max_drawdown"],
            "profit_factor": summary["profit_factor"],
            "skipped_adjustment_gap": summary.get("skipped_adjustment_gap", 0),
        })
    effective = [row for row in rows if row.get("status") == "OK" and row.get("signal_count", 0) > 0]
    return {
        "meta": {"strategy_type": strategy_type, "start_date": start_date, "end_date": end_date, "requested": len(codes)},
        "summary": {
            "tested": len(rows),
            "effective": len(effective),
            "avg_win_rate": round(sum(row["win_rate"] for row in effective) / len(effective), 2) if effective else 0,
            "avg_total_return": round(sum(row["total_return"] for row in effective) / len(effective), 2) if effective else 0,
            "positive_count": sum(1 for row in effective if row["total_return"] > 0),
        },
        "items": sorted(rows, key=lambda row: row.get("total_return", -999), reverse=True),
    }
