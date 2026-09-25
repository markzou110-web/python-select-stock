"""Evaluate whether AI review separates stronger candidates from its input universe."""
from datetime import date, timedelta
from typing import Any, Dict

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from core.logging_config import logger
from core.performance_metrics import return_metrics


HORIZONS = (1, 3, 5, 10)


def _empty_report(days: int) -> Dict[str, Any]:
    return {
        "status": "none",
        "days": days,
        "price_basis": "next_trading_day_open_to_horizon_close",
        "comparison_scope": "same_ai_reviewed_universe",
        "horizons": {},
        "message": "暂无已成熟的AI复核样本",
    }


def build_ai_review_performance(engine: Engine, days: int = 180) -> Dict[str, Any]:
    """Compare AI actions using only closes available after each review date."""
    days = max(1, min(int(days), 3650))
    cutoff = date.today() - timedelta(days=days)
    try:
        reviews = pd.read_sql(text("""
            SELECT id, review_date, code, action, created_at
            FROM ai_candidate_reviews
            WHERE review_date >= :cutoff
        """), engine, params={"cutoff": cutoff.isoformat()})
        if reviews.empty:
            return _empty_report(days)
        prices = pd.read_sql(text("""
            SELECT code, date, open, close
            FROM daily_k
            WHERE date >= :cutoff
            ORDER BY code, date
        """), engine, params={"cutoff": cutoff})
    except Exception as exc:
        logger.warning(f"AI review performance unavailable: {exc}")
        return _empty_report(days)
    if prices.empty:
        return _empty_report(days)

    reviews["review_date"] = pd.to_datetime(reviews["review_date"], errors="coerce").dt.date
    reviews["created_at"] = pd.to_datetime(reviews["created_at"], errors="coerce")
    reviews["code"] = reviews["code"].astype(str).str.zfill(6)
    reviews["action"] = reviews["action"].fillna("WAIT").astype(str).str.upper()
    reviews = (
        reviews.dropna(subset=["review_date"])
        .sort_values(["created_at", "id"])
        .drop_duplicates(["review_date", "code"], keep="last")
    )

    prices["date"] = pd.to_datetime(prices["date"], errors="coerce").dt.date
    prices["code"] = prices["code"].astype(str).str.zfill(6)
    prices["open"] = pd.to_numeric(prices["open"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    price_groups = {
        code: group.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        for code, group in prices.groupby("code")
    }

    outcomes = []
    for row in reviews.itertuples(index=False):
        bars = price_groups.get(row.code)
        if bars is None or bars.empty:
            continue
        signal_rows = bars.index[bars["date"] == row.review_date].tolist()
        if not signal_rows:
            continue
        entry_index = signal_rows[0] + 1
        if entry_index >= len(bars):
            continue
        entry_open = bars.loc[entry_index, "open"]
        if pd.isna(entry_open) or float(entry_open) <= 0:
            continue
        entry_open = float(entry_open)
        outcome = {"action": row.action}
        for horizon in HORIZONS:
            future_index = entry_index + horizon - 1
            future_close = (
                float(bars.loc[future_index, "close"])
                if future_index < len(bars) and pd.notna(bars.loc[future_index, "close"]) else None
            )
            outcome[f"ret_{horizon}d"] = (
                round((future_close / entry_open - 1) * 100, 6)
                if future_close is not None else None
            )
        outcomes.append(outcome)
    frame = pd.DataFrame(outcomes)
    if frame.empty or not any(frame[f"ret_{h}d"].notna().any() for h in HORIZONS):
        return _empty_report(days)

    horizon_reports: Dict[str, Any] = {}
    for horizon in HORIZONS:
        column = f"ret_{horizon}d"
        all_metrics = return_metrics(frame[column])
        action_metrics = {
            action.lower(): return_metrics(frame.loc[frame["action"] == action, column])
            for action in ("BUY", "WAIT", "AVOID")
        }
        buy_metrics = action_metrics["buy"]
        horizon_reports[f"{horizon}d"] = {
            "all_reviewed": all_metrics,
            **action_metrics,
            "buy_lift": {
                "win_rate_pct_points": round(
                    buy_metrics["win_rate"] - all_metrics["win_rate"], 1
                ) if buy_metrics["signals"] else None,
                "avg_return_pct_points": round(
                    buy_metrics["avg_return"] - all_metrics["avg_return"], 2
                ) if buy_metrics["signals"] else None,
            },
        }

    mature_buy_5d = horizon_reports["5d"]["buy"]["signals"]
    return {
        "status": "available",
        "days": days,
        "price_basis": "next_trading_day_open_to_horizon_close",
        "comparison_scope": "same_ai_reviewed_universe",
        "horizons": horizon_reports,
        "sample_warning": (
            "AI BUY的5日成熟样本少于20，仅供观察，不能据此认定提高胜率"
            if mature_buy_5d < 20 else None
        ),
    }
