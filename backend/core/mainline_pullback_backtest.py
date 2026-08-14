"""Point-in-time research backtest for mainline-sector first-pullback entries."""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text

from core.a_grade_kline_replay import (
    DEFAULT_START_DATE,
    DEFAULT_TRAIN_END,
    DEFAULT_VALIDATION_END,
    _chunks,
    _load_codes,
    _load_price_chunk,
    _market_proxy,
    _paired_signal_indices,
)
from core.db import get_db_engine
from core.execution_labels import daily_limit_pct, minimum_buy_shares
from core.indicators import calculate_indicators
from core.price_action import analyze_price_action
from core.sector_strength import sector_phase
from core.strategy import _find_squeeze_signal_indices, _find_tv_zp_signal_indices


BACKTEST_VERSION = "mainline-first-pullback-v1"
ARMS = (
    ("signal_next_open_10d", "signal", 10, None),
    ("signal_next_open_20d", "signal", 20, None),
    ("pullback_strict_10d", "pullback", 10, "strict"),
    ("pullback_strict_20d", "pullback", 20, "strict"),
    ("pullback_practical_10d", "pullback", 10, "practical"),
    ("pullback_practical_20d", "pullback", 20, "practical"),
)


def build_sector_context(prices: pd.DataFrame) -> pd.DataFrame:
    """Build causal daily sector strength from stock returns and current industry mapping."""
    if prices.empty:
        return prices.copy()
    work = prices.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["pct"] = pd.to_numeric(work["pct"], errors="coerce")
    work = work.dropna(subset=["date", "industry", "pct"]).sort_values(["industry", "date"])
    if work.empty:
        return work
    work["breadth"] = pd.to_numeric(work["breadth"], errors="coerce").fillna(0)
    work["hot_ratio"] = pd.to_numeric(work["hot_ratio"], errors="coerce").fillna(0)
    work["limit_count"] = pd.to_numeric(work["limit_count"], errors="coerce").fillna(0)
    grouped = work.groupby("industry", sort=False)
    work["sector_3d_pct"] = grouped["pct"].transform(
        lambda values: ((1 + values / 100).rolling(3, min_periods=1).apply(np.prod, raw=True) - 1) * 100
    )
    work["sector_5d_pct"] = grouped["pct"].transform(
        lambda values: ((1 + values / 100).rolling(5, min_periods=1).apply(np.prod, raw=True) - 1) * 100
    )
    work["sector_trend_slope"] = grouped["sector_5d_pct"].diff().fillna(0)
    work["sector_consecutive_up_days"] = grouped["pct"].transform(
        lambda values: values.gt(0).groupby(values.le(0).cumsum()).cumsum()
    )
    work["score"] = (
        ((work["breadth"] - 40).clip(lower=0) * 0.75).clip(upper=35)
        + (work["pct"].clip(lower=0) * 8).clip(upper=25)
        + (work["hot_ratio"] * 1.5).clip(upper=15)
        + (work["limit_count"] * 2).clip(upper=10)
    ).clip(0, 100)
    work["phase"] = work.apply(
        lambda row: sector_phase(
            row["score"],
            row["breadth"],
            row["pct"],
            row["hot_ratio"],
            {
                "sector_3d_pct": row["sector_3d_pct"],
                "sector_5d_pct": row["sector_5d_pct"],
                "sector_consecutive_up_days": row["sector_consecutive_up_days"],
                "sector_trend_slope": row["sector_trend_slope"],
            },
        ),
        axis=1,
    )
    work["rank"] = work.groupby("date")["score"].rank(method="min", ascending=False)
    work["mainline"] = (
        work["phase"].isin({"SECTOR_EARLY", "SECTOR_CONFIRM"})
        & work["rank"].le(8)
        & work["score"].ge(58)
        & work["breadth"].ge(60)
    )
    return work


