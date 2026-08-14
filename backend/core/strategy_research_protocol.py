"""Shared point-in-time market-state and walk-forward research protocol."""

from __future__ import annotations

from typing import Any, Callable, Iterable

import pandas as pd
from sqlalchemy import text

from core.decision_layer import build_market_decision_context


BREADTH_AXES = {
    "ADVANCE": "B2",
    "CLIMAX": "B2H",
    "REPAIR": "B1",
    "DIVERGENCE": "B1",
    "V_REPAIR": "B1",
    "RETREAT": "B0",
    "ICE": "B0",
}


def breadth_axis(stage: str) -> str:
    return BREADTH_AXES.get(str(stage or "").upper(), "UNKNOWN")


def build_causal_breadth_history(
    aggregates: pd.DataFrame,
    offensive_lookup: dict[pd.Timestamp, bool],
) -> pd.DataFrame:
    """Derive each breadth stage using only cross-sectional rows known that day."""
    if aggregates is None or aggregates.empty:
        return pd.DataFrame()
    breadth = aggregates.copy()
    breadth["date"] = pd.to_datetime(breadth["date"], errors="coerce")
    breadth = breadth.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    cycle_history: list[dict[str, Any]] = []
    stages: list[str] = []
    for row in breadth.itertuples(index=False):
        day = pd.Timestamp(row.date).normalize()
        current = {
            "date": day.strftime("%Y-%m-%d"),
            "advance_ratio": float(row.advance_ratio or 0),
            "strong_ratio": float(row.strong_ratio or 0),
            "weak_ratio": float(row.weak_ratio or 0),
            "avg_return": float(row.avg_return or 0),
        }
        cycle_history.append(current)
        context = build_market_decision_context(
            pd.DataFrame(),
            {
                "status": "OFFENSIVE" if offensive_lookup.get(day, False) else "DEFENSIVE",
                "limit_down_count": int(row.limit_down_count or 0),
            },
            cycle_history=cycle_history,
            data_date=current["date"],
        )
        stages.append(str(context.get("market_sentiment_stage") or "UNKNOWN"))
    breadth["market_sentiment_stage"] = stages
    return breadth


def load_causal_breadth_history(
    engine,
    *,
    start_date: str,
    offensive_lookup: dict[pd.Timestamp, bool],
) -> pd.DataFrame:
    """Load point-in-time cross-sectional breadth and derive its causal stage."""
    query = text(
        """
        WITH priced AS (
            SELECT date, code,
                   close / NULLIF(LAG(close) OVER (PARTITION BY code ORDER BY date), 0) - 1 AS ret
            FROM daily_k
            WHERE date >= :start_date
        )
        SELECT date,
               AVG(CASE WHEN ret > 0 THEN 1.0 ELSE 0.0 END) * 100 AS advance_ratio,
               AVG(CASE WHEN ret >= 0.05 THEN 1.0 ELSE 0.0 END) * 100 AS strong_ratio,
               AVG(CASE WHEN ret <= -0.05 THEN 1.0 ELSE 0.0 END) * 100 AS weak_ratio,
               AVG(ret) * 100 AS avg_return,
               SUM(CASE WHEN ret <= -0.098 THEN 1 ELSE 0 END) AS limit_down_count
        FROM priced
        WHERE ret BETWEEN -0.25 AND 0.25
        GROUP BY date
        ORDER BY date
        """
    )
    aggregates = pd.read_sql(query, engine, params={"start_date": start_date})
    return build_causal_breadth_history(aggregates, offensive_lookup)


def route_permissions(index_axis: str, width_axis: str) -> dict[str, str]:
    """Return the confirmed permission matrix without granting production rights."""
    trend, width = str(index_axis), str(width_axis)
    if width == "B0":
        return {
            "route_a": "BLOCKED",
            "route_b": "BLOCKED",
            "route_c": "OBSERVE_REPAIR_ORIGIN",
        }

    if trend == "T2":
        route_a = {
            "B2": "CONFIRM",
            "B2H": "PULLBACK_ONLY",
            "B1": "OBSERVE",
        }.get(width, "BLOCKED")
        route_b = "SHADOW_PULLBACK" if width in {"B2", "B2H"} else "SHADOW_OBSERVE"
        return {"route_a": route_a, "route_b": route_b, "route_c": "RESEARCH"}

    if trend == "T1":
        return {
            "route_a": "OBSERVE",
            "route_b": "SHADOW_OBSERVE",
            "route_c": "SHADOW_OBSERVE",
        }

    if trend == "T0":
        return {
            "route_a": "BLOCKED",
            "route_b": "SHADOW_OBSERVE" if width in {"B2", "B2H"} else "BLOCKED",
            "route_c": "SHADOW_REPAIR" if width in {"B1", "B2", "B2H"} else "OBSERVE_REPAIR_ORIGIN",
        }

    return {"route_a": "BLOCKED", "route_b": "BLOCKED", "route_c": "RESEARCH"}


