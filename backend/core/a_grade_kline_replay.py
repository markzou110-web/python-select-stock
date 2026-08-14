"""Point-in-time K-line replay for calibrating TV strategy A-grade gates."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import bindparam, text

from core.db import get_db_engine
from core.execution_labels import evaluate_execution_path
from core.indicators import calculate_indicators
from core.price_action import analyze_price_action
from core.risk_constants import (
    BACKTEST_STOP_LOSS_PCT,
    MA_STRATEGY_TAKE_PROFIT_PCT,
    TV_MA_ONLY_MIN_PA_SCORE,
    ZP_PROFIT_PROTECT_TRIGGER_PCT,
)
from core.strategy import _find_squeeze_signal_indices, _find_tv_zp_signal_indices
from core.strategy_research_protocol import (
    build_dual_axis_history,
    build_walk_forward_report,
    load_causal_breadth_history,
    load_official_index_histories,
    select_frozen_test_events,
)
from core.strategy_portfolio_replay import compare_portfolio_configs


REPLAY_VERSION = "tv-or-kline-replay-v6-tiered-zp-profit-protect"
DEFAULT_START_DATE = "2022-05-12"
DEFAULT_TRAIN_END = "2024-12-31"
DEFAULT_VALIDATION_END = "2025-12-31"
RESEARCH_SLIPPAGE_BPS = 5.0


@dataclass(frozen=True)
class GateSpec:
    name: str
    min_pa_score: float = 0.0
    max_pct_5d: float | None = None
    min_raw_score: float = 0.0
    require_non_avoid: bool = False
    require_offensive_market: bool = False


# ponytail: this deliberately small ladder is the ceiling against grid-search overfitting.
GATE_SPECS = (
    GateSpec("strict_signal_baseline"),
    GateSpec("non_avoid", require_non_avoid=True),
    GateSpec("pa_55", min_pa_score=55, require_non_avoid=True),
    GateSpec("pa_60", min_pa_score=60, require_non_avoid=True),
    GateSpec("pa_65", min_pa_score=65, require_non_avoid=True),
    GateSpec("pa_60_chase_15", min_pa_score=60, max_pct_5d=15, require_non_avoid=True),
    GateSpec("pa_60_chase_10", min_pa_score=60, max_pct_5d=10, require_non_avoid=True),
    GateSpec("pa_60_raw_88", min_pa_score=60, min_raw_score=88, require_non_avoid=True),
    GateSpec("pa_60_offensive", min_pa_score=60, require_non_avoid=True, require_offensive_market=True),
    GateSpec(
        "pa_60_chase_15_offensive",
        min_pa_score=60,
        max_pct_5d=15,
        require_non_avoid=True,
        require_offensive_market=True,
    ),
)


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _paired_signal_indices(ma_indices: list[int], zp_indices: list[int], window: int = 3) -> list[int]:
    """Return one event date per MA/ZP pair within the live three-bar window."""
    paired = {
        max(ma_idx, zp_idx)
        for ma_idx in ma_indices
        for zp_idx in zp_indices
        if abs(ma_idx - zp_idx) < max(1, int(window))
    }
    return sorted(paired)


def _tv_signal_indices(
    ma_indices: list[int],
    zp_indices: list[int],
    *,
    require_both: bool,
) -> list[int]:
    if require_both:
        return _paired_signal_indices(ma_indices, zp_indices)
    return sorted(set(ma_indices) | set(zp_indices))


def _tv_signal_events(
    ma_indices: list[int],
    zp_indices: list[int],
    *,
    require_both: bool,
) -> list[tuple[int, frozenset[str]]]:
    if require_both:
        return [
            (idx, frozenset({"ma", "zp"}))
            for idx in _paired_signal_indices(ma_indices, zp_indices)
        ]
    sources_by_idx: dict[int, set[str]] = {}
    for idx in ma_indices:
        sources_by_idx.setdefault(idx, set()).add("ma")
    for idx in zp_indices:
        sources_by_idx.setdefault(idx, set()).add("zp")
    return [
        (idx, frozenset(sources_by_idx[idx]))
        for idx in sorted(sources_by_idx)
    ]


def _strategy_exit_candidate(
    frame: pd.DataFrame,
    *,
    signal_idx: int,
    entry_anchor_idx: int | None = None,
    sources: frozenset[str],
    zp_short_indices: list[int],
    planned_entry_price: float | None = None,
    ma_target_basis_price: float | None = None,
) -> tuple[int, str, float | None] | None:
    """Return the first executable strategy sell point after a next-open entry."""
    anchor_idx = signal_idx if entry_anchor_idx is None else int(entry_anchor_idx)
    entry_idx = anchor_idx + 1
    if entry_idx >= len(frame):
        return None
    candidates: list[tuple[int, str, float | None]] = []

    if "ma" in sources:
        first_open = float(frame.loc[entry_idx, "开盘"])
        planned_entry = float(planned_entry_price or 0)
        entry_raw = max(first_open, planned_entry) if planned_entry > 0 else first_open
        target_basis = float(ma_target_basis_price or 0) or entry_raw
        target = target_basis * (1 + MA_STRATEGY_TAKE_PROFIT_PCT / 100)
        target_pct_from_fill = (target / entry_raw - 1) * 100
        for idx in range(entry_idx + 1, len(frame)):
            if float(frame.loc[idx, "最高"]) >= target:
                candidates.append((
                    idx,
                    f"均线策略止盈信号(+{MA_STRATEGY_TAKE_PROFIT_PCT:g}%)",
                    target_pct_from_fill,
                ))
                break
        if "EMA20" in frame.columns:
            for idx in range(entry_idx, len(frame) - 1):
                ema20 = float(frame.loc[idx, "EMA20"] or 0)
                if ema20 > 0 and float(frame.loc[idx, "收盘"]) < ema20:
                    candidates.append((idx + 1, "均线策略EMA20破位卖点", None))
                    break

    if "zp" in sources:
        first_open = float(frame.loc[entry_idx, "开盘"])
        planned_entry = float(planned_entry_price or 0)
        entry_raw = max(first_open, planned_entry) if planned_entry > 0 else first_open
        profit_protect_active = False
        if "EMA20" in frame.columns:
            trigger_price = entry_raw * (1 + ZP_PROFIT_PROTECT_TRIGGER_PCT / 100)
            for idx in range(entry_idx, len(frame) - 1):
                profit_protect_active = profit_protect_active or float(frame.loc[idx, "最高"]) >= trigger_price
                ema20 = float(frame.loc[idx, "EMA20"] or 0)
                if profit_protect_active and ema20 > 0 and float(frame.loc[idx, "收盘"]) < ema20:
                    candidates.append((idx + 1, "TV-ZP盈利保护：+15%后EMA20破位卖点", None))
                    break
        for idx in zp_short_indices:
            if idx >= entry_idx and idx + 1 < len(frame):
                candidates.append((idx + 1, "TV-ZP short卖点", None))
                break

    return min(candidates, key=lambda item: item[0]) if candidates else None


def _evaluate_strategy_execution(
    code: str,
    frame: pd.DataFrame,
    *,
    signal_idx: int,
    entry_anchor_idx: int | None = None,
    sources: frozenset[str],
    zp_short_indices: list[int],
    planned_entry_price: float | None = None,
    planned_order_value: float | None = 5000.0,
    stop_loss_pct: float | None = BACKTEST_STOP_LOSS_PCT,
    max_open_gap_pct: float = 3.0,
    ma_target_basis_price: float | None = None,
) -> dict[str, Any]:
    """Execute at next open and exit only at the matching strategy sell point or stop."""
    anchor_idx = signal_idx if entry_anchor_idx is None else int(entry_anchor_idx)
    signal_close = float(frame.loc[anchor_idx, "收盘"])
    entry_idx = anchor_idx + 1
    if entry_idx >= len(frame):
        return {
            "mature": False,
            "filled": False,
            "return_pct": None,
            "exit_reason": "尚无下一交易日入场数据",
            "entry_idx": entry_idx,
            "exit_idx": None,
            "hold_days": None,
        }

    candidate = _strategy_exit_candidate(
        frame,
        signal_idx=signal_idx,
        entry_anchor_idx=anchor_idx,
        sources=sources,
        zp_short_indices=zp_short_indices,
        planned_entry_price=planned_entry_price,
        ma_target_basis_price=ma_target_basis_price,
    )
    last_idx = candidate[0] if candidate else len(frame) - 1
    future = frame.iloc[entry_idx : last_idx + 1].copy().reset_index(drop=True)
    if candidate and candidate[2] is None:
        # Sell signals are confirmed at the prior close, then filled at this open.
        exit_open = float(future.loc[len(future) - 1, "开盘"])
        for column in ("开盘", "最高", "最低", "收盘"):
            future.loc[len(future) - 1, column] = exit_open

    execution = evaluate_execution_path(
        code,
        signal_close,
        future,
        stop_loss_pct=-100.0 if stop_loss_pct is None else stop_loss_pct,
        take_profit_pct=candidate[2] if candidate else None,
        max_hold_days=len(future),
        max_open_gap_pct=max_open_gap_pct,
        planned_entry_price=planned_entry_price,
        slippage_bps=RESEARCH_SLIPPAGE_BPS,
        planned_order_value=planned_order_value,
    )
    result = dict(execution)
    if not result.get("filled"):
        result.update({"entry_idx": entry_idx, "exit_idx": None, "hold_days": None})
        return result

    exit_day = int(result.get("exit_day") or len(future))
    exit_idx = min(anchor_idx + exit_day, len(frame) - 1)
    result.update({
        "entry_idx": entry_idx,
        "exit_idx": exit_idx,
        "hold_days": max(0, exit_idx - entry_idx),
    })
    if result.get("exit_reason") == "固定止损":
        return result
    if candidate:
        result["exit_reason"] = candidate[1]
        return result

    # A filled position with no sell marker remains open; do not score an
    # artificial data-end liquidation as a completed trade.
    result.update({
        "mature": False,
        "return_pct": None,
        "exit_reason": "策略卖点尚未出现",
    })
    return result


def _market_proxy(engine, start_date: str) -> pd.DataFrame:
    query = text(
        """
        WITH priced AS (
            SELECT date, code, close,
                   LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev_close
            FROM daily_k
            WHERE date >= :start_date
        )
        SELECT date,
               AVG(close / NULLIF(prev_close, 0) - 1) AS market_return
        FROM priced
        WHERE prev_close > 0
          AND close / prev_close BETWEEN 0.75 AND 1.25
        GROUP BY date
        ORDER BY date
        """
    )
    market = pd.read_sql(query, engine, params={"start_date": start_date})
    market["market_return"] = pd.to_numeric(market["market_return"], errors="coerce").fillna(0.0)
    market["收盘"] = (1 + market["market_return"]).cumprod() * 1000
    market["日期"] = pd.to_datetime(market["date"], errors="coerce")
    market["EMA20"] = market["收盘"].ewm(span=20, adjust=False).mean()
    market["offensive"] = (market["收盘"] > market["EMA20"]) & (market["EMA20"].diff(5) > 0)
    return market[["日期", "收盘", "offensive"]].dropna(subset=["日期"])


def _load_codes(engine, start_date: str, max_codes: int | None) -> list[str]:
    query = text(
        """
        SELECT code
        FROM daily_k
        WHERE date >= :start_date
        GROUP BY code
        HAVING COUNT(*) >= 150
        ORDER BY code
        """
    )
    with engine.connect() as connection:
        codes = [str(row[0]) for row in connection.execute(query, {"start_date": start_date})]
    return codes[:max_codes] if max_codes else codes


def _load_price_chunk(engine, codes: list[str], start_date: str) -> pd.DataFrame:
    query = text(
        """
        SELECT d.code, d.date AS "日期", d.open AS "开盘", d.high AS "最高",
               d.low AS "最低", d.close AS "收盘", d.vol AS "成交量",
               COALESCE(b.name, '') AS name, COALESCE(b.industry, '未知') AS industry
        FROM daily_k d
        LEFT JOIN stock_basic b ON b.code = d.code
        WHERE d.code IN :codes AND d.date >= :start_date
        ORDER BY d.code, d.date
        """
    ).bindparams(bindparam("codes", expanding=True))
    return pd.read_sql(query, engine, params={"codes": codes, "start_date": start_date})


def _market_offensive_lookup(market: pd.DataFrame) -> dict[pd.Timestamp, bool]:
    return {
        pd.Timestamp(day).normalize(): bool(value)
        for day, value in zip(market["日期"], market["offensive"])
    }


def replay_stock(
    code: str,
    prices: pd.DataFrame,
    market: pd.DataFrame,
    *,
    require_both: bool = False,
    include_chart_comparison: bool = False,
) -> list[dict[str, Any]]:
    """Replay de-duplicated TV events using only bars available at each event."""
    if len(prices) < 150:
        return []
    frame = prices.sort_values("日期").reset_index(drop=True).copy()
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce")
    frame = frame.dropna(subset=["日期", "开盘", "最高", "最低", "收盘", "成交量"]).reset_index(drop=True)
    if len(frame) < 150:
        return []

    benchmark = market[["日期", "收盘"]].copy()
    frame = calculate_indicators(frame, bench_df=benchmark)
    ma_indices = _find_squeeze_signal_indices(
        frame,
        threshold=0.12,
        vol_multiplier=1.5,
        rsi_min=55,
        use_macd_filter=True,
        use_bb_sqz=False,
        sqz_lookback=10,
        use_rs_filter=True,
        use_weekly_filter=True,
    )
    zp_indices, zp_short_indices, _debug = _find_tv_zp_signal_indices(frame)
    market_lookup = _market_offensive_lookup(market)
    rows = []
    for idx, sources in _tv_signal_events(ma_indices, zp_indices, require_both=require_both):
        if idx < 120:
            continue
        signal_close = float(frame.loc[idx, "收盘"])
        prev_close = float(frame.loc[idx - 1, "收盘"])
        pct_change = (signal_close / prev_close - 1) * 100 if prev_close > 0 else 0.0
        vol_ma = float(frame.loc[idx, "Vol_MA20"] or 0)
        vol_ratio = float(frame.loc[idx, "成交量"]) / vol_ma if vol_ma > 0 else 0.0
        raw_score = 82 + min(vol_ratio, 3.0) * 4 + min(max(pct_change, 0.0), 5.0)
        close_5d = float(frame.loc[idx - 5, "收盘"])
        pct_5d = (signal_close / close_5d - 1) * 100 if close_5d > 0 else 0.0

        # Price action needs longer weekly context, but never any bar after the signal.
        history = frame.iloc[max(0, idx - 259) : idx + 1].copy()
        pa = analyze_price_action(history)
        plan = pa.get("pa_trade_plan") or {}
        execution = _evaluate_strategy_execution(
            code,
            frame,
            signal_idx=idx,
            sources=sources,
            zp_short_indices=zp_short_indices,
            planned_entry_price=float(pa.get("pa_entry_price") or 0) or None,
        )
        portfolio_execution = _evaluate_strategy_execution(
            code,
            frame,
            signal_idx=idx,
            sources=sources,
            zp_short_indices=zp_short_indices,
            planned_entry_price=float(pa.get("pa_entry_price") or 0) or None,
            planned_order_value=None,
        )
        chart_comparisons: dict[str, Any] = {}
        if include_chart_comparison:
            comparison_specs = {
                "chart_protected": BACKTEST_STOP_LOSS_PCT,
                "chart_points_only": None,
            }
            for prefix, comparison_stop in comparison_specs.items():
                comparison = _evaluate_strategy_execution(
                    code,
                    frame,
                    signal_idx=idx,
                    sources=sources,
                    zp_short_indices=zp_short_indices,
                    planned_entry_price=None,
                    planned_order_value=5000.0,
                    stop_loss_pct=comparison_stop,
                    max_open_gap_pct=100.0,
                    ma_target_basis_price=signal_close,
                )
                chart_comparisons.update({
                    f"{prefix}_exec_{key}": value
                    for key, value in comparison.items()
                })
        signal_date = pd.Timestamp(frame.loc[idx, "日期"]).normalize()
        entry_idx = execution.get("entry_idx")
        exit_idx = execution.get("exit_idx")
        entry_date = (
            pd.Timestamp(frame.loc[int(entry_idx), "日期"]).normalize()
            if entry_idx is not None and 0 <= int(entry_idx) < len(frame)
            else None
        )
        exit_date = (
            pd.Timestamp(frame.loc[int(exit_idx), "日期"]).normalize()
            if exit_idx is not None and 0 <= int(exit_idx) < len(frame)
            else None
        )
        portfolio_entry_idx = portfolio_execution.get("entry_idx")
        portfolio_exit_idx = portfolio_execution.get("exit_idx")
        rows.append(
            {
                "code": code,
                "signal_idx": idx,
                "name": str(frame.loc[idx, "name"] or ""),
                "industry": str(frame.loc[idx, "industry"] or "未知"),
                "signal_date": signal_date,
                "signal_close": signal_close,
                "planned_entry_price": float(pa.get("pa_entry_price") or 0) or None,
                "pa_stop_price": float(pa.get("pa_stop_price") or 0) or None,
                "signal_sources": "+".join(sorted(sources)),
                "tv_execution_tier": (
                    "A" if sources == frozenset({"ma", "zp"})
                    else "B" if "ma" in sources
                    else "C"
                ),
                "tv_execution_risk_unit": (
                    1.0 if sources == frozenset({"ma", "zp"})
                    else 0.6 if "ma" in sources
                    else 0.25
                ),
                "raw_score": round(raw_score, 2),
                "pct_5d": round(pct_5d, 2),
                "pa_score": float(pa.get("price_action_score") or 0),
                "pa_structure_score": float(pa.get("pa_structure_score") or 0),
                "pa_action": str(plan.get("action") or "WAIT"),
                "pa_setup": str(plan.get("setup") or ""),
                "market_offensive": market_lookup.get(signal_date, False),
                "exec_mature": bool(execution.get("mature")),
                "exec_filled": bool(execution.get("filled")),
                "exec_return_pct": execution.get("return_pct"),
                "exec_exit_reason": execution.get("exit_reason"),
                "exec_entry_idx": execution.get("entry_idx"),
                "exec_exit_idx": execution.get("exit_idx"),
                "exec_entry_date": entry_date,
                "exec_exit_date": exit_date,
                "exec_hold_days": execution.get("hold_days"),
                **{
                    f"portfolio_exec_{key}": value
                    for key, value in portfolio_execution.items()
                },
                "portfolio_exec_entry_date": (
                    pd.Timestamp(frame.loc[int(portfolio_entry_idx), "日期"]).normalize()
                    if portfolio_entry_idx is not None
                    and 0 <= int(portfolio_entry_idx) < len(frame)
                    else None
                ),
                "portfolio_exec_exit_date": (
                    pd.Timestamp(frame.loc[int(portfolio_exit_idx), "日期"]).normalize()
                    if portfolio_exit_idx is not None
                    and 0 <= int(portfolio_exit_idx) < len(frame)
                    else None
                ),
                **chart_comparisons,
            }
        )
    return rows


def _gate_mask(frame: pd.DataFrame, gate: GateSpec) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    if gate.require_non_avoid:
        mask &= frame["pa_action"].ne("AVOID")
    if gate.min_pa_score:
        mask &= frame["pa_score"].ge(gate.min_pa_score)
    if gate.max_pct_5d is not None:
        mask &= frame["pct_5d"].le(gate.max_pct_5d)
    if gate.min_raw_score:
        mask &= frame["raw_score"].ge(gate.min_raw_score)
    if gate.require_offensive_market:
        mask &= frame["market_offensive"].fillna(False)
    return mask


def _tiered_v11_candidate_mask(frame: pd.DataFrame) -> pd.Series:
    """Fixed production policy: A direct; B needs PA>=60/non-AVOID/offensive; C observes."""
    if "tv_execution_tier" in frame.columns:
        tier = frame["tv_execution_tier"].astype(str)
    else:
        sources = frame.get(
            "signal_sources",
            pd.Series("", index=frame.index, dtype="object"),
        ).astype(str)
        tier = sources.map({"ma+zp": "A", "ma": "B", "zp": "C"}).fillna("")
    qualified_b = (
        tier.eq("B")
        & frame["pa_action"].ne("AVOID")
        & frame["pa_score"].ge(TV_MA_ONLY_MIN_PA_SCORE)
        & frame["market_offensive"].fillna(False)
    )
    return tier.eq("A") | qualified_b


def _wilson_lower(wins: int, trials: int, z: float = 1.96) -> float:
    if trials <= 0:
        return 0.0
    p = wins / trials
    denominator = 1 + z * z / trials
    centre = p + z * z / (2 * trials)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * trials)) / trials)
    return (centre - margin) / denominator


def summarize_gate(frame: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    selected = frame[mask]
    filled = selected[selected["exec_filled"] & selected["exec_return_pct"].notna()]
    returns = pd.to_numeric(filled["exec_return_pct"], errors="coerce").dropna()
    hold_days = (
        pd.to_numeric(filled["exec_hold_days"], errors="coerce").dropna()
        if "exec_hold_days" in filled.columns
        else pd.Series(dtype=float)
    )
    wins = int((returns > 0).sum())
    losses = returns[returns < 0]
    gross_profit = float(returns[returns > 0].sum())
    gross_loss = abs(float(losses.sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (None if gross_profit == 0 else 99.0)
    return {
        "signals": int(len(selected)),
        "mature": int(selected["exec_mature"].sum()),
        "filled": int(len(returns)),
        "unique_codes": int(selected["code"].nunique()),
        "unique_dates": int(selected["signal_date"].nunique()),
        "win_rate": round(wins / len(returns) * 100, 2) if len(returns) else 0.0,
        "wilson_lower_95": round(_wilson_lower(wins, len(returns)) * 100, 2),
        "avg_return": round(float(returns.mean()), 4) if len(returns) else 0.0,
        "median_return": round(float(returns.median()), 4) if len(returns) else 0.0,
        "avg_hold_days": round(float(hold_days.mean()), 2) if len(hold_days) else 0.0,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "stop_rate": round(float((filled["exec_exit_reason"] == "固定止损").mean()) * 100, 2) if len(filled) else 0.0,
        "exit_reasons": {
            str(reason): int(count)
            for reason, count in filled["exec_exit_reason"].value_counts().sort_index().items()
        },
    }


def _independent_position_mask(frame: pd.DataFrame, candidate_mask: pd.Series) -> pd.Series:
    """Apply one-position-per-stock after a gate selects its own candidates."""
    selected = pd.Series(False, index=frame.index)
    required = {"code", "signal_idx", "exec_filled", "exec_exit_idx"}
    if not required.issubset(frame.columns):
        return candidate_mask.fillna(False).astype(bool)

    candidates = frame[candidate_mask.fillna(False)].sort_values(["code", "signal_idx"])
    for _code, group in candidates.groupby("code", sort=False):
        next_allowed_idx = -1
        for row_idx, row in group.iterrows():
            signal_idx = int(row["signal_idx"])
            if signal_idx < next_allowed_idx:
                continue
            selected.loc[row_idx] = True
            if not bool(row.get("exec_filled")):
                continue
            exit_idx = row.get("exec_exit_idx")
            if pd.isna(exit_idx):
                next_allowed_idx = 10**12
            else:
                next_allowed_idx = int(exit_idx) + 1
    return selected


def build_route_a_protocol_events(
    signals: pd.DataFrame,
    dual_axis_history: pd.DataFrame,
) -> pd.DataFrame:
    """Build the six pre-registered PA × permission-confirmation route-A arms."""
    if signals is None or signals.empty or dual_axis_history is None or dual_axis_history.empty:
        return pd.DataFrame()
    work = signals.copy()
    work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce").dt.normalize()
    permissions = dual_axis_history[
        [
            "date",
            "route_a_permission_1d",
            "route_a_permission_2d",
            *[
                column
                for column in ("index_axis", "breadth_axis", "breadth_stage")
                if column in dual_axis_history.columns
            ],
        ]
    ].copy()
    permissions["date"] = pd.to_datetime(permissions["date"], errors="coerce").dt.normalize()
    work = work.merge(permissions, left_on="signal_date", right_on="date", how="left")
    variants: list[pd.DataFrame] = []
    for pa_threshold in (55, 60, 65):
        quality = (
            work["pa_action"].ne("AVOID")
            & work["pa_score"].ge(pa_threshold)
            & work["pct_5d"].le(15)
        )
        for confirmation_days in (1, 2):
            permission_col = f"route_a_permission_{confirmation_days}d"
            candidate = quality & work[permission_col].eq("CONFIRM")
            selected = _independent_position_mask(work, candidate)
            arm = work[selected].copy()
            if arm.empty:
                continue
            arm["variant"] = f"pa{pa_threshold}_confirm{confirmation_days}d"
            arm["min_pa_score"] = pa_threshold
            arm["permission_confirmation_days"] = confirmation_days
            arm["exit_date"] = pd.to_datetime(arm["exec_exit_date"], errors="coerce")
            variants.append(arm)
    return pd.concat(variants, ignore_index=True) if variants else pd.DataFrame()


def build_route_a_protocol_report(
    signals: pd.DataFrame,
    dual_axis_history: pd.DataFrame,
    *,
    start_date: str,
) -> dict[str, Any]:
    variants = tuple(
        f"pa{pa_threshold}_confirm{confirmation_days}d"
        for pa_threshold in (55, 60, 65)
        for confirmation_days in (1, 2)
    )
    events = build_route_a_protocol_events(signals, dual_axis_history)
    end_date = (
        pd.to_datetime(dual_axis_history["date"], errors="coerce").max()
        if not dual_axis_history.empty
        else None
    )
    report = build_walk_forward_report(
        events,
        variants=variants,
        start_date=start_date,
        end_date=end_date.strftime("%Y-%m-%d") if pd.notna(end_date) else None,
    )
    report["protocol_variants"] = list(variants)
    report["eligible_event_rows"] = int(len(events))
    report["market_state_days"] = {
        f"{trend}_{width}": int(count)
        for (trend, width), count in dual_axis_history.groupby(
            ["index_axis", "breadth_axis"]
        ).size().items()
    } if not dual_axis_history.empty else {}
    return report


def prepare_route_a_portfolio_events(selected_events: pd.DataFrame) -> pd.DataFrame:
    """Map scalable A-EOD executions into the shared portfolio replay contract."""
    if selected_events is None or selected_events.empty:
        return pd.DataFrame()
    required = {
        "portfolio_exec_filled",
        "portfolio_exec_entry_date",
        "portfolio_exec_entry_price",
        "portfolio_exec_exit_date",
        "portfolio_exec_exit_price",
        "portfolio_test_end",
    }
    if not required.issubset(selected_events.columns):
        return pd.DataFrame()
    work = selected_events[selected_events["portfolio_exec_filled"].fillna(False)].copy()
    if work.empty:
        return work
    entry = pd.to_numeric(work["portfolio_exec_entry_price"], errors="coerce")
    # X1 currently executes only the fixed -9% hard stop. Using a tighter PA
    # structure stop for sizing would understate account risk until X2 exists.
    work["stop_price"] = entry / (1 + RESEARCH_SLIPPAGE_BPS / 10_000) * 0.91
    work["entry_date"] = pd.to_datetime(work["portfolio_exec_entry_date"], errors="coerce")
    work["exit_date"] = pd.to_datetime(work["portfolio_exec_exit_date"], errors="coerce")
    work["entry_price"] = entry
    work["exit_price"] = pd.to_numeric(work["portfolio_exec_exit_price"], errors="coerce")
    overall_test_end = pd.to_datetime(
        work["portfolio_test_end"],
        errors="coerce",
    ).max()
    after_window = work["exit_date"].isna() | work["exit_date"].gt(overall_test_end)
    work.loc[after_window, ["exit_date", "exit_price"]] = [pd.NaT, float("nan")]
    work["score"] = pd.to_numeric(
        work.get("pa_score", pd.Series(index=work.index, dtype=float)),
        errors="coerce",
    ).fillna(0)
    raw_score = pd.to_numeric(
        work.get("raw_score", pd.Series(index=work.index, dtype=float)),
        errors="coerce",
    ).fillna(0)
    work["r0_single_capital_pct"] = 0.0
    work.loc[raw_score.ge(60) & raw_score.lt(70), "r0_single_capital_pct"] = 5.0
    work.loc[raw_score.ge(70) & raw_score.lt(80), "r0_single_capital_pct"] = 10.0
    work.loc[raw_score.ge(80) & raw_score.lt(90), "r0_single_capital_pct"] = 15.0
    work.loc[raw_score.ge(90), "r0_single_capital_pct"] = 20.0
    breadth_stage = work.get(
        "breadth_stage",
        pd.Series("UNKNOWN", index=work.index, dtype="object"),
    ).astype(str)
    work["market_capital_pct"] = breadth_stage.map(
        {
            "ADVANCE": 70.0,
            "CLIMAX": 50.0,
            "DIVERGENCE": 40.0,
            "REPAIR": 40.0,
            "V_REPAIR": 30.0,
            "RETREAT": 10.0,
            "ICE": 15.0,
        }
    ).fillna(0.0)
    return work


def run_route_a_portfolio_protocol(
    engine,
    protocol_events: pd.DataFrame,
    walk_forward_report: dict[str, Any],
    benchmark: pd.DataFrame | None = None,
) -> dict[str, Any]:
    selected = select_frozen_test_events(protocol_events, walk_forward_report)
    portfolio_events = prepare_route_a_portfolio_events(selected)
    if portfolio_events.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "selected_config": None,
            "reason": "没有通过训练/验证门槛的冻结测试事件，不能运行R0/R1/R2",
        }
    codes = sorted(portfolio_events["code"].astype(str).unique())
    start_date = pd.to_datetime(portfolio_events["entry_date"], errors="coerce").min()
    end_date = pd.to_datetime(portfolio_events["portfolio_test_end"], errors="coerce").max()
    query = text(
        """
        SELECT code, date, close, vol AS volume
        FROM daily_k
        WHERE code IN :codes AND date BETWEEN :start_date AND :end_date
        ORDER BY date, code
        """
    ).bindparams(bindparam("codes", expanding=True))
    prices = pd.read_sql(
        query,
        engine,
        params={
            "codes": codes,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
        },
    )
    return compare_portfolio_configs(
        portfolio_events,
        prices,
        benchmark=benchmark,
    )


def run_route_a_research_protocol(
    engine,
    signals: pd.DataFrame,
    market: pd.DataFrame,
    *,
    start_date: str,
) -> dict[str, Any]:
    """Run official-index dual-axis research without changing production permission."""
    try:
        breadth = load_causal_breadth_history(
            engine,
            start_date=start_date,
            offensive_lookup=_market_offensive_lookup(market),
        )
        sh_index, cyb_index = load_official_index_histories()
        dual_axis = build_dual_axis_history(
            sh_index,
            cyb_index,
            breadth,
            start_date=start_date,
        )
    except Exception as exc:
        return {
            "status": "DATA_UNAVAILABLE",
            "historical_thresholds_met": False,
            "reason": f"官方指数或因果宽度加载失败: {type(exc).__name__}",
        }
    if dual_axis.empty:
        return {
            "status": "DATA_UNAVAILABLE",
            "historical_thresholds_met": False,
            "reason": "官方双指数与因果宽度没有可对齐的交易日",
        }
    report = build_route_a_protocol_report(signals, dual_axis, start_date=start_date)
    protocol_events = build_route_a_protocol_events(signals, dual_axis)
    report["portfolio"] = run_route_a_portfolio_protocol(
        engine,
        protocol_events,
        report,
        benchmark=dual_axis[["date", "sh_close"]].rename(
            columns={"sh_close": "close"}
        ),
    )
    return report


def build_gate_report(
    signals: pd.DataFrame,
    *,
    train_end: str = DEFAULT_TRAIN_END,
    validation_end: str = DEFAULT_VALIDATION_END,
) -> dict[str, Any]:
    if signals.empty:
        return {"status": "INSUFFICIENT_DATA", "gates": {}, "selected_gate": None}
    work = signals.copy()
    work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce")
    train_cutoff = pd.Timestamp(train_end)
    validation_cutoff = pd.Timestamp(validation_end)
    segments = {
        "train": work["signal_date"].le(train_cutoff),
        "validation": work["signal_date"].gt(train_cutoff) & work["signal_date"].le(validation_cutoff),
        "test": work["signal_date"].gt(validation_cutoff),
        "all": pd.Series(True, index=work.index),
    }
    gates = {}
    for gate in GATE_SPECS:
        gate_mask = _independent_position_mask(work, _gate_mask(work, gate))
        gates[gate.name] = {
            segment: summarize_gate(work, gate_mask & segment_mask)
            for segment, segment_mask in segments.items()
        }
    tiered_mask = _independent_position_mask(work, _tiered_v11_candidate_mask(work))
    gates["tiered_v11"] = {
        segment: summarize_gate(work, tiered_mask & segment_mask)
        for segment, segment_mask in segments.items()
    }

    eligible = []
    for gate in GATE_SPECS[1:]:
        train = gates[gate.name]["train"]
        validation = gates[gate.name]["validation"]
        if (
            train["filled"] >= 50
            and validation["filled"] >= 30
            and train["avg_return"] > 0
            and validation["avg_return"] > 0
            and (train["profit_factor"] or 0) >= 1
            and (validation["profit_factor"] or 0) >= 1
        ):
            eligible.append(gate.name)
    selected = max(
        eligible,
        key=lambda name: (
            gates[name]["validation"]["avg_return"],
            gates[name]["validation"]["profit_factor"] or 0,
            gates[name]["validation"]["wilson_lower_95"],
        ),
        default=None,
    )
    status = "NO_SUPPORTED_GATE"
    fixed_split_support = False
    e3_blockers: list[str] = []
    if selected:
        selected_test = gates[selected]["test"]
        fixed_split_support = (
            selected_test["filled"] >= 100
            and selected_test["avg_return"] > 0
            and (selected_test["profit_factor"] or 0) >= 1.3
        )
        if selected_test["filled"] < 100:
            e3_blockers.append(f"研究验证段实际成交仅{selected_test['filled']}笔，少于100笔")
        if not fixed_split_support:
            e3_blockers.append("固定分段尚未同时满足样本数、平均收益和盈亏因子门槛")
        e3_blockers.extend(
            [
                "24/6/3月走查前推尚未执行",
                "不少于3个滚动样本外正期望窗口尚未验证",
                "异常赢家贡献和组合最大回撤尚未验证",
            ]
        )
        status = "E3_NOT_REACHED"
    return {
        "status": status,
        "provisional_gate": selected,
        "selected_gate": selected,
        # Kept for report consumers: fixed-split research cannot confirm E3.
        "test_confirmation": False,
        "fixed_split_support": fixed_split_support,
        "e3_blockers": e3_blockers,
        "split": {"train_end": train_end, "validation_end": validation_end},
        "selection_rule": (
            "provisional trade-level rank only: train>=50 and validation>=30 filled; "
            "both avg>0 and PF>=1; maximize validation avg return, then PF and Wilson lower"
        ),
        "fixed_split_rule": "research segment>=100 filled, avg>0, PF>=1.3",
        "e3_rule": "fixed-split thresholds plus 24/6/3 walk-forward, >=3 positive OOS windows, outlier and portfolio drawdown checks",
        "gates": gates,
    }


def run_kline_replay(
    engine,
    *,
    start_date: str = DEFAULT_START_DATE,
    train_end: str = DEFAULT_TRAIN_END,
    validation_end: str = DEFAULT_VALIDATION_END,
    chunk_size: int = 200,
    max_codes: int | None = None,
    require_both: bool = False,
    include_research_protocol: bool = True,
) -> dict[str, Any]:
    market = _market_proxy(engine, start_date)
    codes = _load_codes(engine, start_date, max_codes)
    rows: list[dict[str, Any]] = []
    for code_chunk in _chunks(codes, max(1, int(chunk_size))):
        prices = _load_price_chunk(engine, code_chunk, start_date)
        for code, group in prices.groupby("code", sort=False):
            rows.extend(replay_stock(str(code), group, market, require_both=require_both))
    signals = pd.DataFrame(rows)
    gate_report = build_gate_report(signals, train_end=train_end, validation_end=validation_end)
    research_protocol = (
        run_route_a_research_protocol(
            engine,
            signals,
            market,
            start_date=start_date,
        )
        if include_research_protocol
        else {"status": "SKIPPED"}
    )
    return {
        "meta": {
            "replay_version": REPLAY_VERSION,
            "generated_on": date.today().isoformat(),
            "start_date": start_date,
            "codes_tested": len(codes),
            "signals": len(signals),
            "strategy": (
                "tv_dual_strict paired MA/ZP events"
                if require_both
                else "tv_dual MA-or-ZP union events"
            ),
            "entry_model": (
                "T close signal; T+1 confirmation-price trigger, max 3% open extension, "
                "5bp slippage, A-share fees, -9% stop; "
                "exit on matching MA +15%/EMA20-break or TV-ZP short marker"
            ),
            "point_in_time_fields": ["OHLCV", "derived market proxy", "RS", "weekly trend", "price action"],
            "excluded_non_point_in_time_fields": ["fundamentals", "historical market cap", "historical ST status"],
            "limitations": [
                "current stock_basic industry/name metadata may contain survivorship changes",
                "signal events are de-duplicated; consecutive daily re-alerts are not independent samples",
                "each gate allows one position per stock and ignores its own buy markers while that position is open",
                "gate state is applied after filtering; signals rejected by one gate do not block its later signals",
                "daily OHLC cannot reveal whether the entry-day low occurred before or after a confirmation trigger",
                "positions without a strategy sell marker remain open and are excluded from completed-trade returns",
                "daily OHLC cannot reveal intraday stop/target order, so stop-first execution is conservative",
            ],
        },
        "report": gate_report,
        "research_protocol": research_protocol,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay TV MA-or-ZP candidates from historical daily K-lines")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--train-end", default=DEFAULT_TRAIN_END)
    parser.add_argument("--validation-end", default=DEFAULT_VALIDATION_END)
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--max-codes", type=int)
    parser.add_argument("--strict", action="store_true", help="研究对照：仅回放 MA 与 TV-ZP 配对事件")
    parser.add_argument("--skip-research-protocol", action="store_true")
    args = parser.parse_args()
    report = run_kline_replay(
        get_db_engine(),
        start_date=args.start_date,
        train_end=args.train_end,
        validation_end=args.validation_end,
        chunk_size=args.chunk_size,
        max_codes=args.max_codes,
        require_both=args.strict,
        include_research_protocol=not args.skip_research_protocol,
    )
    print(json.dumps(report, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
