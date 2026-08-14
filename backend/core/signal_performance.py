"""Append-only Bark signal snapshots and point-in-time performance attribution."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable

import pandas as pd
from sqlalchemy import text

from core.performance_metrics import return_metrics
from core.execution_insights import get_active_execution_plan


SNAPSHOT_VERSION = "intraday-signal-snapshot-v1"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _detail(candidate: Dict[str, Any]) -> Dict[str, Any]:
    value = candidate.get("price_action_detail") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return value if isinstance(value, dict) else {}


def _value(candidate: Dict[str, Any], detail: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = candidate.get(key)
        if value not in (None, ""):
            return value
        value = detail.get(key)
        if value not in (None, ""):
            return value
    return None


def save_intraday_signal_snapshots(
    candidates: Iterable[Dict[str, Any]],
    engine,
    source: str,
    signal_time: datetime | None = None,
) -> int:
    """Persist one immutable row per source/minute/code/strategy; retries are no-ops."""
    if engine is None:
        return 0
    observed_at = (signal_time or datetime.now()).replace(second=0, microsecond=0)
    json_expr = ":payload" if engine.dialect.name == "sqlite" else "CAST(:payload AS JSONB)"
    rows = []
    for candidate in candidates:
        detail = _detail(candidate)
        code = str(_value(candidate, detail, "代码", "code") or "").zfill(6)
        strategy = str(_value(candidate, detail, "strategy_type") or "squeeze")
        price = _number(_value(candidate, detail, "现价", "price"))
        if not code or price is None:
            continue
        identity = f"{source}:{observed_at.isoformat()}:{code}:{strategy}"
        signal_id = f"sig_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:28]}"
        trade_eligible = bool(_value(candidate, detail, "trade_eligible"))
        trade_bucket = str(_value(candidate, detail, "trade_bucket") or "OBSERVE")
        review_state = str(_value(candidate, detail, "execution_review_state") or "")
        if not review_state:
            review_state = "TRADE" if trade_eligible and trade_bucket == "TRADE" else "OBSERVE"
        payload = {
            "version": SNAPSHOT_VERSION,
            "score": _value(candidate, detail, "Score", "score", "calibrated_score"),
            "trade_opportunity_score": _value(candidate, detail, "trade_opportunity_score"),
            "trade_blockers": _value(candidate, detail, "trade_blockers") or [],
            "evidence_id": _value(candidate, detail, "evidence_id"),
            "evidence_grade": _value(candidate, detail, "evidence_grade"),
            "reachability_reason": _value(candidate, detail, "confirmation_reachability_reason"),
            "strong_exception_shadow_reason": _value(candidate, detail, "strong_exception_shadow_reason"),
            "bottom_discovery_stage": _value(candidate, detail, "bottom_discovery_stage"),
            "bottom_discovery_action": _value(candidate, detail, "bottom_discovery_action"),
            "sop_base_grade": _value(candidate, detail, "sop_base_grade"),
            "sop_quality_score": _value(candidate, detail, "sop_quality_score"),
            "display_signal_score": _value(candidate, detail, "display_signal_score"),
            "display_quality_score": _value(candidate, detail, "display_quality_score"),
            "display_opportunity_score": _value(candidate, detail, "display_opportunity_score"),
            "score_display_scale": _value(candidate, detail, "score_display_scale"),
            "sop_quality_gap_to_a": _value(candidate, detail, "sop_quality_gap_to_a"),
            "sop_grade_reason": _value(candidate, detail, "sop_grade_reason"),
            "sop_grade_transition_reasons": _value(candidate, detail, "sop_grade_transition_reasons") or [],
            "sop_checks": _value(candidate, detail, "sop_checks") or [],
            "sop_bonuses": _value(candidate, detail, "sop_bonuses") or [],
            "sop_risks": _value(candidate, detail, "sop_risks") or [],
            "sop_vetoes": _value(candidate, detail, "sop_vetoes") or [],
            "price_action_score": _value(candidate, detail, "price_action_score"),
            "pct_5d": _value(candidate, detail, "pct_5d"),
            "pa_trade_action": _value(candidate, detail, "pa_trade_action"),
            "pa_execution_policy_version": _value(candidate, detail, "pa_execution_policy_version"),
            "pa_execution_tier": _value(candidate, detail, "pa_execution_tier"),
            "pa_execution_tier_label": _value(candidate, detail, "pa_execution_tier_label"),
            "pa_execution_hard_blocked": bool(_value(candidate, detail, "pa_execution_hard_blocked")),
            "pa_execution_hard_reason": _value(candidate, detail, "pa_execution_hard_reason"),
            "pa_target_price": _value(candidate, detail, "pa_target_price", "target_price"),
            "a_eod_t1_plan": bool(_value(candidate, detail, "a_eod_t1_plan")),
            "a_eod_t1_policy_version": _value(candidate, detail, "a_eod_t1_policy_version"),
            "a_eod_t1_frozen_entry_price": _value(candidate, detail, "a_eod_t1_frozen_entry_price"),
            "a_eod_t1_frozen_stop_price": _value(candidate, detail, "a_eod_t1_frozen_stop_price"),
            "a_eod_t1_frozen_target_price": _value(candidate, detail, "a_eod_t1_frozen_target_price"),
            "a_eod_t1_position_pct": _value(candidate, detail, "a_eod_t1_position_pct"),
            "a_eod_t1_portfolio_cap_pct": _value(candidate, detail, "a_eod_t1_portfolio_cap_pct"),
            "a_eod_t1_max_positions": _value(candidate, detail, "a_eod_t1_max_positions"),
        }
        active_plan_payload = dict(detail)
        active_plan_payload.update({key: value for key, value in candidate.items() if value is not None})
        active_plan = get_active_execution_plan(active_plan_payload)
        rows.append({
            "signal_id": signal_id,
            "signal_date": observed_at.date(),
            "signal_time": observed_at,
            "source": source,
            "code": code,
            "name": _value(candidate, detail, "名称", "name"),
            "strategy_type": strategy,
            "grade": _value(candidate, detail, "sop_grade"),
            "signal_price": price,
            "confirmation_price": _number(active_plan["entry"]),
            "stop_price": _number(active_plan["stop"]),
            "trade_bucket": trade_bucket,
            "trade_eligible": int(trade_eligible),
            "instruction_state": review_state,
            "market_stage": _value(candidate, detail, "market_sentiment_stage", "market_regime"),
            "sector_phase": _value(candidate, detail, "sector_phase"),
            "sector_mainline": _value(candidate, detail, "sector_mainline"),
            "reachability": _value(candidate, detail, "confirmation_reachability"),
            "strong_exception_shadow": int(bool(_value(candidate, detail, "strong_exception_shadow"))),
            "payload": json.dumps(payload, ensure_ascii=False, default=str),
            "created_at": datetime.now(),
        })
    if not rows:
        return 0
    with engine.begin() as conn:
        result = conn.execute(text(f"""
            INSERT INTO intraday_signal_snapshots (
                signal_id, signal_date, signal_time, source, code, name, strategy_type,
                grade, signal_price, confirmation_price, stop_price, trade_bucket,
                trade_eligible, instruction_state, market_stage, sector_phase,
                sector_mainline, reachability, strong_exception_shadow, snapshot_payload, created_at
            ) VALUES (
                :signal_id, :signal_date, :signal_time, :source, :code, :name, :strategy_type,
                :grade, :signal_price, :confirmation_price, :stop_price, :trade_bucket,
                :trade_eligible, :instruction_state, :market_stage, :sector_phase,
                :sector_mainline, :reachability, :strong_exception_shadow, {json_expr}, :created_at
            ) ON CONFLICT(signal_id) DO NOTHING
        """), rows)
    return max(0, int(result.rowcount or 0))


def _metric(values: Iterable[Any]) -> Dict[str, Any]:
    series = pd.to_numeric(pd.Series(list(values), dtype="object"), errors="coerce").dropna()
    metrics = return_metrics(series)
    return {
        "signals": int(len(series)),
        "win_rate": round(float(metrics.get("win_rate") or 0), 1),
        "avg_return": round(float(metrics.get("expected_return") or 0), 2),
        "median_return": round(float(series.median()), 2) if len(series) else None,
        "profit_loss_ratio": round(float(metrics.get("profit_loss_ratio") or 0), 2),
    }


def _empty_report(days: int) -> Dict[str, Any]:
    return {
        "version": SNAPSHOT_VERSION,
        "status": "INSUFFICIENT_DATA",
        "days": days,
        "summary": {"snapshot_events": 0, "unique_daily_candidates": 0},
        "validation": {
            "selection": {"status": "INSUFFICIENT_DATA", "mature_5d": 0, "required": 30},
            "execution": {"status": "INSUFFICIENT_DATA", "mature_5d": 0, "required": 30},
            "confirmation": {"status": "INSUFFICIENT_DATA", "triggered_samples": 0, "required": 30},
        },
        "cohorts": [],
        "strong_stock_coverage": {},
        "gate_attribution": {},
        "items": [],
        "note": "尚无新版不可变盘中信号快照；不能用当天最终扫描结果替代历史时点",
    }


def build_signal_performance_report(engine, days: int = 120) -> Dict[str, Any]:
    """Measure discovery, timing, confirmation and gate counterfactuals separately."""
    days = max(1, min(int(days), 365))
    if engine is None:
        return _empty_report(days)
    cutoff = date.today() - timedelta(days=days)
    snapshots = pd.read_sql(text("""
        SELECT signal_id, signal_date, signal_time, source, code, name, strategy_type,
               grade, signal_price, confirmation_price, stop_price, trade_bucket,
               trade_eligible, instruction_state, market_stage, sector_phase,
               sector_mainline, reachability, strong_exception_shadow, snapshot_payload
        FROM intraday_signal_snapshots
        WHERE signal_date >= :cutoff
        ORDER BY signal_time ASC
    """), engine, params={"cutoff": cutoff})
    if snapshots.empty:
        return _empty_report(days)

    snapshots["signal_date"] = pd.to_datetime(snapshots["signal_date"]).dt.date
    snapshots["signal_time"] = pd.to_datetime(snapshots["signal_time"])
    first = snapshots.drop_duplicates(
        subset=["signal_date", "source", "code", "strategy_type"], keep="first"
    ).copy()
    daily = pd.read_sql(text("""
        SELECT code, date, open, high, low, close
        FROM daily_k
        WHERE date >= :start_date
        ORDER BY code, date
    """), engine, params={"start_date": cutoff - timedelta(days=10)})
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    for column in ("open", "high", "low", "close"):
        daily[column] = pd.to_numeric(daily[column], errors="coerce")

    bar_time_expr = "bar_time" if engine.dialect.name == "sqlite" else "CAST(bar_time AS TIMESTAMP)"
    minute = pd.read_sql(text(f"""
        SELECT code, bar_time, high, low, close
        FROM intraday_minute_bars
        WHERE {bar_time_expr} >= :start_time
        ORDER BY code, {bar_time_expr}
    """), engine, params={"start_time": datetime.combine(cutoff, datetime.min.time())})
    if not minute.empty:
        minute["bar_time"] = pd.to_datetime(minute["bar_time"])
        minute["bar_date"] = minute["bar_time"].dt.date

    daily_groups = {str(code): group.reset_index(drop=True) for code, group in daily.groupby("code")}
    minute_groups = {
        (str(code), bar_date): group.reset_index(drop=True)
        for (code, bar_date), group in minute.groupby(["code", "bar_date"])
    } if not minute.empty else {}

    items = []
    for _, row in first.iterrows():
        code = str(row["code"])
        signal_date = row["signal_date"]
        price = float(row["signal_price"])
        code_daily = daily_groups.get(code, pd.DataFrame())
        current_rows = code_daily.index[code_daily["date"] == signal_date].tolist() if not code_daily.empty else []
        current_index = current_rows[0] if current_rows else None
        close = float(code_daily.loc[current_index, "close"]) if current_index is not None else None
        returns: Dict[str, float | None] = {}
        for horizon in (1, 3, 5):
            future_index = current_index + horizon if current_index is not None else None
            future_close = (
                float(code_daily.loc[future_index, "close"])
                if future_index is not None and future_index < len(code_daily) else None
            )
            returns[f"ret_{horizon}d"] = round((future_close / price - 1) * 100, 2) if future_close else None
        alert_to_close = round((close / price - 1) * 100, 2) if close else None
        confirmation = _number(row.get("confirmation_price"))
        snapshot_payload = row.get("snapshot_payload") or {}
        if isinstance(snapshot_payload, str):
            try:
                snapshot_payload = json.loads(snapshot_payload)
            except json.JSONDecodeError:
                snapshot_payload = {}
        if not isinstance(snapshot_payload, dict):
            snapshot_payload = {}
        bars = minute_groups.get((code, signal_date), pd.DataFrame())
        post_signal = bars[bars["bar_time"] >= row["signal_time"]] if not bars.empty else bars
        confirmation_triggered = None
        confirmation_to_close = None
        if confirmation and not post_signal.empty:
            confirmation_triggered = bool((pd.to_numeric(post_signal["high"], errors="coerce") >= confirmation).any())
            if confirmation_triggered and close:
                confirmation_to_close = round((close / confirmation - 1) * 100, 2)
        item = {
            "signal_id": row["signal_id"], "signal_date": str(signal_date),
            "signal_time": row["signal_time"].isoformat(), "source": row["source"],
            "code": code, "name": row.get("name"), "strategy_type": row["strategy_type"],
            "grade": row.get("grade"), "signal_price": price,
            "sop_base_grade": snapshot_payload.get("sop_base_grade"),
            "bottom_discovery_stage": snapshot_payload.get("bottom_discovery_stage"),
            "bottom_discovery_action": snapshot_payload.get("bottom_discovery_action"),
            "sop_quality_score": snapshot_payload.get("sop_quality_score"),
            "display_signal_score": snapshot_payload.get("display_signal_score"),
            "display_quality_score": snapshot_payload.get("display_quality_score"),
            "display_opportunity_score": snapshot_payload.get("display_opportunity_score"),
            "score_display_scale": snapshot_payload.get("score_display_scale"),
            "sop_quality_gap_to_a": snapshot_payload.get("sop_quality_gap_to_a"),
            "sop_grade_reason": snapshot_payload.get("sop_grade_reason"),
            "sop_grade_transition_reasons": snapshot_payload.get("sop_grade_transition_reasons") or [],
            "sop_checks": snapshot_payload.get("sop_checks") or [],
            "sop_bonuses": snapshot_payload.get("sop_bonuses") or [],
            "sop_risks": snapshot_payload.get("sop_risks") or [],
            "sop_vetoes": snapshot_payload.get("sop_vetoes") or [],
            "trade_eligible": bool(row["trade_eligible"]),
            "instruction_state": row["instruction_state"],
            "strong_exception_shadow": bool(row["strong_exception_shadow"]),
            "reachability": row.get("reachability"), "alert_to_close_pct": alert_to_close,
            "confirmation_triggered": confirmation_triggered,
            "confirmation_to_close_pct": confirmation_to_close,
            **returns,
        }
        items.append(item)

    item_df = pd.DataFrame(items)
    cohorts = []
    masks = {
        "selection": pd.Series(True, index=item_df.index),
        "execution": item_df["trade_eligible"].astype(bool),
        "blocked": ~item_df["trade_eligible"].astype(bool),
        "strong_exception_shadow": item_df["strong_exception_shadow"].astype(bool),
    }
    for name, mask in masks.items():
        group = item_df[mask]
        cohorts.append({
            "cohort": name,
            "candidates": int(len(group)),
            "alert_to_close": _metric(group["alert_to_close_pct"]),
            "ret_1d": _metric(group["ret_1d"]),
            "ret_3d": _metric(group["ret_3d"]),
            "ret_5d": _metric(group["ret_5d"]),
            "confirmation_to_close": _metric(group["confirmation_to_close_pct"]),
        })

    attribution: Dict[str, int] = {}
    mature_blocked = item_df[(~item_df["trade_eligible"]) & item_df["ret_5d"].notna()]
    for value in mature_blocked["ret_5d"]:
        key = "RISK_GATE_MISSED_WINNER" if float(value) > 0 else "RISK_GATE_SAVED_LOSS"
        attribution[key] = attribution.get(key, 0) + 1
    attribution["NO_5D_OUTCOME"] = int(item_df["ret_5d"].isna().sum())
    attribution["CONFIRMATION_TRIGGERED"] = int((item_df["confirmation_triggered"] == True).sum())
    attribution["CONFIRMATION_NOT_TRIGGERED"] = int((item_df["confirmation_triggered"] == False).sum())

    daily_rank = daily.sort_values(["code", "date"]).copy()
    daily_rank["previous_close"] = daily_rank.groupby("code")["close"].shift(1)
    daily_rank["pct"] = (daily_rank["close"] / daily_rank["previous_close"] - 1) * 100
    coverage = {"dates": 0, "top20_hits": 0, "top50_hits": 0, "top100_hits": 0}
    for signal_date, group in first.groupby("signal_date"):
        ranked = daily_rank[daily_rank["date"] == signal_date].dropna(subset=["pct"]).sort_values("pct", ascending=False)
        if ranked.empty:
            continue
        coverage["dates"] += 1
        selected_codes = set(group["code"].astype(str))
        for top_n in (20, 50, 100):
            hits = len(selected_codes & set(ranked.head(top_n)["code"].astype(str)))
            coverage[f"top{top_n}_hits"] += hits
    for top_n in (20, 50, 100):
        denominator = coverage["dates"] * top_n
        coverage[f"top{top_n}_coverage_pct"] = round(coverage[f"top{top_n}_hits"] / denominator * 100, 2) if denominator else 0

    mature_5d = int(item_df["ret_5d"].notna().sum())
    execution_mature_5d = int(
        item_df[item_df["trade_eligible"].astype(bool)]["ret_5d"].notna().sum()
    )
    confirmation_samples = int(item_df["confirmation_to_close_pct"].notna().sum())
    required = 30
    validation = {
        "selection": {
            "status": "VALIDATED" if mature_5d >= required else "INSUFFICIENT_DATA",
            "mature_5d": mature_5d,
            "required": required,
        },
        "execution": {
            "status": "VALIDATED" if execution_mature_5d >= required else "INSUFFICIENT_DATA",
            "mature_5d": execution_mature_5d,
            "required": required,
        },
        "confirmation": {
            "status": "VALIDATED" if confirmation_samples >= required else "INSUFFICIENT_DATA",
            "triggered_samples": confirmation_samples,
            "required": required,
        },
    }
    return {
        "version": SNAPSHOT_VERSION,
        # Overall validation requires both discovery and executable samples;
        # a large observation cohort alone cannot validate the buy decision.
        "status": "VALIDATED" if all(
            validation[name]["status"] == "VALIDATED" for name in ("selection", "execution")
        ) else "INSUFFICIENT_DATA",
        "days": days,
        "summary": {
            "snapshot_events": int(len(snapshots)),
            "unique_daily_candidates": int(len(first)),
            "tradable_candidates": int(item_df["trade_eligible"].sum()),
            "strong_exception_shadow": int(item_df["strong_exception_shadow"].sum()),
            "mature_5d": mature_5d,
            "execution_mature_5d": execution_mature_5d,
            "confirmation_samples": confirmation_samples,
        },
        "validation": validation,
        "cohorts": cohorts,
        "strong_stock_coverage": coverage,
        "gate_attribution": attribution,
        "items": sorted(items, key=lambda item: item["signal_time"], reverse=True)[:100],
        "note": "同日同源同股只使用首次提醒评价；选股、可交易和确认样本分别验收；分钟数据不足时确认触发保持未知，避免日高价造成未来函数",
    }