def apply_confirmation_hysteresis(candidate: pd.Series, *, upgrade_days: int) -> pd.Series:
    """Require consecutive completed days for CONFIRM; apply downgrades immediately."""
    required = max(1, int(upgrade_days))
    if required == 1:
        return candidate.astype(str).copy()
    output: list[str] = []
    confirm_streak = 0
    for value in candidate.astype(str):
        if value == "CONFIRM":
            confirm_streak += 1
            output.append("CONFIRM" if confirm_streak >= required else "OBSERVE")
        else:
            confirm_streak = 0
            output.append(value)
    return pd.Series(output, index=candidate.index, dtype="object")


def _normalize_index_history(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", f"{label}_close", f"{label}_ema20"])
    work = frame.copy()
    date_col = "date" if "date" in work.columns else "日期"
    close_col = "close" if "close" in work.columns else "收盘"
    if date_col not in work.columns or close_col not in work.columns:
        return pd.DataFrame(columns=["date", f"{label}_close", f"{label}_ema20"])
    work["date"] = pd.to_datetime(work[date_col], errors="coerce")
    work[f"{label}_close"] = pd.to_numeric(work[close_col], errors="coerce")
    work = work.dropna(subset=["date", f"{label}_close"]).sort_values("date")
    work[f"{label}_ema20"] = work[f"{label}_close"].ewm(span=20, adjust=False).mean()
    return work[["date", f"{label}_close", f"{label}_ema20"]].drop_duplicates("date", keep="last")


def load_official_index_histories(
    fetcher: Callable[[str], pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load official Shanghai Composite and ChiNext histories without stock fallback."""
    if fetcher is None:
        import akshare as ak

        fetcher = ak.stock_zh_index_daily
    sh = fetcher(symbol="sh000001")
    cyb = fetcher(symbol="sz399006")
    normalized_sh = _normalize_index_history(sh, "sh")
    normalized_cyb = _normalize_index_history(cyb, "cyb")
    if normalized_sh.empty or normalized_cyb.empty:
        return pd.DataFrame(), pd.DataFrame()
    if float(normalized_sh["sh_close"].iloc[-1]) < 1000 or float(normalized_cyb["cyb_close"].iloc[-1]) < 500:
        return pd.DataFrame(), pd.DataFrame()
    return sh, cyb


def build_dual_axis_history(
    sh_index: pd.DataFrame,
    cyb_index: pd.DataFrame,
    breadth_history: pd.DataFrame,
    *,
    start_date: str | None = None,
) -> pd.DataFrame:
    """Build T2/T1/T0 × B2/B2H/B1/B0 states using completed daily data."""
    sh = _normalize_index_history(sh_index, "sh")
    cyb = _normalize_index_history(cyb_index, "cyb")
    if sh.empty or cyb.empty or breadth_history is None or breadth_history.empty:
        return pd.DataFrame()

    width = breadth_history.copy()
    date_col = "date" if "date" in width.columns else "日期"
    stage_col = (
        "market_sentiment_stage"
        if "market_sentiment_stage" in width.columns
        else "stage"
    )
    if date_col not in width.columns or stage_col not in width.columns:
        return pd.DataFrame()
    width["date"] = pd.to_datetime(width[date_col], errors="coerce")
    width["breadth_stage"] = width[stage_col].astype(str).str.upper()
    width["breadth_axis"] = width["breadth_stage"].map(BREADTH_AXES).fillna("UNKNOWN")
    width = width[["date", "breadth_stage", "breadth_axis"]].dropna(subset=["date"])

    merged = sh.merge(cyb, on="date", how="inner").merge(width, on="date", how="inner")
    if merged.empty:
        return merged
    sh_bull = merged["sh_close"] > merged["sh_ema20"]
    cyb_bull = merged["cyb_close"] > merged["cyb_ema20"]
    merged["index_axis"] = "T1"
    merged.loc[sh_bull & cyb_bull, "index_axis"] = "T2"
    merged.loc[~sh_bull & ~cyb_bull, "index_axis"] = "T0"

    permissions = [
        route_permissions(trend, width_axis)
        for trend, width_axis in zip(merged["index_axis"], merged["breadth_axis"])
    ]
    merged["route_a_candidate_permission"] = [item["route_a"] for item in permissions]
    merged["route_b_permission"] = [item["route_b"] for item in permissions]
    merged["route_c_permission"] = [item["route_c"] for item in permissions]
    merged["route_a_permission_1d"] = apply_confirmation_hysteresis(
        merged["route_a_candidate_permission"],
        upgrade_days=1,
    )
    merged["route_a_permission_2d"] = apply_confirmation_hysteresis(
        merged["route_a_candidate_permission"],
        upgrade_days=2,
    )
    if start_date:
        merged = merged[merged["date"].ge(pd.Timestamp(start_date))]
    return merged.sort_values("date").reset_index(drop=True)


def generate_walk_forward_windows(
    dates: Iterable[Any],
    *,
    train_months: int = 24,
    validation_months: int = 6,
    test_months: int = 3,
    step_months: int = 3,
) -> list[dict[str, pd.Timestamp]]:
    normalized = pd.to_datetime(pd.Series(list(dates)), errors="coerce").dropna()
    if normalized.empty:
        return []
    start, end = normalized.min().normalize(), normalized.max().normalize()
    windows: list[dict[str, pd.Timestamp]] = []
    train_start = start
    while True:
        train_end = train_start + pd.DateOffset(months=train_months) - pd.Timedelta(days=1)
        validation_start = train_end + pd.Timedelta(days=1)
        validation_end = validation_start + pd.DateOffset(months=validation_months) - pd.Timedelta(days=1)
        test_start = validation_end + pd.Timedelta(days=1)
        test_end = test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1)
        if test_end > end:
            break
        windows.append(
            {
                "train_start": train_start,
                "train_end": train_end,
                "validation_start": validation_start,
                "validation_end": validation_end,
                "test_start": test_start,
                "test_end": test_end,
            }
        )
        train_start = train_start + pd.DateOffset(months=step_months)
    return windows


def _profit_factor(returns: pd.Series) -> float | None:
    gains = float(returns[returns > 0].sum())
    losses = abs(float(returns[returns < 0].sum()))
    if losses > 0:
        return gains / losses
    return 99.0 if gains > 0 else None


def _event_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    completed = frame[frame["exec_filled"].fillna(False) & frame["exec_return_pct"].notna()]
    returns = pd.to_numeric(completed["exec_return_pct"], errors="coerce").dropna()
    factor = _profit_factor(returns)
    return {
        "signals": int(len(frame)),
        "completed": int(len(returns)),
        "win_rate": round(float(returns.gt(0).mean()) * 100, 2) if len(returns) else 0.0,
        "avg_return": round(float(returns.mean()), 4) if len(returns) else 0.0,
        "median_return": round(float(returns.median()), 4) if len(returns) else 0.0,
        "profit_factor": round(factor, 4) if factor is not None else None,
    }


def build_walk_forward_report(
    events: pd.DataFrame,
    *,
    variants: tuple[str, ...],
    variant_col: str = "variant",
    start_date: str | None = None,
    end_date: str | None = None,
    min_train_completed: int = 50,
    min_validation_completed: int = 30,
) -> dict[str, Any]:
    """Select only on train/validation and report frozen non-overlapping tests."""
    if events is None or events.empty:
        return {"status": "INSUFFICIENT_DATA", "windows": [], "historical_thresholds_met": False}
    work = events.copy()
    work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce")
    work["exit_date"] = pd.to_datetime(work["exit_date"], errors="coerce")
    work = work.dropna(subset=["signal_date"])
    first = pd.Timestamp(start_date) if start_date else work["signal_date"].min()
    last = pd.Timestamp(end_date) if end_date else work["signal_date"].max()
    windows = generate_walk_forward_windows(pd.date_range(first, last, freq="D"))
    results: list[dict[str, Any]] = []
    selected_test_returns: list[float] = []

    for number, window in enumerate(windows, start=1):
        variant_results: dict[str, Any] = {}
        eligible: list[str] = []
        for variant in variants:
            variant_rows = work[work[variant_col].eq(variant)]
            train_rows = variant_rows[
                variant_rows["signal_date"].between(window["train_start"], window["train_end"])
                & variant_rows["exit_date"].notna()
                & variant_rows["exit_date"].le(window["train_end"])
            ]
            validation_rows = variant_rows[
                variant_rows["signal_date"].between(window["validation_start"], window["validation_end"])
                & variant_rows["exit_date"].notna()
                & variant_rows["exit_date"].le(window["validation_end"])
            ]
            test_rows = variant_rows[
                variant_rows["signal_date"].between(window["test_start"], window["test_end"])
                & variant_rows["exit_date"].notna()
                & variant_rows["exit_date"].le(window["test_end"])
            ]
            metrics = {
                "train": _event_metrics(train_rows),
                "validation": _event_metrics(validation_rows),
                "test": _event_metrics(test_rows),
            }
            variant_results[variant] = metrics
            if (
                metrics["train"]["completed"] >= min_train_completed
                and metrics["validation"]["completed"] >= min_validation_completed
                and metrics["train"]["avg_return"] > 0
                and metrics["validation"]["avg_return"] > 0
                and (metrics["train"]["profit_factor"] or 0) >= 1
                and (metrics["validation"]["profit_factor"] or 0) >= 1
            ):
                eligible.append(variant)

        selected = max(
            eligible,
            key=lambda name: (
                variant_results[name]["validation"]["avg_return"],
                variant_results[name]["validation"]["profit_factor"] or 0,
            ),
            default=None,
        )
        selected_test = (
            variant_results[selected]["test"]
            if selected is not None
            else _event_metrics(work.iloc[0:0])
        )
        if selected is not None:
            selected_rows = work[
                work[variant_col].eq(selected)
                & work["signal_date"].between(window["test_start"], window["test_end"])
                & work["exit_date"].notna()
                & work["exit_date"].le(window["test_end"])
                & work["exec_filled"].fillna(False)
                & work["exec_return_pct"].notna()
            ]
            selected_test_returns.extend(
                pd.to_numeric(selected_rows["exec_return_pct"], errors="coerce").dropna().tolist()
            )
        results.append(
            {
                "window": number,
                **{key: value.strftime("%Y-%m-%d") for key, value in window.items()},
                "selected_variant": selected,
                "selected_test": selected_test,
                "variants": variant_results,
            }
        )

    combined = pd.Series(selected_test_returns, dtype=float)
    combined_factor = _profit_factor(combined)
    positive_windows = sum(
        item["selected_test"]["completed"] > 0 and item["selected_test"]["avg_return"] > 0
        for item in results
    )
    historical_met = (
        positive_windows >= 3
        and len(combined) >= 100
        and float(combined.mean()) > 0
        and (combined_factor or 0) >= 1.3
    )
    return {
        "status": "E3_NOT_REACHED",
        "historical_thresholds_met": historical_met,
        "window_count": len(results),
        "selected_window_count": sum(item["selected_variant"] is not None for item in results),
        "unselected_window_count": sum(item["selected_variant"] is None for item in results),
        "positive_oos_windows": positive_windows,
        "combined_test": {
            "completed": int(len(combined)),
            "avg_return": round(float(combined.mean()), 4) if len(combined) else 0.0,
            "median_return": round(float(combined.median()), 4) if len(combined) else 0.0,
            "profit_factor": round(combined_factor, 4) if combined_factor is not None else None,
        },
        "e3_blockers": [
            "组合最大回撤和样本外年化收益尚未由统一组合权益曲线验证",
            "点时股票池、历史ST/退市和行业归属仍不完整",
            "2026属于已查看的研究验证段，不是未触碰测试",
        ],
        "windows": results,
    }


def select_frozen_test_events(
    events: pd.DataFrame,
    walk_forward_report: dict[str, Any],
    *,
    variant_col: str = "variant",
) -> pd.DataFrame:
    """Return test-period signals for only the variant frozen before each window."""
    if events is None or events.empty:
        return pd.DataFrame()
    work = events.copy()
    work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce")
    selected: list[pd.DataFrame] = []
    for window in walk_forward_report.get("windows") or []:
        variant = window.get("selected_variant")
        if not variant:
            continue
        test_start = pd.Timestamp(window["test_start"])
        test_end = pd.Timestamp(window["test_end"])
        rows = work[
            work[variant_col].eq(variant)
            & work["signal_date"].between(test_start, test_end)
        ].copy()
        if rows.empty:
            continue
        rows["portfolio_test_end"] = test_end
        rows["walk_forward_window"] = int(window["window"])
        selected.append(rows)
    return pd.concat(selected, ignore_index=True) if selected else pd.DataFrame()
