import json
from collections import Counter, defaultdict
from typing import Any, Dict, List

from sqlalchemy import text


def _parse_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _avg(values: List[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def build_ops_summary(engine, limit: int = 50) -> Dict[str, Any]:
    """Aggregate recent scan and failure-sample records for the ops dashboard."""
    if not engine:
        return {
            "status": "error",
            "scan_quality": {
                "total_scans": 0,
                "success_rate": 0,
                "avg_duration_sec": 0,
                "avg_candidates": 0,
                "avg_results": 0,
            },
            "failure_reason_top": [],
            "strategy_distribution": [],
            "failure_sample_by_strategy": [],
            "version_distribution": [],
            "performance_phases": [],
        }

    safe_limit = min(max(int(limit or 50), 1), 200)
    with engine.connect() as conn:
        scans = conn.execute(text("""
            SELECT status, strategy_type, duration_sec, candidate_count, result_count,
                   fail_reasons, version_snapshot, params_snapshot
            FROM scan_audit_log
            ORDER BY started_at DESC
            LIMIT :limit
        """), {"limit": safe_limit}).mappings().all()
        failures = conn.execute(text("""
            SELECT strategy_type, pnl_pct
            FROM failure_samples
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"limit": safe_limit}).mappings().all()

    failure_reasons: Counter[str] = Counter()
    strategy_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "scan_count": 0,
        "result_counts": [],
        "duration_secs": [],
    })
    versions: Counter[str] = Counter()
    phase_values: Dict[str, List[float]] = defaultdict(list)

    durations: List[float] = []
    candidates: List[float] = []
    results: List[float] = []
    success_count = 0

    for row in scans:
        if row.get("status") == "SUCCESS":
            success_count += 1

        if row.get("duration_sec") is not None:
            durations.append(float(row["duration_sec"]))
        if row.get("candidate_count") is not None:
            candidates.append(float(row["candidate_count"]))
        if row.get("result_count") is not None:
            results.append(float(row["result_count"]))

        strategy = row.get("strategy_type") or "unknown"
        strategy_stats[strategy]["scan_count"] += 1
        strategy_stats[strategy]["result_counts"].append(float(row.get("result_count") or 0))
        if row.get("duration_sec") is not None:
            strategy_stats[strategy]["duration_secs"].append(float(row["duration_sec"]))

        for reason, count in _parse_json(row.get("fail_reasons")).items():
            try:
                failure_reasons[str(reason)] += int(count or 0)
            except (TypeError, ValueError):
                failure_reasons[str(reason)] += 1

        for key, value in _parse_json(row.get("version_snapshot")).items():
            if value:
                versions[f"{key}:{value}"] += 1
        phases = _parse_json(row.get("params_snapshot")).get("performance_phases_sec") or {}
        if isinstance(phases, dict):
            for phase, seconds in phases.items():
                try:
                    phase_values[str(phase)].append(float(seconds))
                except (TypeError, ValueError):
                    continue

    failure_by_strategy: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "count": 0,
        "pnl_values": [],
    })
    for row in failures:
        strategy = row.get("strategy_type") or "unknown"
        failure_by_strategy[strategy]["count"] += 1
        if row.get("pnl_pct") is not None:
            failure_by_strategy[strategy]["pnl_values"].append(float(row["pnl_pct"]))

    total_scans = len(scans)
    strategy_distribution = [
        {
            "strategy_type": strategy,
            "scan_count": stats["scan_count"],
            "avg_results": _avg(stats["result_counts"]),
            "avg_duration_sec": _avg(stats["duration_secs"]),
        }
        for strategy, stats in strategy_stats.items()
    ]
    strategy_distribution.sort(key=lambda item: (-item["scan_count"], item["strategy_type"]))

    failure_sample_by_strategy = [
        {
            "strategy_type": strategy,
            "count": stats["count"],
            "avg_pnl_pct": _avg(stats["pnl_values"]),
        }
        for strategy, stats in failure_by_strategy.items()
    ]
    failure_sample_by_strategy.sort(key=lambda item: (-item["count"], item["strategy_type"]))

    return {
        "status": "ok",
        "scan_quality": {
            "total_scans": total_scans,
            "success_rate": round(success_count / total_scans * 100, 1) if total_scans else 0,
            "avg_duration_sec": _avg(durations),
            "avg_candidates": _avg(candidates),
            "avg_results": _avg(results),
        },
        "failure_reason_top": [
            {"reason": reason, "count": count}
            for reason, count in failure_reasons.most_common(8)
        ],
        "strategy_distribution": strategy_distribution,
        "failure_sample_by_strategy": failure_sample_by_strategy,
        "version_distribution": [
            {"version": version, "count": count}
            for version, count in versions.most_common(8)
        ],
        "performance_phases": sorted(
            [
                {"phase": phase, "avg_duration_sec": _avg(values), "samples": len(values)}
                for phase, values in phase_values.items()
            ],
            key=lambda item: -item["avg_duration_sec"],
        ),
    }
