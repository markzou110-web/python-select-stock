"""Champion/challenger promotion rules kept independent from signal generation."""
from typing import Any, Dict, Iterable


REQUIRED_POSITIVE_WINDOWS = 3
REQUIRED_OOS_TRADES = 100


def evaluate_challenger(metrics: Dict[str, Any]) -> Dict[str, Any]:
    checks = {
        "oos_trades": int(metrics.get("oos_trades") or 0) >= REQUIRED_OOS_TRADES,
        "positive_windows": int(metrics.get("consecutive_positive_windows") or 0) >= REQUIRED_POSITIVE_WINDOWS,
        "positive_expectancy": float(metrics.get("net_expectancy") or 0) > 0,
        "profit_factor": float(metrics.get("profit_factor") or 0) >= 1.3,
        "positive_alpha": float(metrics.get("alpha") or 0) > 0,
        "drawdown_within_budget": bool(metrics.get("drawdown_within_budget", False)),
    }
    return {
        "eligible": all(checks.values()),
        "checks": checks,
        "status": "PROMOTION_READY" if all(checks.values()) else "SHADOW",
        "policy_version": "champion-challenger-v1",
    }


def select_champion(releases: Iterable[Dict[str, Any]]) -> Dict[str, Any] | None:
    champions = [row for row in releases if row.get("status") == "CHAMPION"]
    if len(champions) != 1:
        return None
    return champions[0]
