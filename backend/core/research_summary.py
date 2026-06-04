from collections import Counter, defaultdict
from typing import Any, Dict, List

from sqlalchemy import text


def _avg(values: List[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _parse_percent(value: Any) -> float | None:
    if value is None:
        return None
    text_value = str(value).replace("%", "").strip()
    if not text_value:
        return None
    try:
        return float(text_value)
    except ValueError:
        return None


def _top(counter: Counter[str], limit: int = 8) -> List[Dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def _score_bucket(score: Any) -> str:
    try:
        score_value = float(score or 0)
    except (TypeError, ValueError):
        score_value = 0
    if score_value >= 85:
        return "强信号"
    if score_value >= 70:
        return "中高信号"
    if score_value >= 55:
        return "观察信号"
    return "弱信号"


def build_research_summary(engine, limit: int = 500) -> Dict[str, Any]:
    """Summarize recent scan_history rows for strategy research review."""
    if not engine:
        return {
            "status": "error",
            "summary": {"total_signals": 0, "latest_date": None, "avg_score": 0, "avg_win_rate": 0},
            "by_strategy": [],
            "by_industry": [],
            "by_regime": [],
            "by_signal": [],
            "by_trade_action": [],
            "score_buckets": [],
        }

    safe_limit = min(max(int(limit or 500), 1), 2000)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT date, strategy_type, industry, score, win_rate,
                   price_action_regime, price_action_signal,
                   pa_trade_action, pa_trade_setup
            FROM scan_history
            ORDER BY date DESC
            LIMIT :limit
        """), {"limit": safe_limit}).mappings().all()

    strategies: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "scores": [], "win_rates": []})
    industries: Counter[str] = Counter()
    regimes: Counter[str] = Counter()
    signals: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    score_buckets: Counter[str] = Counter()
    all_scores: List[float] = []
    all_win_rates: List[float] = []
    latest_date = None

    for row in rows:
        if latest_date is None and row.get("date") is not None:
            latest_date = str(row["date"])

        strategy = row.get("strategy_type") or "unknown"
        strategies[strategy]["count"] += 1

        if row.get("score") is not None:
            score = float(row["score"])
            all_scores.append(score)
            strategies[strategy]["scores"].append(score)

        win_rate = _parse_percent(row.get("win_rate"))
        if win_rate is not None:
            all_win_rates.append(win_rate)
            strategies[strategy]["win_rates"].append(win_rate)

        industries[row.get("industry") or "未知行业"] += 1
        regimes[row.get("price_action_regime") or "未知结构"] += 1
        signals[row.get("price_action_signal") or row.get("pa_trade_setup") or "未知信号"] += 1
        actions[row.get("pa_trade_action") or "UNKNOWN"] += 1
        score_buckets[_score_bucket(row.get("score"))] += 1

    by_strategy = [
        {
            "strategy_type": strategy,
            "count": stats["count"],
            "avg_score": _avg(stats["scores"]),
            "avg_win_rate": _avg(stats["win_rates"]),
        }
        for strategy, stats in strategies.items()
    ]
    by_strategy.sort(key=lambda item: (-item["count"], -item["avg_score"], item["strategy_type"]))

    return {
        "status": "ok",
        "summary": {
            "total_signals": len(rows),
            "latest_date": latest_date,
            "avg_score": _avg(all_scores),
            "avg_win_rate": _avg(all_win_rates),
        },
        "by_strategy": by_strategy,
        "by_industry": _top(industries),
        "by_regime": _top(regimes),
        "by_signal": _top(signals),
        "by_trade_action": _top(actions),
        "score_buckets": _top(score_buckets, limit=4),
    }