def load_sector_context(engine, start_date: str) -> pd.DataFrame:
    query = text(
        """
        WITH priced AS (
            SELECT d.date, d.code, COALESCE(b.industry, '未知') AS industry,
                   d.close / NULLIF(LAG(d.close) OVER (PARTITION BY d.code ORDER BY d.date), 0) - 1 AS ret
            FROM daily_k d
            LEFT JOIN stock_basic b ON b.code = d.code
            WHERE d.date >= :start_date
        )
        SELECT date, industry,
               AVG(ret) * 100 AS pct,
               AVG(CASE WHEN ret > 0 THEN 1.0 ELSE 0.0 END) * 100 AS breadth,
               AVG(CASE WHEN ret >= 0.05 THEN 1.0 ELSE 0.0 END) * 100 AS hot_ratio,
               SUM(CASE WHEN ret >= 0.098 THEN 1 ELSE 0 END) AS limit_count,
               COUNT(*) AS total_count
        FROM priced
        WHERE ret BETWEEN -0.25 AND 0.25 AND industry <> '未知'
        GROUP BY date, industry
        HAVING COUNT(*) >= 3
        ORDER BY industry, date
        """
    )
    return build_sector_context(pd.read_sql(query, engine, params={"start_date": start_date}))


def _sector_lookup(context: pd.DataFrame) -> dict[tuple[pd.Timestamp, str], dict[str, Any]]:
    return {
        (pd.Timestamp(row.date).normalize(), str(row.industry)): row._asdict()
        for row in context.itertuples(index=False)
    }


def stock_sector_fit(
    frame: pd.DataFrame,
    idx: int,
    industry: str,
    sector_lookup: dict[tuple[pd.Timestamp, str], dict[str, Any]],
) -> dict[str, float | bool]:
    if idx < 5:
        return {"core": False, "relative_5d": 0.0, "lead_consistency": 0.0, "today_excess": 0.0}
    signal_date = pd.Timestamp(frame.loc[idx, "日期"]).normalize()
    sector = sector_lookup.get((signal_date, industry), {})
    close_now = float(frame.loc[idx, "收盘"])
    close_5d = float(frame.loc[idx - 5, "收盘"])
    stock_5d = (close_now / close_5d - 1) * 100 if close_5d > 0 else 0.0
    relative_5d = stock_5d - float(sector.get("sector_5d_pct") or 0)
    wins = 0
    observed = 0
    for current in range(idx - 4, idx + 1):
        previous_close = float(frame.loc[current - 1, "收盘"])
        if previous_close <= 0:
            continue
        day = pd.Timestamp(frame.loc[current, "日期"]).normalize()
        day_sector = sector_lookup.get((day, industry))
        if not day_sector:
            continue
        stock_pct = (float(frame.loc[current, "收盘"]) / previous_close - 1) * 100
        wins += stock_pct > float(day_sector.get("pct") or 0)
        observed += 1
    consistency = wins / observed * 100 if observed else 0.0
    previous_close = float(frame.loc[idx - 1, "收盘"])
    today_pct = (close_now / previous_close - 1) * 100 if previous_close > 0 else 0.0
    today_excess = today_pct - float(sector.get("pct") or 0)
    return {
        "core": bool(relative_5d >= 1 and consistency >= 60 and today_excess >= 0),
        "relative_5d": round(relative_5d, 2),
        "lead_consistency": round(consistency, 1),
        "today_excess": round(today_excess, 2),
    }


def find_first_pullback_confirmation(
    frame: pd.DataFrame,
    signal_idx: int,
    wait_days: int = 5,
    max_pullback_volume_ratio: float = 0.8,
    min_confirmation_volume_ratio: float = 1.15,
) -> int | None:
    """Find a confirmation bar strictly after a causal, low-volume first pullback."""
    if signal_idx + 2 >= len(frame):
        return None
    signal_low = float(frame.loc[signal_idx, "最低"])
    ema20 = float(frame.loc[signal_idx, "EMA20"] or 0)
    invalidation = min(signal_low, ema20) if ema20 > 0 else signal_low
    had_pullback = False
    last_idx = min(len(frame) - 1, signal_idx + max(2, int(wait_days)))
    for idx in range(signal_idx + 1, last_idx + 1):
        close = float(frame.loc[idx, "收盘"])
        low = float(frame.loc[idx, "最低"])
        if close < invalidation or low < invalidation * 0.985:
            return None
        vol_ma = float(frame.loc[idx, "Vol_MA20"] or 0)
        volume = float(frame.loc[idx, "成交量"])
        previous_close = float(frame.loc[idx - 1, "收盘"])
        pullback_today = (
            (close < previous_close or low < float(frame.loc[signal_idx, "收盘"]))
            and vol_ma > 0
            and volume <= vol_ma * max_pullback_volume_ratio
        )
        if had_pullback:
            ema10 = float(frame.loc[idx, "EMA10"] or 0)
            confirmed = (
                close > float(frame.loc[idx, "开盘"])
                and close > float(frame.loc[idx - 1, "最高"])
                and vol_ma > 0
                and volume >= vol_ma * min_confirmation_volume_ratio
                and (ema10 <= 0 or close >= ema10)
            )
            if confirmed:
                return idx
        had_pullback = had_pullback or pullback_today
    return None


