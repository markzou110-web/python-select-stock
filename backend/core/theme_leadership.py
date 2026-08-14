"""Observation-only discovery for theme-driven independent leaders.

This module deliberately does not produce execution permission.  It supplements
the existing scanner with an early research signal for stocks that outperform a
weak industry while a recognizable market narrative is forming.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd
from sqlalchemy import bindparam, text

from core.logging_config import logger


_THEME_ALIASES = {
    "AI应用": ("人工智能", "AI", "DeepSeek", "大模型", "智能评标"),
    "算力": ("算力", "东数西算", "数据中心", "国资云"),
    "国企改革": ("国企改革", "市值管理", "估值提升"),
    "水利": ("水利", "黄河", "南水北调"),
}
_DENIAL_MARKERS = ("澄清", "不涉及", "不属于", "否认", "失实")


def _normalise_code(value: Any) -> str:
    text = str(value or "").strip()
    return text.zfill(6) if text.isdigit() else text


def extract_market_themes(
    reason: str = "",
    concept_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Map unstructured reason/concept text to stable market-theme labels."""
    parts = [str(reason or ""), *(str(tag or "") for tag in (concept_tags or []))]
    combined = " ".join(parts)
    combined_lower = combined.lower()
    themes = [
        theme
        for theme, aliases in _THEME_ALIASES.items()
        if any(alias.lower() in combined_lower for alias in aliases)
    ]
    denial_detected = any(marker in combined for marker in _DENIAL_MARKERS)
    evidence_sources = int(bool(reason)) + int(bool(concept_tags))
    credibility = "MEDIUM" if themes and evidence_sources >= 2 and not denial_detected else "LOW"
    return {
        "themes": themes,
        "credibility": credibility,
        "denial_detected": denial_detected,
    }


