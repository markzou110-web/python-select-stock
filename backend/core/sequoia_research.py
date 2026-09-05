"""Point-in-time research signals adapted from Sequoia-X concepts.

These helpers only generate factors or SHADOW candidates. They never grant
trade permission; execution remains owned by scanner governance.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

import numpy as np
import pandas as pd
from sqlalchemy import text

from core.logging_config import logger


def _number_series(frame: pd.DataFrame, chinese: str, english: str) -> pd.Series:
    source = frame[chinese] if chinese in frame.columns else frame.get(english)
    if source is None:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(source, errors="coerce")


def compute_cross_sectional_rps(
    history: pd.DataFrame,
    *,
    periods: Iterable[int] = (60, 120, 250),
    as_of: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """Calculate point-in-time market and sector RPS percentiles.

    Only securities with a bar on the latest completed date are ranked, so a
    suspended/stale quote cannot look strong merely because its price is old.
    """
    if history is None or history.empty:
        return {}
    work = history.rename(columns={"日期": "date", "收盘": "close", "行业": "industry"}).copy()
    required = {"code", "date", "close"}
    if not required.issubset(work.columns):
        return {}
    work["code"] = work["code"].astype(str).str.zfill(6)
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["date", "close"])
    work = work[work["close"] > 0]
    if as_of is not None:
        cutoff = pd.to_datetime(as_of, errors="coerce")
        if not pd.isna(cutoff):
            work = work[work["date"] <= cutoff]
    if work.empty:
        return {}

    work = work.sort_values(["code", "date"])
    latest_date = work["date"].max()
    periods = tuple(sorted({int(period) for period in periods if int(period) > 0}))
    grouped_close = work.groupby("code", sort=False)["close"]
    for period in periods:
        work[f"return_{period}"] = grouped_close.pct_change(periods=period, fill_method=None)

    latest = work[work["date"].eq(latest_date)].copy()
    for period in periods:
        return_col = f"return_{period}"
        rps_col = f"rps_{period}"
        valid = latest[return_col].notna()
        latest[rps_col] = np.nan
        latest.loc[valid, rps_col] = latest.loc[valid, return_col].rank(pct=True) * 100
        if period == 120 and "industry" in latest.columns:
            sector_col = "rps_sector_120"
            latest[sector_col] = np.nan
            valid_sector = valid & latest["industry"].fillna("").ne("")
            latest.loc[valid_sector, sector_col] = (
                latest.loc[valid_sector]
                .groupby("industry")[return_col]
                .rank(pct=True)
                * 100
            )

    if {"rps_60", "rps_120"}.issubset(latest.columns):
        latest["rps_acceleration"] = latest["rps_60"] - latest["rps_120"]

    result: Dict[str, Dict[str, Any]] = {}
    output_columns = [f"rps_{period}" for period in periods]
    output_columns += [column for column in ("rps_sector_120", "rps_acceleration") if column in latest]
    for _, row in latest.iterrows():
        values: Dict[str, Any] = {"rps_data_date": latest_date.date().isoformat()}
        for column in output_columns:
            value = row.get(column)
            values[column] = round(float(value), 1) if value is not None and not pd.isna(value) else None
        result[str(row["code"])] = values
    return result


def load_cross_sectional_rps(engine, as_of: str) -> Dict[str, Dict[str, Any]]:
    """Load four point-in-time closes per stock and calculate RPS."""
    try:
        points = pd.read_sql(
            text("""
                WITH ranked AS (
                    SELECT d.code, d.date, d.close, b.industry,
                           ROW_NUMBER() OVER (PARTITION BY d.code ORDER BY d.date DESC) AS rn
                    FROM daily_k d
                    LEFT JOIN stock_basic b ON b.code = d.code
                    WHERE d.date <= :as_of
                )
                SELECT code, industry,
                       MAX(CASE WHEN rn = 1 THEN date END) AS date,
                       MAX(CASE WHEN rn = 1 THEN close END) AS close_now,
                       MAX(CASE WHEN rn = 61 THEN close END) AS close_60,
                       MAX(CASE WHEN rn = 121 THEN close END) AS close_120,
                       MAX(CASE WHEN rn = 251 THEN close END) AS close_250
                FROM ranked
                WHERE rn IN (1, 61, 121, 251)
                GROUP BY code, industry
            """),
            engine,
            params={"as_of": as_of},
        )
        if points.empty:
            return {}
        points["code"] = points["code"].astype(str).str.zfill(6)
        points["date"] = pd.to_datetime(points["date"], errors="coerce")
        latest_date = points["date"].max()
        points = points[points["date"].eq(latest_date)].copy()
        points["close_now"] = pd.to_numeric(points["close_now"], errors="coerce")
        for period in (60, 120, 250):
            prior = pd.to_numeric(points[f"close_{period}"], errors="coerce")
            returns = points["close_now"] / prior - 1
            valid = prior.gt(0) & returns.notna()
            points[f"return_{period}"] = returns
            points[f"rps_{period}"] = np.nan
            points.loc[valid, f"rps_{period}"] = returns.loc[valid].rank(pct=True) * 100
            if period == 120:
                sector_valid = valid & points["industry"].fillna("").ne("")
                points["rps_sector_120"] = np.nan
                points.loc[sector_valid, "rps_sector_120"] = (
                    points.loc[sector_valid]
                    .groupby("industry")["return_120"]
                    .rank(pct=True)
                    * 100
                )
        points["rps_acceleration"] = points["rps_60"] - points["rps_120"]
        output: Dict[str, Dict[str, Any]] = {}
        for _, row in points.iterrows():
            values: Dict[str, Any] = {"rps_data_date": latest_date.date().isoformat()}
            for column in ("rps_60", "rps_120", "rps_250", "rps_sector_120", "rps_acceleration"):
                value = row.get(column)
                values[column] = round(float(value), 1) if value is not None and not pd.isna(value) else None
            output[str(row["code"])] = values
        return output
    except Exception as exc:
        logger.warning(f"Cross-sectional RPS unavailable: {exc}")
        return {}


def high_tight_flag_signal_mask(frame: pd.DataFrame) -> pd.Series:
    """Ordered 40-day advance followed by a tight, high, low-volume base."""
    high = _number_series(frame, "最高", "high")
    low = _number_series(frame, "最低", "low")
    volume = _number_series(frame, "成交量", "volume")
    signal = pd.Series(False, index=frame.index, dtype=bool)
    for position in range(39, len(frame)):
        start = position - 39
        window_low = low.iloc[start:position + 1]
        window_high = high.iloc[start:position + 1]
        if window_low.isna().any() or window_high.isna().any():
            continue
        low_offset = int(np.argmin(window_low.to_numpy()))
        base_low = float(window_low.iloc[low_offset])
        later_high = float(window_high.iloc[low_offset:].max())
        recent_high = float(high.iloc[position - 9:position + 1].max())
        recent_low = float(low.iloc[position - 9:position + 1].min())
        prior_volume = float(volume.iloc[position - 20:position].mean())
        if base_low <= 0 or recent_low <= 0 or prior_volume <= 0:
            continue
        ordered_advance = later_high / base_low >= 1.60
        tight = recent_high / recent_low <= 1.15
        high_level = recent_low >= later_high * 0.80
        contracted_volume = float(volume.iloc[position]) <= prior_volume * 0.60
        signal.iloc[position] = bool(ordered_advance and tight and high_level and contracted_volume)
    return signal


def turtle_breakout_signal_mask(frame: pd.DataFrame) -> pd.Series:
    """20-day high breakout confirmed by liquidity and a bullish real body."""
    close = _number_series(frame, "收盘", "close")
    open_ = _number_series(frame, "开盘", "open")
    high = _number_series(frame, "最高", "high")
    volume = _number_series(frame, "成交量", "volume")
    prior_high = high.shift(1).rolling(20, min_periods=20).max()
    turnover_amount = close * volume * 100.0  # 本系统成交量单位为“手”。
    return (
        (close > prior_high)
        & (turnover_amount >= 100_000_000)
        & (close > open_)
        & (close > close.shift(1))
    ).fillna(False)


def _daily_limit_ratio(code: str) -> float:
    code = str(code or "").zfill(6)
    if code.startswith(("4", "8", "920")):
        return 0.30
    if code.startswith(("30", "688")):
        return 0.20
    return 0.10


def limit_up_shakeout_signal_mask(frame: pd.DataFrame, code: str) -> pd.Series:
    """Board-aware limit-up day followed by high-volume support-holding washout."""
    close = _number_series(frame, "收盘", "close")
    open_ = _number_series(frame, "开盘", "open")
    low = _number_series(frame, "最低", "low")
    volume = _number_series(frame, "成交量", "volume")
    ratio = _daily_limit_ratio(code)
    limit_up_yesterday = close.shift(1) >= close.shift(2) * (1 + ratio - 0.005)
    bearish = close < open_
    volume_surge = volume >= volume.shift(1) * 2.0
    support_hold = low >= close.shift(1) * 0.998
    return (limit_up_yesterday & bearish & volume_surge & support_hold).fillna(False)


def confirm_limit_up_shakeout_candidates(
    candidates: Iterable[Dict[str, Any]],
    event_map: Dict[str, Dict[str, Any]],
    event_date: str,
) -> list[Dict[str, Any]]:
    """Require a sealed prior-session limit-up event before keeping a candidate."""
    confirmed: list[Dict[str, Any]] = []
    for candidate in candidates:
        code = str(candidate.get("代码") or candidate.get("code") or "").zfill(6)
        event = event_map.get(code) or {}
        if str(event.get("status") or "").upper() != "SEALED":
            continue
        row = dict(candidate)
        row.update({
            "shakeout_source_event_date": event_date,
            "prior_limit_up_status": "SEALED",
            "prior_limit_up_streak": event.get("limit_up_streak"),
            "prior_limit_up_break_count": event.get("break_count"),
        })
        confirmed.append(row)
    return confirmed


def signal_metrics(frame: pd.DataFrame, strategy_type: str, code: str = "") -> Dict[str, Any]:
    """Return explainable metrics for the latest research signal."""
    close = _number_series(frame, "收盘", "close")
    high = _number_series(frame, "最高", "high")
    low = _number_series(frame, "最低", "low")
    volume = _number_series(frame, "成交量", "volume")
    if frame.empty:
        return {}
    if strategy_type == "high_tight_flag":
        return {
            "advance_40d_pct": round((float(high.tail(40).max()) / float(low.tail(40).min()) - 1) * 100, 2),
            "range_10d_pct": round((float(high.tail(10).max()) / float(low.tail(10).min()) - 1) * 100, 2),
            "volume_ratio": round(float(volume.iloc[-1]) / float(volume.iloc[-21:-1].mean()), 2),
        }
    if strategy_type == "turtle_breakout":
        prior_high = float(high.shift(1).rolling(20).max().iloc[-1])
        return {
            "prior_high_20": round(prior_high, 2),
            "breakout_pct": round((float(close.iloc[-1]) / prior_high - 1) * 100, 2),
            "turnover_amount_yi": round(float(close.iloc[-1] * volume.iloc[-1] * 100) / 1e8, 2),
        }
    if strategy_type == "limit_up_shakeout":
        return {
            "prior_limit_ratio": _daily_limit_ratio(code),
            "volume_ratio": round(float(volume.iloc[-1]) / float(volume.iloc[-2]), 2),
            "support_distance_pct": round((float(low.iloc[-1]) / float(close.iloc[-2]) - 1) * 100, 2),
        }
    return {}
