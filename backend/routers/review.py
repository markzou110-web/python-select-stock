from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from typing import Dict, Any, List
import io
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


def _load_scan_performance_df(days: int) -> pd.DataFrame:
    engine = get_db_engine()
    if not engine:
        return pd.DataFrame()

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
        return df
    for horizon in [1, 3, 5, 10, 20]:
        df[f"ret_{horizon}d"] = (df[f"close_{horizon}d"] - df["price"]) / df["price"] * 100
    return df


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
        df = _load_scan_performance_df(days)
        if df.empty:
            return _empty_response()

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


@router.get("/scan-performance/export")
def export_scan_performance(days: int = 120):
    df = _load_scan_performance_df(days)
    if df.empty:
        df = pd.DataFrame(columns=[
            "code", "name", "industry", "strategy_type", "signal_date", "price",
            "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
        ])

    export_cols = [
        "code", "name", "industry", "strategy_type", "signal_date", "price",
        "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
    ]
    export_df = df[[c for c in export_cols if c in df.columns]].copy()
    rename_map = {
        "code": "代码",
        "name": "名称",
        "industry": "行业",
        "strategy_type": "策略",
        "signal_date": "信号日期",
        "price": "信号价",
        "ret_1d": "1日收益%",
        "ret_3d": "3日收益%",
        "ret_5d": "5日收益%",
        "ret_10d": "10日收益%",
        "ret_20d": "20日收益%",
    }
    export_df = export_df.rename(columns=rename_map)
    for col in [c for c in export_df.columns if c.endswith("%")]:
        export_df[col] = export_df[col].round(2)

    buf = io.StringIO()
    buf.write("\ufeff")
    export_df.to_csv(buf, index=False)
    buf.seek(0)
    filename = f"alpha_vision_review_{days}d.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