def detect_technical_seeds(
    history: pd.DataFrame,
    snapshot: pd.DataFrame,
    observed_at: Optional[datetime] = None,
    min_industry_members: int = 5,
) -> List[Dict[str, Any]]:
    """Find early relative-strength seeds using completed daily bars only."""
    required_history = {"code", "name", "industry", "date", "close", "vol"}
    required_snapshot = {"code", "price", "pct_chg"}
    if (
        history is None
        or snapshot is None
        or history.empty
        or snapshot.empty
        or not required_history.issubset(history.columns)
        or not required_snapshot.issubset(snapshot.columns)
    ):
        return []

    observed_at = observed_at or datetime.now()
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["vol"] = pd.to_numeric(frame["vol"], errors="coerce")
    frame["code"] = frame["code"].map(_normalise_code)
    frame = frame[
        frame["date"].notna()
        & (frame["date"].dt.date < observed_at.date())
        & frame["close"].gt(0)
        & frame["vol"].gt(0)
    ]

    metrics: List[Dict[str, Any]] = []
    for code, group in frame.groupby("code", sort=False):
        group = group.sort_values("date").drop_duplicates("date", keep="last")
        if len(group) < 21:
            continue
        tail = group.tail(21)
        latest = tail.iloc[-1]
        base_5d = float(tail.iloc[-6]["close"])
        previous_20 = tail.iloc[:-1]
        if base_5d <= 0 or previous_20.empty:
            continue
        metrics.append({
            "code": code,
            "name": str(latest.get("name") or ""),
            "industry": str(latest.get("industry") or "未分类"),
            "signal_date": latest["date"].strftime("%Y-%m-%d"),
            "ret_5d": (float(latest["close"]) / base_5d - 1.0) * 100,
            "volume_ratio_20d": float(latest["vol"]) / float(previous_20["vol"].mean()),
            "new_high_20d": float(latest["close"]) >= float(previous_20["close"].max()),
        })
    metric_frame = pd.DataFrame(metrics)
    if metric_frame.empty:
        return []

    industry_sizes = metric_frame.groupby("industry")["code"].transform("size")
    metric_frame = metric_frame[industry_sizes >= max(1, int(min_industry_members))].copy()
    if metric_frame.empty:
        return []
    metric_frame["industry_median_5d"] = metric_frame.groupby("industry")["ret_5d"].transform("median")
    metric_frame["industry_percentile"] = (
        metric_frame.groupby("industry")["ret_5d"].rank(method="average", pct=True) * 100
    )
    metric_frame["industry_excess_5d"] = metric_frame["ret_5d"] - metric_frame["industry_median_5d"]

    live = snapshot.copy()
    live["code"] = live["code"].map(_normalise_code)
    live["price"] = pd.to_numeric(live["price"], errors="coerce")
    live["pct_chg"] = pd.to_numeric(live["pct_chg"], errors="coerce")
    merged = metric_frame.merge(live, on="code", how="inner", suffixes=("", "_live"))
    merged = merged[
        merged["ret_5d"].between(5.0, 15.0, inclusive="both")
        & merged["volume_ratio_20d"].ge(1.5)
        & merged["new_high_20d"]
        & merged["industry_percentile"].ge(85.0)
        & merged["pct_chg"].between(-3.0, 7.0, inclusive="left")
        & merged["price"].gt(0)
    ]

    results: List[Dict[str, Any]] = []
    for row in merged.to_dict("records"):
        name = str(row.get("name_live") or row.get("name") or "")
        if "ST" in name.upper() or "退" in name:
            continue
        independent = row["industry_percentile"] >= 90 and row["industry_excess_5d"] >= 6
        results.append({
            "code": row["code"],
            "name": name,
            "industry": row["industry"],
            "signal_date": row["signal_date"],
            "price": round(float(row["price"]), 2),
            "pct_chg": round(float(row["pct_chg"]), 2),
            "ret_5d": round(float(row["ret_5d"]), 2),
            "industry_excess_5d": round(float(row["industry_excess_5d"]), 2),
            "industry_percentile": round(float(row["industry_percentile"]), 1),
            "volume_ratio_20d": round(float(row["volume_ratio_20d"]), 2),
            "state": "INDEPENDENT_LEADER" if independent else "EARLY_WATCH",
            "trade_eligible": False,
            "trade_bucket": "OBSERVE",
            "instruction_state": "THEME_SHADOW",
        })
    return sorted(
        results,
        key=lambda item: (
            item["state"] == "INDEPENDENT_LEADER",
            item["industry_percentile"],
            item["industry_excess_5d"],
            item["volume_ratio_20d"],
        ),
        reverse=True,
    )


def enrich_theme_seeds(
    seeds: Iterable[Mapping[str, Any]],
    hot_reason_map: Optional[Mapping[str, str]] = None,
    concept_map: Optional[Mapping[str, Sequence[str]]] = None,
) -> List[Dict[str, Any]]:
    """Attach narrative evidence while preserving the observation-only contract."""
    hot_reason_map = hot_reason_map or {}
    concept_map = concept_map or {}
    enriched: List[Dict[str, Any]] = []
    for seed in seeds:
        item = dict(seed)
        code = _normalise_code(item.get("code"))
        reason = str(hot_reason_map.get(code) or "")
        concepts = list(concept_map.get(code) or [])
        attribution = extract_market_themes(reason, concepts)
        item.update({
            "code": code,
            "market_themes": attribution["themes"],
            "market_theme": "/".join(attribution["themes"]),
            "theme_reason": reason,
            "concept_tags": concepts,
            "narrative_credibility": attribution["credibility"],
            "denial_detected": attribution["denial_detected"],
            "trade_eligible": False,
            "trade_bucket": "OBSERVE",
            "instruction_state": "THEME_SHADOW",
        })
        enriched.append(item)
    return enriched


