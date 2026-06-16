from typing import Any, Dict

import pandas as pd
from sqlalchemy import text

from core.performance_metrics import return_metrics
from core.pro_workflow import classify_strategy_health


def build_strategy_health(engine, days: int = 120) -> Dict[str, Any]:
    """Evaluate recent verified 5-day returns for automatic strategy controls."""
    if engine is None:
        return {"status": "error", "strategies": {}}
    try:
        df = pd.read_sql(text("""
            SELECT s.strategy_type, (future.close - s.price) / s.price * 100 AS ret_5d
            FROM scan_history s
            JOIN LATERAL (
                SELECT close
                FROM daily_k d
                WHERE d.code = s.code AND d.date > s.date
                ORDER BY d.date ASC
                OFFSET 4 LIMIT 1
            ) future ON true
            WHERE s.date >= CURRENT_DATE - (:days || ' days')::interval
              AND s.price > 0
              AND COALESCE((s.price_action_detail->>'research_eligible')::boolean, false) = true
        """), engine, params={"days": int(days)})
    except Exception as exc:
        return {"status": "error", "strategies": {}, "error": str(exc)}

    strategies = {}
    for strategy, group in df.groupby("strategy_type", dropna=False):
        metrics = return_metrics(group["ret_5d"])
        strategies[str(strategy or "unknown")] = {**metrics, **classify_strategy_health(metrics)}
    return {"status": "ok", "strategies": strategies}


def apply_strategy_health_controls(results: list[dict], health: Dict[str, Any]) -> None:
    strategies = health.get("strategies") or {}
    for row in results:
        strategy = str(row.get("strategy_type") or "unknown")
        strategy_health = strategies.get(strategy)
        if not strategy_health:
            continue
        row["strategy_health"] = strategy_health
        status = strategy_health.get("status")
        if status == "PAUSED":
            row["trade_eligible"] = False
            row["trade_bucket"] = "OBSERVE"
            blockers = list(row.get("trade_blockers") or [])
            blockers.append(f"策略近期负期望，自动暂停：{strategy_health.get('reason')}")
            row["trade_blockers"] = list(dict.fromkeys(blockers))
        elif status == "DOWNWEIGHT":
            row["calibrated_score"] = round(max(0, float(row.get("calibrated_score") or 0) - 10), 1)
            row["Score"] = row["calibrated_score"]