def simulate_atr_trade(
    code: str,
    frame: pd.DataFrame,
    anchor_idx: int,
    *,
    max_hold_days: int,
    max_open_gap_pct: float,
    structure_low: float,
) -> dict[str, Any]:
    """Enter at the bar after anchor and exit with bounded structure stop plus ATR trail."""
    empty = {"filled": False, "return_pct": None, "exit_reason": None}
    entry_idx = anchor_idx + 1
    if entry_idx >= len(frame) or entry_idx + max_hold_days >= len(frame):
        return {**empty, "reason": "持有期未成熟"}
    reference = float(frame.loc[anchor_idx, "收盘"])
    entry_raw = float(frame.loc[entry_idx, "开盘"])
    if reference <= 0 or entry_raw <= 0:
        return {**empty, "reason": "入场价无效"}
    gap = (entry_raw / reference - 1) * 100
    if gap > max_open_gap_pct or gap >= daily_limit_pct(code) - 0.2:
        return {**empty, "reason": "高开或涨停无法成交", "open_gap_pct": round(gap, 2)}

    raw_risk = max(0.0, (entry_raw - structure_low) / entry_raw * 100) if structure_low > 0 else 7.0
    risk_pct = min(7.0, max(4.0, raw_risk))
    stop = entry_raw * (1 - risk_pct / 100)
    atr = float(frame.loc[entry_idx, "ATR"] or 0)
    if not math.isfinite(atr) or atr <= 0:
        atr = entry_raw * 0.03
    max_close = entry_raw
    exit_raw = float(frame.loc[entry_idx + max_hold_days, "收盘"])
    exit_reason = "持有期结束"
    exit_idx = entry_idx + max_hold_days
    for idx in range(entry_idx + 1, entry_idx + max_hold_days + 1):
        day_open = float(frame.loc[idx, "开盘"])
        day_low = float(frame.loc[idx, "最低"])
        day_close = float(frame.loc[idx, "收盘"])
        if day_low <= stop:
            exit_raw = min(day_open, stop) if day_open <= stop else stop
            exit_reason, exit_idx = "结构止损", idx
            break
        max_close = max(max_close, day_close)
        if day_close < max_close - atr * 2.2:
            exit_raw, exit_reason, exit_idx = day_close, "ATR移动止盈", idx
            break
        if idx - entry_idx >= 3 and day_close <= entry_raw:
            exit_raw, exit_reason, exit_idx = day_close, "3日无跟随", idx
            break

    entry = entry_raw * 1.0005
    exit_price = exit_raw * 0.9995
    shares = int(5000 / entry / 100) * 100
    if shares < minimum_buy_shares(code):
        return {**empty, "reason": "计划资金不足最低申报数量", "open_gap_pct": round(gap, 2)}
    buy_value, sell_value = entry * shares, exit_price * shares
    fees = max(buy_value * 0.00025, 5.0) + max(sell_value * 0.00025, 5.0) + sell_value * 0.0005
    return {
        "filled": True,
        "return_pct": round((sell_value - buy_value - fees) / buy_value * 100, 4),
        "entry_date": str(frame.loc[entry_idx, "日期"])[:10],
        "exit_date": str(frame.loc[exit_idx, "日期"])[:10],
        "exit_reason": exit_reason,
        "hold_days": exit_idx - entry_idx,
        "risk_pct": round(risk_pct, 2),
        "open_gap_pct": round(gap, 2),
    }