def build_theme_leadership_body(items: Sequence[Mapping[str, Any]], slot: str) -> str:
    """Build a Bark section whose wording can never be mistaken for permission."""
    lines = [
        f"【题材独立龙头影子｜不可交易】 {slot}",
        "性质：早期研究观察，不是买入指令；后续必须另行收到明确交易许可才可执行。",
    ]
    for item in items:
        themes = item.get("market_theme") or "题材待核实"
        state = "弱行业独立领涨" if item.get("state") == "INDEPENDENT_LEADER" else "早期走强"
        credibility = "双源印证" if item.get("narrative_credibility") == "MEDIUM" else "低可信待核实"
        lines.extend([
            f"{item.get('name', '')}({item.get('code', '')}) | {themes} | {state}",
            (
                f"5日 {float(item.get('ret_5d') or 0):+.1f}% / 领先行业 "
                f"{float(item.get('industry_excess_5d') or 0):+.1f}% / 量比 "
                f"{float(item.get('volume_ratio_20d') or 0):.2f} / {credibility}"
            ),
            "行动：加入影子观察，等待价格确认与风控门禁；当前不可交易。",
        ])
    return "\n".join(lines)


def summarize_theme_leadership_outcomes(
    events: pd.DataFrame,
    daily: pd.DataFrame,
) -> Dict[str, Any]:
    """Evaluate shadow signals without converting them into trade signals."""
    horizons = (1, 3, 5)
    returns: Dict[int, List[float]] = {days: [] for days in horizons}
    if events is None or daily is None or events.empty or daily.empty:
        return {
            "sample_count": 0,
            "horizons": {
                f"{days}d": {"sample_count": 0, "avg_return_pct": None, "win_rate_pct": None}
                for days in horizons
            },
        }

    bars = daily.copy()
    bars["code"] = bars["code"].map(_normalise_code)
    bars["date"] = pd.to_datetime(bars["date"], errors="coerce")
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars = bars[bars["date"].notna() & bars["close"].gt(0)].sort_values(["code", "date"])
    valid_events = 0
    for event in events.to_dict("records"):
        payload = event.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                payload = {}
        price = float(payload.get("price") or payload.get("current_price") or 0)
        event_time = pd.to_datetime(event.get("event_time"), errors="coerce")
        if price <= 0 or pd.isna(event_time):
            continue
        code = _normalise_code(event.get("code"))
        future = bars[(bars["code"] == code) & (bars["date"].dt.date > event_time.date())]
        valid_events += 1
        for days in horizons:
            if len(future) >= days:
                returns[days].append((float(future.iloc[days - 1]["close"]) / price - 1.0) * 100)

    summary: Dict[str, Any] = {}
    for days in horizons:
        values = pd.Series(returns[days], dtype="float64")
        summary[f"{days}d"] = {
            "sample_count": int(len(values)),
            "avg_return_pct": round(float(values.mean()), 2) if len(values) else None,
            "win_rate_pct": round(float(values.gt(0).mean() * 100), 1) if len(values) else None,
        }
    return {"sample_count": valid_events, "horizons": summary}


def _load_recent_history(engine: Any, observed_at: datetime) -> pd.DataFrame:
    try:
        return pd.read_sql(text("""
            WITH recent_dates AS (
                SELECT DISTINCT date
                FROM daily_k
                WHERE date < :as_of
                ORDER BY date DESC
                LIMIT 30
            )
            SELECT d.code, b.name, COALESCE(b.industry, '') AS industry,
                   d.date, d.close, d.vol
            FROM daily_k d
            JOIN recent_dates r ON r.date = d.date
            LEFT JOIN stock_basic b ON b.code = d.code
            ORDER BY d.code, d.date
        """), engine, params={"as_of": observed_at.date()})
    except Exception as exc:
        logger.warning(f"Theme leadership history unavailable: {exc}")
        return pd.DataFrame()


def _record_shadow_events(engine: Any, items: Sequence[Mapping[str, Any]], observed_at: datetime) -> None:
    if not items:
        return
    try:
        existing = pd.read_sql(text("""
            SELECT code
            FROM lifecycle_events
            WHERE event_type = 'THEME_LEADERSHIP_SHADOW'
              AND DATE(event_time) = :event_date
        """), engine, params={"event_date": observed_at.date()})
        seen = set(existing.get("code", pd.Series(dtype=str)).map(_normalise_code))
    except Exception as exc:
        logger.debug(f"Theme leadership dedupe unavailable: {exc}")
        seen = set()

    from core.audit_log import record_lifecycle_event

    for item in items:
        code = _normalise_code(item.get("code"))
        if code in seen:
            continue
        record_lifecycle_event(
            "THEME_LEADERSHIP_SHADOW",
            source="theme_momentum_watcher",
            code=code,
            name=item.get("name"),
            strategy_type="THEME_SHADOW",
            theme=item.get("market_theme"),
            payload=dict(item),
        )
        seen.add(code)


