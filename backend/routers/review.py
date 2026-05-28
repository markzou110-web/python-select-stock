from fastapi import APIRouter
from sqlalchemy import text
from typing import Dict, Any, List
import pandas as pd

from core.db import get_db_engine
from core.logging_config import logger

router = APIRouter(prefix="/api/review", tags=["review"])


def _empty_response() -> Dict[str, Any]:
    return {
        "summary": {
            "signals": 0,
            "win_rate_5d": 0,
            "avg_return_5d": 0,
            "best_bucket": "暂无",
            "worst_bucket": "暂无",
        },
        "horizons": [],
        "by_strategy": [],
        "by_industry": [],
        "recent_dates": [],
    }


@router.get("/scan-performance")
def get_scan_performance(days: int = 120) -> Dict[str, Any]:
    """
    Review historical scan signals by joining scan_history with future daily_k prices.
    A win is defined as a positive future return at the requested horizon.
    """
    engine = get_db_engine()
    if not engine:
        return _empty_response()

    try:
        query = text("""
            WITH signals AS (
                SELECT code, name, industry, strategy_type, date AS signal_date, price
                FROM scan_history
                WHERE date >= CURRENT_DATE - (:days || ' days')::interval
                  AND price IS NOT NULL
                  AND price > 0
            ),
            future AS (
                SELECT
                    s.code,
                    s.name,
                    s.industry,
                    COALESCE(s.strategy_type, 'squeeze') AS strategy_type,
                    s.signal_date,
                    s.price,
                    h1.close AS close_1d,
                    h3.close AS close_3d,
                    h5.close AS close_5d,
                    h10.close AS close_10d,
                    h20.close AS close_20d
                FROM signals s
                LEFT JOIN LATERAL (
                    SELECT close FROM daily_k d WHERE d.code = s.code AND d.date > s.signal_date AND d.date <= s.signal_date + INTERVAL '1 day' ORDER BY d.date DESC LIMIT 1
                ) h1 ON true
                LEFT JOIN LATERAL (
                    SELECT close FROM daily_k d WHERE d.code = s.code AND d.date > s.signal_date AND d.date <= s.signal_date + INTERVAL '3 day' ORDER BY d.date DESC LIMIT 1
                ) h3 ON true
                LEFT JOIN LATERAL (
                    SELECT close FROM daily_k d WHERE d.code = s.code AND d.date > s.signal_date AND d.date <= s.signal_date + INTERVAL '5 day' ORDER BY d.date DESC LIMIT 1
                ) h5 ON true
                LEFT JOIN LATERAL (
                    SELECT close FROM daily_k d WHERE d.code = s.code AND d.date > s.signal_date AND d.date <= s.signal_date + INTERVAL '10 day' ORDER BY d.date DESC LIMIT 1
                ) h10 ON true
                LEFT JOIN LATERAL (
                    SELECT close FROM daily_k d WHERE d.code = s.code AND d.date > s.signal_date AND d.date <= s.signal_date + INTERVAL '20 day' ORDER BY d.date DESC LIMIT 1
                ) h20 ON true
            )
            SELECT * FROM future
            ORDER BY signal_date DESC
        """)
        df = pd.read_sql(query, engine, params={"days": int(days)})
        if df.empty:
            return _empty_response()

        for horizon in [1, 3, 5, 10, 20]:
            col = f"close_{horizon}d"
            ret_col = f"ret_{horizon}d"
            df[ret_col] = (df[col] - df["price"]) / df["price"] * 100

        def metric_frame(grouped: pd.DataFrame, key: str) -> List[Dict[str, Any]]:
            rows = []
            for value, group in grouped:
                returns = group["ret_5d"].dropna()
                if returns.empty:
                    continue
                rows.append({
                    key: value or "未知",
                    "signals": int(len(returns)),
                    "win_rate": round(float((returns > 0).mean() * 100), 1),
                    "avg_return": round(float(returns.mean()), 2),
                    "best_return": round(float(returns.max()), 2),
                    "worst_return": round(float(returns.min()), 2),
                })
            rows.sort(key=lambda r: (r["win_rate"], r["avg_return"], r["signals"]), reverse=True)
            return rows[:12]

        horizons = []
        for horizon in [1, 3, 5, 10, 20]:
            returns = df[f"ret_{horizon}d"].dropna()
            if returns.empty:
                horizons.append({"horizon": f"{horizon}日", "signals": 0, "win_rate": 0, "avg_return": 0})
            else:
                horizons.append({
                    "horizon": f"{horizon}日",
                    "signals": int(len(returns)),
                    "win_rate": round(float((returns > 0).mean() * 100), 1),
                    "avg_return": round(float(returns.mean()), 2),
                })

        by_strategy = metric_frame(df.groupby("strategy_type", dropna=False), "strategy")
        by_industry = metric_frame(df.groupby("industry", dropna=False), "industry")

        recent = []
        for date_value, group in df.groupby("signal_date"):
            returns = group["ret_5d"].dropna()
            if returns.empty:
                continue
            recent.append({
                "date": str(date_value),
                "signals": int(len(returns)),
                "win_rate": round(float((returns > 0).mean() * 100), 1),
                "avg_return": round(float(returns.mean()), 2),
            })
        recent.sort(key=lambda r: r["date"], reverse=True)

        ret_5d = df["ret_5d"].dropna()
        best_bucket = by_industry[0]["industry"] if by_industry else "暂无"
        worst_bucket = by_industry[-1]["industry"] if by_industry else "暂无"
        return {
            "summary": {
                "signals": int(len(ret_5d)),
                "win_rate_5d": round(float((ret_5d > 0).mean() * 100), 1) if not ret_5d.empty else 0,
                "avg_return_5d": round(float(ret_5d.mean()), 2) if not ret_5d.empty else 0,
                "best_bucket": best_bucket,
                "worst_bucket": worst_bucket,
            },
            "horizons": horizons,
            "by_strategy": by_strategy,
            "by_industry": by_industry,
            "recent_dates": recent[:20],
        }
    except Exception as exc:
        logger.error(f"Review performance error: {exc}")
        return _empty_response()
