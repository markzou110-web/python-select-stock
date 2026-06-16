from collections import defaultdict
from typing import Any, Dict, List


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return default


def _percentile_scores(values: List[float]) -> List[float]:
    if len(values) <= 1:
        return [50.0] * len(values)
    ordered = sorted(values)
    return [round(100 * ordered.index(value) / (len(ordered) - 1), 1) for value in values]


def calibrate_scan_scores(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert strategy-specific raw scores into a comparable 0-100 score."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in results:
        grouped[str(row.get("strategy_type") or "unknown")].append(row)

    for rows in grouped.values():
        raw_values = [float(row.get("Score") or 0) for row in rows]
        percentiles = _percentile_scores(raw_values)
        for row, raw_score, percentile in zip(rows, raw_values, percentiles):
            plan = row.get("pa_trade_plan") or {}
            if not row.get("pa_trade_action") and plan.get("action"):
                row["pa_trade_action"] = plan["action"]
            structure = _clamp(row.get("pa_structure_score"), _clamp(row.get("price_action_score"), 50))
            execution = _clamp(row.get("pa_execution_score"), 50)
            safety = _clamp(row.get("pa_risk_score"), 50)
            price_action_composite = round(structure * 0.4 + execution * 0.35 + safety * 0.25, 1)
            components = {
                "strategy_percentile": percentile,
                "price_action": price_action_composite,
                "price_action_structure": structure,
                "price_action_execution": execution,
                "price_action_safety": safety,
                "sector_alignment": _clamp(row.get("sector_alignment_score"), 50),
                "trade_opportunity": _clamp(row.get("trade_opportunity_score"), 50),
            }
            calibrated = round(
                components["strategy_percentile"] * 0.40
                + components["price_action"] * 0.20
                + components["sector_alignment"] * 0.15
                + components["trade_opportunity"] * 0.25,
                1,
            )
            row["raw_score"] = round(raw_score, 2)
            row["calibrated_score"] = calibrated
            row["score_components"] = components
            row["Score"] = calibrated

            missing = []
            if not row.get("strategy_type"):
                missing.append("strategy_type")
            if not row.get("pa_trade_action"):
                missing.append("pa_trade_action")
            if str(row.get("trade_bucket") or "UNKNOWN") in {"UNKNOWN", ""}:
                missing.append("trade_bucket")
            if str(row.get("market_regime") or "UNKNOWN") in {"UNKNOWN", ""}:
                missing.append("market_regime")
            row["research_missing_fields"] = missing
            row["research_eligible"] = not missing
    return results