def discover_theme_leadership(
    engine: Any,
    snapshot: pd.DataFrame,
    observed_at: Optional[datetime] = None,
    max_candidates: int = 12,
) -> Dict[str, Any]:
    """Discover, attribute and record observation-only theme leaders."""
    observed_at = observed_at or datetime.now()
    seeds = detect_technical_seeds(_load_recent_history(engine, observed_at), snapshot, observed_at=observed_at)
    if not seeds:
        return {"items": [], "count": 0, "technical_seed_count": 0}

    candidates = seeds[:max(1, int(max_candidates))]
    from core.data import get_cached_data, set_cached_data
    from core.direct_sources import eastmoney_concept_blocks, ths_hot_reason

    trade_date = observed_at.strftime("%Y-%m-%d")
    hot_cache_key = f"theme_leadership:ths:{trade_date}"
    hot_rows = get_cached_data(hot_cache_key, 1800)
    if hot_rows is None:
        hot_rows = ths_hot_reason(trade_date)
        set_cached_data(hot_cache_key, hot_rows)
    hot_reason_map = {
        _normalise_code(row.get("code")): str(row.get("reason") or "")
        for row in (hot_rows or [])
        if isinstance(row, Mapping)
    }

    concept_map: Dict[str, Sequence[str]] = {}
    for seed in candidates:
        code = seed["code"]
        cache_key = f"theme_leadership:concepts:{code}"
        concept_payload = get_cached_data(cache_key, 21600)
        if concept_payload is None:
            concept_payload = eastmoney_concept_blocks(code)
            set_cached_data(cache_key, concept_payload)
        concept_map[code] = list((concept_payload or {}).get("concept_tags") or [])

    attributed = enrich_theme_seeds(candidates, hot_reason_map, concept_map)
    items = [item for item in attributed if item.get("market_themes")]
    _record_shadow_events(engine, items, observed_at)
    return {"items": items, "count": len(items), "technical_seed_count": len(seeds)}


def load_theme_leadership_review(engine: Any, days: int = 120) -> Dict[str, Any]:
    """Load persisted shadow events and summarize forward returns."""
    days = min(730, max(5, int(days)))
    try:
        events = pd.read_sql(text("""
            SELECT code, event_time, payload
            FROM lifecycle_events
            WHERE event_type = 'THEME_LEADERSHIP_SHADOW'
              AND event_time >= CURRENT_DATE - (:days * INTERVAL '1 day')
            ORDER BY event_time
        """), engine, params={"days": days})
        if events.empty:
            result = summarize_theme_leadership_outcomes(events, pd.DataFrame())
        else:
            codes = sorted({_normalise_code(code) for code in events["code"]})
            daily = pd.read_sql(text("""
                SELECT code, date, close, high, low
                FROM daily_k
                WHERE code IN :codes
                  AND date >= CURRENT_DATE - (:days * INTERVAL '1 day')
                ORDER BY code, date
            """).bindparams(bindparam("codes", expanding=True)), engine,
                params={"codes": codes, "days": days})
            result = summarize_theme_leadership_outcomes(events, daily)
        return {
            **result,
            "window_days": days,
            "trade_eligible": False,
            "purpose": "shadow_validation",
        }
    except Exception as exc:
        logger.warning(f"Theme leadership review unavailable: {exc}")
        return {
            "sample_count": 0,
            "horizons": {},
            "window_days": days,
            "trade_eligible": False,
            "purpose": "shadow_validation",
            "error": "review_unavailable",
        }