def replay_stock(
    code: str,
    prices: pd.DataFrame,
    market: pd.DataFrame,
    sectors: dict[tuple[pd.Timestamp, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    frame = prices.sort_values("日期").reset_index(drop=True).copy()
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce")
    frame = frame.dropna(subset=["日期", "开盘", "最高", "最低", "收盘", "成交量"]).reset_index(drop=True)
    if len(frame) < 150:
        return []
    frame = calculate_indicators(frame, bench_df=market[["日期", "收盘"]])
    ma = _find_squeeze_signal_indices(
        frame, 0.12, 1.5, 55, use_macd_filter=True, use_bb_sqz=False,
        sqz_lookback=10, use_rs_filter=True, use_weekly_filter=True,
    )
    zp, _short, _debug = _find_tv_zp_signal_indices(frame)
    industry = str(frame.loc[0, "industry"] or "未知")
    output = []
    for idx in _paired_signal_indices(ma, zp):
        if idx < 120 or idx + 21 >= len(frame):
            continue
        signal_date = pd.Timestamp(frame.loc[idx, "日期"]).normalize()
        sector = sectors.get((signal_date, industry), {})
        close_5d = float(frame.loc[idx - 5, "收盘"])
        pct_5d = (float(frame.loc[idx, "收盘"]) / close_5d - 1) * 100 if close_5d > 0 else 0.0
        fit = stock_sector_fit(frame, idx, industry, sectors)
        pa = analyze_price_action(frame.iloc[max(0, idx - 259) : idx + 1].copy())
        plan = pa.get("pa_trade_plan") or {}
        eligible = (
            bool(sector.get("mainline"))
            and bool(fit["core"])
            and 2 <= pct_5d <= 10
            and float(pa.get("price_action_score") or 0) >= 60
            and str(plan.get("action") or "WAIT") != "AVOID"
        )
        if not eligible:
            continue
        confirmations = {
            "strict": find_first_pullback_confirmation(frame, idx),
            "practical": find_first_pullback_confirmation(
                frame,
                idx,
                wait_days=10,
                max_pullback_volume_ratio=1.0,
                min_confirmation_volume_ratio=1.0,
            ),
        }
        for arm, entry_mode, hold_days, profile in ARMS:
            anchor = idx if entry_mode == "signal" else confirmations[profile]
            if anchor is None:
                execution = {"filled": False, "return_pct": None, "reason": "未形成回踩确认"}
            else:
                structure_start = idx if entry_mode == "signal" else idx + 1
                structure_low = float(frame.loc[structure_start:anchor, "最低"].min())
                execution = simulate_atr_trade(
                    code,
                    frame,
                    anchor,
                    max_hold_days=hold_days,
                    max_open_gap_pct=3.0 if entry_mode == "signal" else 2.0,
                    structure_low=structure_low,
                )
            output.append(
                {
                    "code": code,
                    "signal_date": signal_date,
                    "industry": industry,
                    "arm": arm,
                    "sector_phase": sector.get("phase"),
                    "sector_rank": sector.get("rank"),
                    "sector_score": sector.get("score"),
                    "relative_5d": fit["relative_5d"],
                    "lead_consistency": fit["lead_consistency"],
                    "pct_5d": round(pct_5d, 2),
                    "pullback_confirmed": bool(profile and confirmations[profile] is not None),
                    "pullback_profile": profile,
                    **{f"exec_{key}": value for key, value in execution.items()},
                }
            )
    return output


def _metrics(rows: pd.DataFrame) -> dict[str, Any]:
    filled = rows[rows["exec_filled"] & rows["exec_return_pct"].notna()]
    returns = pd.to_numeric(filled["exec_return_pct"], errors="coerce").dropna()
    gains, losses = returns[returns > 0].sum(), abs(returns[returns < 0].sum())
    signal_count = len(rows[["code", "signal_date"]].drop_duplicates()) if not rows.empty else 0
    return {
        "signals": int(signal_count),
        "filled": int(len(returns)),
        "win_rate": round(float(returns.gt(0).mean()) * 100, 2) if len(returns) else 0.0,
        "avg_return": round(float(returns.mean()), 4) if len(returns) else 0.0,
        "median_return": round(float(returns.median()), 4) if len(returns) else 0.0,
        "profit_factor": round(float(gains / losses), 4) if losses > 0 else (99.0 if gains > 0 else None),
    }


def build_report(
    rows: pd.DataFrame,
    train_end: str = DEFAULT_TRAIN_END,
    validation_end: str = DEFAULT_VALIDATION_END,
) -> dict[str, Any]:
    if rows.empty:
        return {"status": "INSUFFICIENT_DATA", "selected_arm": None, "arms": {}}
    work = rows.copy()
    work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce")
    train_cutoff, validation_cutoff = pd.Timestamp(train_end), pd.Timestamp(validation_end)
    segments = {
        "train": work["signal_date"].le(train_cutoff),
        "validation": work["signal_date"].gt(train_cutoff) & work["signal_date"].le(validation_cutoff),
        "test": work["signal_date"].gt(validation_cutoff),
        "all": pd.Series(True, index=work.index),
    }
    arms = {}
    for arm, _mode, _hold, _profile in ARMS:
        arm_mask = work["arm"].eq(arm)
        arms[arm] = {name: _metrics(work[arm_mask & mask]) for name, mask in segments.items()}
    eligible = [
        arm for arm, _mode, _hold, _profile in ARMS
        if arms[arm]["train"]["filled"] >= 100
        and arms[arm]["validation"]["filled"] >= 50
        and arms[arm]["train"]["avg_return"] > 0
        and arms[arm]["validation"]["avg_return"] > 0
        and (arms[arm]["train"]["profit_factor"] or 0) >= 1.2
        and (arms[arm]["validation"]["profit_factor"] or 0) >= 1.2
    ]
    selected = max(
        eligible,
        key=lambda arm: (arms[arm]["validation"]["avg_return"], arms[arm]["validation"]["profit_factor"] or 0),
        default=None,
    )
    confirmed = False
    if selected:
        test = arms[selected]["test"]
        confirmed = (
            test["filled"] >= 300
            and test["avg_return"] >= 0.8
            and (test["profit_factor"] or 0) >= 1.4
        )
    return {
        # The practical profile was introduced after observing that the strict profile
        # produced no fills, so the current test period is no longer a pristine holdout.
        "status": "FRESH_HOLDOUT_REQUIRED" if confirmed else "SHADOW_ONLY",
        "selected_arm": selected,
        "test_confirmed": confirmed,
        "split": {"train_end": train_end, "validation_end": validation_end},
        "promotion_targets": {"test_filled": 300, "avg_return": 0.8, "profit_factor": 1.4},
        "arms": arms,
    }


def run_backtest(engine, *, start_date: str = DEFAULT_START_DATE, chunk_size: int = 200, max_codes: int | None = None) -> dict[str, Any]:
    market = _market_proxy(engine, start_date)
    sector_context = load_sector_context(engine, start_date)
    sectors = _sector_lookup(sector_context)
    codes = _load_codes(engine, start_date, max_codes)
    rows: list[dict[str, Any]] = []
    for code_chunk in _chunks(codes, max(1, int(chunk_size))):
        prices = _load_price_chunk(engine, code_chunk, start_date)
        for code, group in prices.groupby("code", sort=False):
            rows.extend(replay_stock(str(code), group, market, sectors))
    events = pd.DataFrame(rows)
    candidate_events = (
        len(events[["code", "signal_date"]].drop_duplicates()) if not events.empty else 0
    )
    return {
        "meta": {
            "version": BACKTEST_VERSION,
            "generated_on": date.today().isoformat(),
            "start_date": start_date,
            "codes_tested": len(codes),
            "candidate_events": candidate_events,
            "evaluated_event_arms": len(events),
            "entry": "signal next-open vs strict/practical first-pullback confirmation next-open",
            "exit": "4%-7% bounded structure stop, 3-day no-follow-through, ATR2.2 trail, 10/20-day max",
            "limitations": [
                "historical industry membership uses current stock_basic mapping",
                "daily OHLC uses conservative stop-first ordering",
                "no point-in-time fundamentals, market cap, or historical ST labels",
                "practical pullback profile is adaptive follow-up research and requires a fresh holdout",
            ],
        },
        "report": build_report(events),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest mainline-sector first-pullback strategy")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--max-codes", type=int)
    args = parser.parse_args()
    print(json.dumps(run_backtest(get_db_engine(), start_date=args.start_date, chunk_size=args.chunk_size, max_codes=args.max_codes), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
