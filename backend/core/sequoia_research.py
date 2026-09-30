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


def _trader_vic_2b_context(
    frame: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Find bullish 2B reclaims near a flat/rising 200-day average."""
    close = _number_series(frame, "收盘", "close")
    open_ = _number_series(frame, "开盘", "open")
    low = _number_series(frame, "最低", "low")
    volume = _number_series(frame, "成交量", "volume")
    ma200 = close.rolling(200, min_periods=200).mean()
    ma200_slope = ma200 / ma200.shift(20) - 1
    signal = pd.Series(False, index=frame.index, dtype=bool)
    support_levels = pd.Series(np.nan, index=frame.index, dtype=float)
    volume_ratios = pd.Series(np.nan, index=frame.index, dtype=float)

    for position in range(199, len(frame)):
        average_volume = volume.iloc[position - 20:position].mean()
        if (
            pd.isna(ma200.iloc[position])
            or pd.isna(ma200_slope.iloc[position])
            or ma200_slope.iloc[position] < -0.01
            or close.iloc[position] < ma200.iloc[position] * 0.95
            or average_volume <= 0
            or close.iloc[position] <= open_.iloc[position]
            or volume.iloc[position] < average_volume * 1.2
        ):
            continue

        for event in range(max(20, position - 5), position + 1):
            support = low.iloc[event - 20:event].min()
            if (
                pd.isna(support)
                or low.iloc[event] >= support
                or (event < position and close.iloc[position - 1] > support)
                or close.iloc[position] <= support
            ):
                continue
            signal.iloc[position] = True
            support_levels.iloc[position] = float(support)
            volume_ratios.iloc[position] = float(volume.iloc[position] / average_volume)
            break

    return signal, support_levels, volume_ratios


def trader_vic_2b_signal_mask(frame: pd.DataFrame) -> pd.Series:
    """Long-only 2B: reclaim a broken 20-day low with volume confirmation."""
    if frame is None or frame.empty:
        return pd.Series(False, index=frame.index if frame is not None else None, dtype=bool)
    return _trader_vic_2b_context(frame)[0]


def build_trader_vic_2b_markers(frame: pd.DataFrame) -> list[Dict[str, Any]]:
    """Return chart markers for 2B structures; these do not imply RPS approval."""
    if frame is None or frame.empty or "日期" not in frame.columns and "date" not in frame.columns:
        return []
    signal = trader_vic_2b_signal_mask(frame)
    date_column = "日期" if "日期" in frame.columns else "date"
    markers = []
    for position in np.flatnonzero(signal.to_numpy()):
        date = pd.to_datetime(frame.iloc[position][date_column], errors="coerce")
        if pd.isna(date):
            continue
        markers.append({
            "time": date.strftime("%Y-%m-%d"),
            "position": "belowBar",
            "color": "#0f766e",
            "shape": "circle",
            "text": "VIC 2B形态",
            "source": "trader_vic_2b",
        })
    return markers


def build_123_2b_markers(frame: pd.DataFrame, lookback: int = 30) -> list[Dict[str, Any]]:
    """Draw textbook-style 1-2-3 stages and failed-extreme 2B observations.

    1-2-3 swing pivots use a one-bar confirmation delay to avoid looking ahead.
    Markers are chart annotations only and do not grant trade eligibility.
    """
    if frame is None or frame.empty or len(frame) < 12:
        return []
    date_column = "日期" if "日期" in frame.columns else "date" if "date" in frame.columns else None
    if date_column is None:
        return []
    high = _number_series(frame, "最高", "high").reset_index(drop=True)
    low = _number_series(frame, "最低", "low").reset_index(drop=True)
    close = _number_series(frame, "收盘", "close").reset_index(drop=True)
    if high.isna().any() or low.isna().any() or close.isna().any():
        return []

    markers: list[Dict[str, Any]] = []
    pivots_high: list[int] = []
    pivots_low: list[int] = []
    state: dict[str, Any] | None = None
    window = max(10, int(lookback))

    def add_marker(index: int, text: str, *, bullish: bool, source: str, label: str) -> None:
        date = pd.to_datetime(frame.iloc[index][date_column], errors="coerce")
        if pd.isna(date):
            return
        markers.append({
            "time": date.strftime("%Y-%m-%d"),
            "position": "belowBar" if bullish else "aboveBar",
            "color": "#0f766e" if bullish else "#dc2626",
            "shape": "circle" if "2B" in text else "square",
            "text": text,
            "label": label,
            "source": source,
        })

    for index in range(2, len(frame)):
        # A pivot at index-1 is only known now, after its right-hand bar closes.
        pivot = index - 1
        if high.iloc[pivot] > high.iloc[pivot - 1] and high.iloc[pivot] > high.iloc[index]:
            pivots_high.append(pivot)
        if low.iloc[pivot] < low.iloc[pivot - 1] and low.iloc[pivot] < low.iloc[index]:
            pivots_low.append(pivot)

        prior_start = max(0, index - window)
        prior_high = float(high.iloc[prior_start:index].max())
        prior_low = float(low.iloc[prior_start:index].min())
        # Classic 2B: a marginal new extreme fails and the close returns inside.
        if high.iloc[index] > prior_high and close.iloc[index] < prior_high:
            add_marker(index, "2B卖", bullish=False, source="book_2b", label=f"假突破前高{prior_high:.2f}后收回；仅作风险观察")
        elif low.iloc[index] < prior_low and close.iloc[index] > prior_low:
            add_marker(index, "2B买", bullish=True, source="book_2b", label=f"假跌破前低{prior_low:.2f}后收复；仅作反转观察")

        if state is None:
            lows = [point for point in pivots_low if index - point <= window]
            highs = [point for point in pivots_high if index - point <= window]
            bearish_line = None
            bullish_line = None
            if len(lows) >= 2:
                a, b = lows[-2:]
                if low.iloc[b] > low.iloc[a]:
                    slope = (low.iloc[b] - low.iloc[a]) / (b - a)
                    bearish_line = (a, float(low.iloc[a]), slope)
            if len(highs) >= 2:
                a, b = highs[-2:]
                if high.iloc[b] < high.iloc[a]:
                    slope = (high.iloc[b] - high.iloc[a]) / (b - a)
                    bullish_line = (a, float(high.iloc[a]), slope)
            prior_close = float(close.iloc[index - 1])
            if bearish_line:
                a, value, slope = bearish_line
                line_now = value + slope * (index - a)
                line_prev = value + slope * (index - 1 - a)
                if prior_close >= line_prev and close.iloc[index] < line_now:
                    old_high = float(high.iloc[max(prior_start, a):index].max())
                    state = {"direction": "bear", "phase": 1, "break": index, "extreme": old_high}
                    add_marker(index, "123-1", bullish=False, source="book_123", label="上升趋势线收盘跌破：条件1")
            if state is None and bullish_line:
                a, value, slope = bullish_line
                line_now = value + slope * (index - a)
                line_prev = value + slope * (index - 1 - a)
                if prior_close <= line_prev and close.iloc[index] > line_now:
                    old_low = float(low.iloc[max(prior_start, a):index].min())
                    state = {"direction": "bull", "phase": 1, "break": index, "extreme": old_low}
                    add_marker(index, "123-1", bullish=True, source="book_123", label="下降趋势线收盘突破：条件1")
            continue

        if index - state["break"] > window:
            state = None
            continue
        if state["phase"] == 1:
            level = float(state["extreme"])
            if state["direction"] == "bear" and high.iloc[index] >= level * 0.985 and close.iloc[index] < level:
                state.update(phase=2, retest=index, reaction=float(low.iloc[state["break"]:index + 1].min()))
                add_marker(index, "123-2", bullish=False, source="book_123", label="反弹测试前高失败：条件2")
                if high.iloc[index] > level:
                    add_marker(index, "2B卖", bullish=False, source="book_2b", label="上破前高后收回其下：顶部2B预警，止损需明确")
            elif state["direction"] == "bull" and low.iloc[index] <= level * 1.015 and close.iloc[index] > level:
                state.update(phase=2, retest=index, reaction=float(high.iloc[state["break"]:index + 1].max()))
                add_marker(index, "123-2", bullish=True, source="book_123", label="回测前低未再创新低：条件2")
                if low.iloc[index] < level:
                    add_marker(index, "2B买", bullish=True, source="book_2b", label="跌破前低后收复其上：底部2B预警，止损需明确")
        elif state["phase"] == 2:
            broken = close.iloc[index] < state["reaction"] if state["direction"] == "bear" else close.iloc[index] > state["reaction"]
            if broken:
                bullish = state["direction"] == "bull"
                add_marker(index, "123-3", bullish=bullish, source="book_123", label="突破回调低点/反弹高点：123趋势转变确认")
                state = None
    unique: dict[tuple[str, str, str], Dict[str, Any]] = {}
    for marker in markers:
        unique.setdefault((marker["time"], marker["text"], marker["source"]), marker)
    return list(unique.values())


def ma_volume_signal_mask(frame: pd.DataFrame) -> pd.Series:
    """5/20-day bullish moving-average cross with 20-day volume expansion."""
    close = _number_series(frame, "收盘", "close")
    volume = _number_series(frame, "成交量", "volume")
    ma5 = close.rolling(5, min_periods=5).mean()
    ma20 = close.rolling(20, min_periods=20).mean()
    vol_ma20 = volume.rolling(20, min_periods=20).mean()
    crossed_up = (ma5.shift(1) <= ma20.shift(1)) & (ma5 > ma20)
    return (crossed_up & (volume > vol_ma20 * 1.5)).fillna(False)


def uptrend_limit_down_signal_mask(frame: pd.DataFrame, code: str) -> pd.Series:
    """High-volume board-limit decline while the prior 20/60-day trend is bullish."""
    close = _number_series(frame, "收盘", "close")
    volume = _number_series(frame, "成交量", "volume")
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    vol_ma20 = volume.rolling(20, min_periods=20).mean()
    ratio = _daily_limit_ratio(code)
    prior_uptrend = ma20.shift(1) > ma60.shift(1)
    limit_down = close <= close.shift(1) * (1 - ratio + 0.005)
    volume_surge = volume > vol_ma20 * 2.0
    return (prior_uptrend & limit_down & volume_surge).fillna(False)


def rps_breakout_proximity_mask(frame: pd.DataFrame) -> pd.Series:
    """Candidate stage for RPS: close remains within 10% of the 120-day high."""
    close = _number_series(frame, "收盘", "close")
    high = _number_series(frame, "最高", "high")
    high_120 = high.rolling(120, min_periods=120).max()
    return (close >= high_120 * 0.90).fillna(False)


def kangaroo_tail_signal_mask(frame: pd.DataFrame) -> pd.Series:
    """Bullish Elder kangaroo tail (long-only, SHADOW research signal).

    《以交易为生》：单根振幅达前 10 根常态振幅 2 倍以上的长柱向下刺破
    20 日低点，但收盘回到柱体上半部——恐慌抛售被吸收的反转信号。
    本系统仅做多，故只实现看涨袋鼠尾。
    """
    close = _number_series(frame, "收盘", "close")
    high = _number_series(frame, "最高", "high")
    low = _number_series(frame, "最低", "low")
    tail_range = high - low
    normal_range = tail_range.shift(1).rolling(10, min_periods=10).mean()
    prior_low = low.shift(1).rolling(20, min_periods=20).min()
    close_position = (close - low) / tail_range.replace(0, np.nan)
    return (
        (tail_range >= normal_range * 2.0)
        & (low < prior_low)
        & (close_position >= 0.5)
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
    if strategy_type == "trader_vic_2b":
        signal, support_levels, volume_ratios = _trader_vic_2b_context(frame)
        position = int(np.flatnonzero(signal.to_numpy())[-1]) if bool(signal.any()) else len(frame) - 1
        ma200 = close.rolling(200, min_periods=200).mean()
        prior_ma200 = ma200.shift(20)
        slope = ma200.iloc[position] / prior_ma200.iloc[position] - 1
        support = support_levels.iloc[position]
        return {
            "vic_2b_support": round(float(support), 2) if pd.notna(support) else None,
            "vic_2b_volume_ratio": round(float(volume_ratios.iloc[position]), 2)
            if pd.notna(volume_ratios.iloc[position]) else None,
            "ma200": round(float(ma200.iloc[position]), 2) if pd.notna(ma200.iloc[position]) else None,
            "ma200_slope_20d_pct": round(float(slope) * 100, 2) if pd.notna(slope) else None,
            "rps_120_minimum": 60,
        }
    if strategy_type == "ma_volume":
        ma5 = close.rolling(5, min_periods=5).mean()
        ma20 = close.rolling(20, min_periods=20).mean()
        volume_ratio = volume.iloc[-1] / volume.rolling(20, min_periods=20).mean().iloc[-1]
        return {
            "ma5": round(float(ma5.iloc[-1]), 2),
            "ma20": round(float(ma20.iloc[-1]), 2),
            "volume_ratio": round(float(volume_ratio), 2) if pd.notna(volume_ratio) and volume_ratio > 0 else None,
        }
    if strategy_type == "uptrend_limit_down":
        volume_ratio = volume.iloc[-1] / volume.rolling(20, min_periods=20).mean().iloc[-1]
        return {
            "ma20": round(float(close.rolling(20).mean().iloc[-2]), 2),
            "ma60": round(float(close.rolling(60).mean().iloc[-2]), 2),
            "limit_down_ratio": _daily_limit_ratio(code),
            "volume_ratio": round(float(volume_ratio), 2) if pd.notna(volume_ratio) and volume_ratio > 0 else None,
        }
    if strategy_type == "rps_breakout":
        high_120 = float(high.tail(120).max())
        return {
            "rps_threshold": 90,
            "high_120": round(high_120, 2),
            "distance_to_120d_high_pct": round((float(close.iloc[-1]) / high_120 - 1) * 100, 2) if high_120 > 0 else None,
        }
    if strategy_type == "limit_up_shakeout":
        return {
            "prior_limit_ratio": _daily_limit_ratio(code),
            "volume_ratio": round(float(volume.iloc[-1]) / float(volume.iloc[-2]), 2),
            "support_distance_pct": round((float(low.iloc[-1]) / float(close.iloc[-2]) - 1) * 100, 2),
        }
    return {}
