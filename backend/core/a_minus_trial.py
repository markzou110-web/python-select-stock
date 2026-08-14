"""Closed-loop health gate for the controlled A-minus trial channel."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import text

from core.risk_constants import (
    A_MINUS_TRIAL_MIN_MATURE_SAMPLES,
    A_MINUS_TRIAL_POLICY_VERSION,
    A_MINUS_TRIAL_PROMOTION_MIN_AVG_RETURN,
    A_MINUS_TRIAL_PROMOTION_MIN_PROFIT_FACTOR,
    A_MINUS_TRIAL_ROUND_TRIP_COST_PCT,
)
from core.strategy_health import _apply_stop_take_model


def _snapshot(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _future_expression(sqlite: bool) -> str:
    if sqlite:
        return """(SELECT GROUP_CONCAT(printf('%f:%f:%f', sub.low, sub.high, sub.close), ',')
                    FROM (SELECT d.low, d.high, d.close FROM daily_k d
                          WHERE d.code=e.code AND d.date>e.signal_date
                          ORDER BY d.date ASC LIMIT 5) sub)"""
    return """(SELECT string_agg(CONCAT_WS(':', sub.low::text, sub.high::text, sub.close::text), ',')
                FROM (SELECT d.low, d.high, d.close FROM daily_k d
                      WHERE d.code=e.code AND d.date>e.signal_date
                      ORDER BY d.date ASC LIMIT 5) sub)"""


def _parse_future(value: Any) -> list[tuple[float, float, float]]:
    if not value:
        return []
    try:
        return [tuple(float(part) for part in bar.split(":")) for bar in str(value).split(",")]
    except (TypeError, ValueError):
        return []


def _metrics(returns: pd.Series) -> dict[str, float | int]:
    values = pd.to_numeric(returns, errors="coerce").dropna()
    gains = float(values[values > 0].sum())
    losses = abs(float(values[values < 0].sum()))
    return {
        "mature_samples": int(len(values)),
        "win_rate": round(float(values.gt(0).mean()) * 100, 2) if len(values) else 0.0,
        "avg_return": round(float(values.mean()), 4) if len(values) else 0.0,
        "profit_factor": round(gains / losses, 4) if losses > 0 else (99.0 if gains > 0 else 0.0),
    }


def build_a_minus_trial_health(engine, days: int = 730) -> dict[str, Any]:
    """Enable collection until 30 mature Bark samples, then enforce promotion economics."""
    thresholds = {
        "min_mature_samples": A_MINUS_TRIAL_MIN_MATURE_SAMPLES,
        "min_avg_return": A_MINUS_TRIAL_PROMOTION_MIN_AVG_RETURN,
        "min_profit_factor": A_MINUS_TRIAL_PROMOTION_MIN_PROFIT_FACTOR,
    }
    if engine is None:
        return {
            "status": "ERROR", "enabled": False, "promotion_eligible": False,
            "reason": "A-试仓健康度数据源不可用", "policy_version": A_MINUS_TRIAL_POLICY_VERSION,
            "thresholds": thresholds,
        }
    try:
        future = _future_expression(engine.dialect.name == "sqlite")
        rows = pd.read_sql(text(f"""
            SELECT e.signal_date,e.code,e.planned_entry_price,e.signal_snapshot,
                   {future} AS future_csv
            FROM execution_intents e
            WHERE e.signal_date>=:cutoff AND e.source='bark' AND e.instruction='可交易'
            ORDER BY e.signal_date,e.code
        """), engine, params={"cutoff": (date.today() - timedelta(days=max(1, int(days)))).isoformat()})
    except Exception as exc:
        return {
            "status": "ERROR", "enabled": False, "promotion_eligible": False,
            "reason": f"A-试仓健康度计算失败：{str(exc)[:120]}",
            "policy_version": A_MINUS_TRIAL_POLICY_VERSION, "thresholds": thresholds,
        }

    returns = []
    for row in rows.itertuples(index=False):
        snapshot = _snapshot(row.signal_snapshot)
        if not snapshot.get("a_minus_trial"):
            continue
        future_bars = _parse_future(row.future_csv)
        entry = float(row.planned_entry_price or 0)
        if entry <= 0 or len(future_bars) < 5:
            continue
        gross = _apply_stop_take_model(entry, future_bars)
        returns.append(gross - A_MINUS_TRIAL_ROUND_TRIP_COST_PCT)

    metrics = _metrics(pd.Series(returns, dtype="float64"))
    mature = int(metrics["mature_samples"])
    if mature < A_MINUS_TRIAL_MIN_MATURE_SAMPLES:
        status, enabled, promotion = "COLLECTING", True, False
        reason = f"A-试仓成熟样本{mature}/{A_MINUS_TRIAL_MIN_MATURE_SAMPLES}，继续小仓收集"
    else:
        promotion = (
            float(metrics["avg_return"]) >= A_MINUS_TRIAL_PROMOTION_MIN_AVG_RETURN
            and float(metrics["profit_factor"]) >= A_MINUS_TRIAL_PROMOTION_MIN_PROFIT_FACTOR
        )
        status, enabled = ("PROMOTION_ELIGIBLE", True) if promotion else ("PAUSED", False)
        reason = (
            "A-试仓达到升级经济性门槛，等待人工批准正式升级"
            if promotion else "A-试仓30笔经济性未达标，已自动暂停新增试仓"
        )
    return {
        **metrics,
        "status": status,
        "enabled": enabled,
        "promotion_eligible": promotion,
        "reason": reason,
        "policy_version": A_MINUS_TRIAL_POLICY_VERSION,
        "thresholds": thresholds,
        "net_cost_pct": A_MINUS_TRIAL_ROUND_TRIP_COST_PCT,
    }
