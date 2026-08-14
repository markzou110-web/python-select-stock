"""Independent shadow replay for route B strong-trend pullback entries."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from core.a_grade_kline_replay import (
    DEFAULT_START_DATE,
    DEFAULT_TRAIN_END,
    DEFAULT_VALIDATION_END,
    _chunks,
    _evaluate_strategy_execution,
    _independent_position_mask,
    _load_codes,
    _load_price_chunk,
    _market_offensive_lookup,
    _market_proxy,
    _paired_signal_indices,
)
from core.db import get_db_engine
from core.indicators import calculate_indicators
from core.price_action import analyze_price_action
from core.strategy import _find_squeeze_signal_indices, _find_tv_zp_signal_indices
from core.strategy_research_protocol import (
    build_dual_axis_history,
    build_walk_forward_report,
    load_causal_breadth_history,
    load_official_index_histories,
)


REPLAY_VERSION = "strong-trend-pullback-v1-p0-p1-skip"
ARMS = ("P0_DIRECT", "P1_PULLBACK", "SKIP")
B0_STAGES = frozenset({"RETREAT", "ICE"})


@dataclass(frozen=True)
class PullbackPlanOutcome:
    status: str
    reason: str
    mature: bool
    confirmation_idx: int | None = None
    wait_days: int | None = None


def evaluate_p1_plan(
    frame: pd.DataFrame,
    *,
    signal_idx: int,
    confirmation_price: float,
    invalidation_price: float,
    market_stage_lookup: dict[pd.Timestamp, str] | None = None,
    cancel_signal_indices: set[int] | None = None,
    wait_days: int = 3,
    band_pct: float = 1.0,
) -> PullbackPlanOutcome:
    """Evaluate the pre-registered 1-3 day pullback plan without future filling."""
    if (
        confirmation_price <= 0
        or invalidation_price <= 0
        or invalidation_price >= confirmation_price
        or signal_idx < 0
        or signal_idx >= len(frame)
    ):
        return PullbackPlanOutcome("CANCELLED", "计划价格无效", True)

    signal_volume = float(frame.loc[signal_idx, "成交量"] or 0)
    if signal_volume <= 0:
        return PullbackPlanOutcome("CANCELLED", "信号日成交量无效", True)
    band = max(0.0, float(band_pct)) / 100
    lower, upper = confirmation_price * (1 - band), confirmation_price * (1 + band)
    last_required_idx = signal_idx + max(1, int(wait_days))
    last_available_idx = min(last_required_idx, len(frame) - 1)
    stages = market_stage_lookup or {}
    cancel_indices = cancel_signal_indices or set()

    for idx in range(signal_idx + 1, last_available_idx + 1):
        signal_date = pd.Timestamp(frame.loc[idx, "日期"]).normalize()
        if str(stages.get(signal_date) or "").upper() in B0_STAGES:
            return PullbackPlanOutcome("CANCELLED", "市场宽度B0", True, wait_days=idx - signal_idx)
        if idx in cancel_indices:
            return PullbackPlanOutcome("CANCELLED", "等待期先出现策略卖点", True, wait_days=idx - signal_idx)

        low = float(frame.loc[idx, "最低"])
        close = float(frame.loc[idx, "收盘"])
        volume = float(frame.loc[idx, "成交量"] or 0)
        if low < invalidation_price:
            return PullbackPlanOutcome("CANCELLED", "结构失效", True, wait_days=idx - signal_idx)
        if lower <= low <= upper and close > confirmation_price and signal_volume > 0 and volume <= signal_volume:
            return PullbackPlanOutcome(
                "PULLBACK_CONFIRMED",
                "回踩确认",
                True,
                confirmation_idx=idx,
                wait_days=idx - signal_idx,
            )

    if last_available_idx < last_required_idx:
        return PullbackPlanOutcome("PENDING_DATA", "等待窗口数据不足", False)
    return PullbackPlanOutcome("EXPIRED", "3个交易日内未确认", True, wait_days=max(1, int(wait_days)))


def _empty_execution(*, mature: bool, reason: str) -> dict[str, Any]:
    return {
        "mature": mature,
        "filled": False,
        "return_pct": None,
        "exit_reason": reason,
        "entry_idx": None,
        "exit_idx": None,
        "hold_days": None,
    }


def _matching_cancel_indices(
    frame: pd.DataFrame,
    *,
    signal_idx: int,
    sources: frozenset[str],
    zp_short_indices: list[int],
) -> set[int]:
    indices = {idx for idx in zp_short_indices if "zp" in sources and idx > signal_idx}
    if "ma" in sources and "EMA20" in frame.columns:
        for idx in range(signal_idx + 1, len(frame)):
            ema20 = float(frame.loc[idx, "EMA20"] or 0)
            if ema20 > 0 and float(frame.loc[idx, "收盘"]) < ema20:
                indices.add(idx)
    return indices


def build_event_arm_rows(
    code: str,
    frame: pd.DataFrame,
    *,
    signal_idx: int,
    sources: frozenset[str],
    zp_short_indices: list[int],
    confirmation_price: float,
    invalidation_price: float,
    market_stage_lookup: dict[pd.Timestamp, str] | None = None,
) -> list[dict[str, Any]]:
    """Build P0/P1/SKIP rows from the same overextended signal event."""
    stages = market_stage_lookup or {}
    signal_date = pd.Timestamp(frame.loc[signal_idx, "日期"]).normalize()
    signal_stage = str(stages.get(signal_date) or "").upper()
    if signal_stage in B0_STAGES:
        p0 = _empty_execution(mature=True, reason="市场宽度B0")
    else:
        p0 = _evaluate_strategy_execution(
            code,
            frame,
            signal_idx=signal_idx,
            sources=sources,
            zp_short_indices=zp_short_indices,
        )

    plan = evaluate_p1_plan(
        frame,
        signal_idx=signal_idx,
        confirmation_price=confirmation_price,
        invalidation_price=invalidation_price,
        market_stage_lookup=stages,
        cancel_signal_indices=_matching_cancel_indices(
            frame,
            signal_idx=signal_idx,
            sources=sources,
            zp_short_indices=zp_short_indices,
        ),
    )
    if plan.confirmation_idx is None:
        p1 = _empty_execution(mature=plan.mature, reason=plan.reason)
    else:
        p1 = _evaluate_strategy_execution(
            code,
            frame,
            signal_idx=signal_idx,
            entry_anchor_idx=plan.confirmation_idx,
            sources=sources,
            zp_short_indices=zp_short_indices,
        )

    base = {"code": code, "signal_idx": signal_idx, "signal_date": signal_date}

    def row(arm: str, outcome: PullbackPlanOutcome | None, execution: dict[str, Any]) -> dict[str, Any]:
        entry_idx = execution.get("entry_idx")
        exit_idx = execution.get("exit_idx")
        result = {
            **base,
            "arm": arm,
            "plan_status": outcome.status if outcome else arm,
            "plan_reason": outcome.reason if outcome else ("直接追价" if arm == "P0_DIRECT" else "策略跳过"),
            "plan_confirmation_idx": outcome.confirmation_idx if outcome else None,
            "plan_wait_days": outcome.wait_days if outcome else 0,
            **{f"exec_{key}": value for key, value in execution.items()},
            "exec_entry_date": (
                pd.Timestamp(frame.loc[int(entry_idx), "日期"]).normalize()
                if entry_idx is not None and 0 <= int(entry_idx) < len(frame)
                else None
            ),
            "exec_exit_date": (
                pd.Timestamp(frame.loc[int(exit_idx), "日期"]).normalize()
                if exit_idx is not None and 0 <= int(exit_idx) < len(frame)
                else None
            ),
        }
        result["exec_exit_reason"] = execution.get("exit_reason") or execution.get("reason")
        return result

    return [
        row("P0_DIRECT", None, p0),
        row("P1_PULLBACK", plan, p1),
        row("SKIP", None, _empty_execution(mature=True, reason="策略跳过")),
    ]


def _load_market_stage_lookup(
    engine,
    start_date: str,
    market: pd.DataFrame,
) -> dict[pd.Timestamp, str]:
    """Build causal breadth stages from daily cross-sectional returns."""
    breadth = load_causal_breadth_history(
        engine,
        start_date=start_date,
        offensive_lookup=_market_offensive_lookup(market),
    )
    if breadth.empty:
        return {}
    return {
        pd.Timestamp(day).normalize(): str(stage)
        for day, stage in zip(breadth["date"], breadth["market_sentiment_stage"])
    }


def replay_stock(
    code: str,
    prices: pd.DataFrame,
    market: pd.DataFrame,
    market_stage_lookup: dict[pd.Timestamp, str],
) -> list[dict[str, Any]]:
    """Replay strict dual signals with >15% five-day gain as route B events."""
    if len(prices) < 150:
        return []
    frame = prices.sort_values("日期").reset_index(drop=True).copy()
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce")
    frame = frame.dropna(subset=["日期", "开盘", "最高", "最低", "收盘", "成交量"]).reset_index(drop=True)
    if len(frame) < 150:
        return []

    frame = calculate_indicators(frame, bench_df=market[["日期", "收盘"]].copy())
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
    rows: list[dict[str, Any]] = []
    for idx in _paired_signal_indices(ma_indices, zp_indices):
        if idx < 120:
            continue
        close_5d = float(frame.loc[idx - 5, "收盘"])
        pct_5d = (float(frame.loc[idx, "收盘"]) / close_5d - 1) * 100 if close_5d > 0 else 0.0
        if pct_5d <= 15:
            continue

        pa = analyze_price_action(frame.iloc[max(0, idx - 259) : idx + 1].copy())
        plan = pa.get("pa_trade_plan") or {}
        confirmation_price = float(pa.get("pa_entry_price") or 0)
        pa_stop = float(pa.get("pa_stop_price") or 0)
        invalidation_price = max(float(frame.loc[idx, "最低"]), pa_stop)
        if (
            float(pa.get("price_action_score") or 0) < 60
            or str(plan.get("action") or "WAIT") == "AVOID"
            or confirmation_price <= 0
            or invalidation_price <= 0
        ):
            continue

        event_rows = build_event_arm_rows(
            code,
            frame,
            signal_idx=idx,
            sources=frozenset({"ma", "zp"}),
            zp_short_indices=zp_short_indices,
            confirmation_price=confirmation_price,
            invalidation_price=invalidation_price,
            market_stage_lookup=market_stage_lookup,
        )
        for event in event_rows:
            event.update(
                {
                    "name": str(frame.loc[idx, "name"] or ""),
                    "industry": str(frame.loc[idx, "industry"] or "未知"),
                    "signal_sources": "ma+zp",
                    "pct_5d": round(pct_5d, 2),
                    "pa_score": float(pa.get("price_action_score") or 0),
                    "confirmation_price": confirmation_price,
                    "invalidation_price": invalidation_price,
                    "market_stage": str(market_stage_lookup.get(pd.Timestamp(frame.loc[idx, "日期"]).normalize()) or "UNKNOWN"),
                }
            )
        rows.extend(event_rows)
    return rows


def build_route_b_protocol_events(
    events: pd.DataFrame,
    dual_axis_history: pd.DataFrame,
) -> pd.DataFrame:
    """Build independent P0/P1 arms only where route B may shadow pullbacks."""
    if events is None or events.empty or dual_axis_history is None or dual_axis_history.empty:
        return pd.DataFrame()
    work = events.copy()
    work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce").dt.normalize()
    permissions = dual_axis_history[["date", "route_b_permission"]].copy()
    permissions["date"] = pd.to_datetime(permissions["date"], errors="coerce").dt.normalize()
    work = work.merge(permissions, left_on="signal_date", right_on="date", how="left")
    variants: list[pd.DataFrame] = []
    for arm in ("P0_DIRECT", "P1_PULLBACK"):
        candidate = work["arm"].eq(arm) & work["route_b_permission"].eq("SHADOW_PULLBACK")
        selected = _independent_position_mask(work, candidate)
        variant = work[selected].copy()
        if variant.empty:
            continue
        variant["variant"] = arm
        variant["exit_date"] = pd.to_datetime(variant["exec_exit_date"], errors="coerce")
        variants.append(variant)
    return pd.concat(variants, ignore_index=True) if variants else pd.DataFrame()


def build_route_b_protocol_report(
    events: pd.DataFrame,
    dual_axis_history: pd.DataFrame,
    *,
    start_date: str,
) -> dict[str, Any]:
    protocol_events = build_route_b_protocol_events(events, dual_axis_history)
    end_date = pd.to_datetime(dual_axis_history["date"], errors="coerce").max()
    report = build_walk_forward_report(
        protocol_events,
        variants=("P0_DIRECT", "P1_PULLBACK"),
        start_date=start_date,
        end_date=end_date.strftime("%Y-%m-%d") if pd.notna(end_date) else None,
    )
    report["eligible_event_rows"] = int(len(protocol_events))
    report["market_state_days"] = {
        f"{trend}_{width}": int(count)
        for (trend, width), count in dual_axis_history.groupby(
            ["index_axis", "breadth_axis"]
        ).size().items()
    } if not dual_axis_history.empty else {}
    return report


def run_route_b_research_protocol(
    engine,
    events: pd.DataFrame,
    market: pd.DataFrame,
    *,
    start_date: str,
) -> dict[str, Any]:
    """Run route-B walk-forward research; it never grants production permission."""
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
    return build_route_b_protocol_report(events, dual_axis, start_date=start_date)


def _profit_factor(returns: pd.Series) -> float | None:
    gains = float(returns[returns > 0].sum())
    losses = abs(float(returns[returns < 0].sum()))
    if losses > 0:
        return gains / losses
    return 99.0 if gains > 0 else None


def _summarize(frame: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    selected = frame[mask]
    completed = selected[selected["exec_filled"] & selected["exec_return_pct"].notna()]
    returns = pd.to_numeric(completed["exec_return_pct"], errors="coerce").dropna()
    wait_days = pd.to_numeric(selected["plan_wait_days"], errors="coerce").dropna()
    confirmed = int(selected["plan_status"].eq("PULLBACK_CONFIRMED").sum())
    profit_factor = _profit_factor(returns)
    return {
        "signals": int(len(selected)),
        "confirmed": confirmed,
        "confirmation_rate": round(confirmed / len(selected) * 100, 2) if len(selected) else 0.0,
        "mature": int(selected["exec_mature"].fillna(False).sum()),
        "filled": int(selected["exec_filled"].fillna(False).sum()),
        "completed": int(len(returns)),
        "open_positions": int((selected["exec_filled"] & selected["exec_return_pct"].isna()).sum()),
        "fill_rate": round(float(selected["exec_filled"].mean()) * 100, 2) if len(selected) else 0.0,
        "win_rate": round(float(returns.gt(0).mean()) * 100, 2) if len(returns) else 0.0,
        "avg_return": round(float(returns.mean()), 4) if len(returns) else 0.0,
        "median_return": round(float(returns.median()), 4) if len(returns) else 0.0,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "avg_wait_days": round(float(wait_days.mean()), 2) if len(wait_days) else 0.0,
        "stop_rate": round(float(completed["exec_exit_reason"].eq("固定止损").mean()) * 100, 2) if len(completed) else 0.0,
        "reasons": {
            str(reason): int(count)
            for reason, count in selected["exec_exit_reason"].fillna("未知").value_counts().sort_index().items()
        },
    }


def build_report(
    rows: pd.DataFrame,
    *,
    train_end: str = DEFAULT_TRAIN_END,
    validation_end: str = DEFAULT_VALIDATION_END,
) -> dict[str, Any]:
    if rows.empty:
        return {"status": "INSUFFICIENT_DATA", "arms": {}, "fixed_split_p1_support": False}
    work = rows.copy()
    work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce")
    train_cutoff, validation_cutoff = pd.Timestamp(train_end), pd.Timestamp(validation_end)
    segments = {
        "train": work["signal_date"].le(train_cutoff),
        "validation": work["signal_date"].gt(train_cutoff) & work["signal_date"].le(validation_cutoff),
        "test": work["signal_date"].gt(validation_cutoff),
        "all": pd.Series(True, index=work.index),
    }
    arms: dict[str, Any] = {}
    for arm in ARMS:
        arm_mask = _independent_position_mask(work, work["arm"].eq(arm))
        arms[arm] = {
            segment: _summarize(work, arm_mask & segment_mask)
            for segment, segment_mask in segments.items()
        }

    p0_validation, p1_validation = arms["P0_DIRECT"]["validation"], arms["P1_PULLBACK"]["validation"]
    p0_test, p1_test = arms["P0_DIRECT"]["test"], arms["P1_PULLBACK"]["test"]
    fixed_support = (
        p1_validation["completed"] >= 30
        and p1_validation["avg_return"] > p0_validation["avg_return"]
        and (p1_validation["profit_factor"] or 0) >= 1.2
        and p1_test["completed"] >= 100
        and p1_test["avg_return"] > p0_test["avg_return"]
        and p1_test["avg_return"] > 0
        and (p1_test["profit_factor"] or 0) >= 1.3
    )
    blockers = [
        "固定分段结果不能替代research_protocol中的24/6/3月走查前推",
        "历史点时行业归属和板块退潮门禁尚不可靠",
        "当前每笔5000元研究委托尚未替换为R0/R1/R2组合仓位模型",
        "组合最大回撤、资金占用和异常赢家贡献尚未验证",
    ]
    if p1_test["completed"] < 100:
        blockers.insert(0, f"研究验证段P1已完成交易仅{p1_test['completed']}笔，少于100笔")
    return {
        "status": "SHADOW_ONLY",
        "fixed_split_p1_support": fixed_support,
        "split": {"train_end": train_end, "validation_end": validation_end},
        "hypothesis": "P1 pullback improves executable return distribution versus P0 direct chasing",
        "e3_blockers": blockers,
        "arms": arms,
    }


def run_backtest(
    engine,
    *,
    start_date: str = DEFAULT_START_DATE,
    train_end: str = DEFAULT_TRAIN_END,
    validation_end: str = DEFAULT_VALIDATION_END,
    chunk_size: int = 200,
    max_codes: int | None = None,
    include_research_protocol: bool = True,
) -> dict[str, Any]:
    market = _market_proxy(engine, start_date)
    market_stages = _load_market_stage_lookup(engine, start_date, market)
    codes = _load_codes(engine, start_date, max_codes)
    rows: list[dict[str, Any]] = []
    for code_chunk in _chunks(codes, max(1, int(chunk_size))):
        prices = _load_price_chunk(engine, code_chunk, start_date)
        for code, group in prices.groupby("code", sort=False):
            rows.extend(replay_stock(str(code), group, market, market_stages))
    events = pd.DataFrame(rows)
    candidate_events = len(events[["code", "signal_date"]].drop_duplicates()) if not events.empty else 0
    research_protocol = (
        run_route_b_research_protocol(
            engine,
            events,
            market,
            start_date=start_date,
        )
        if include_research_protocol
        else {"status": "SKIPPED"}
    )
    return {
        "meta": {
            "version": REPLAY_VERSION,
            "generated_on": date.today().isoformat(),
            "start_date": start_date,
            "codes_tested": len(codes),
            "candidate_events": candidate_events,
            "event_arm_rows": len(events),
            "universe": "strict MA+TV-ZP paired signals, PA>=60/non-AVOID, five-day gain >15%",
            "p1": "1-3 days, original confirmation +/-1%, close reclaims, volume<=signal day, structure intact",
            "entry": "P0 next open; P1 next open after confirmation; max 3% gap; realistic A-share costs",
            "exit": "matching strategy sell point with T+1 and -9% protective stop; no fixed holding period",
            "status": "SHADOW_ONLY",
            "limitations": [
                "historical stock universe and ST/delist labels are incomplete",
                "current industry metadata is not used as a hard gate because it is not point-in-time reliable",
                "P1等待期B0取消使用因果宽度与等权趋势代理；research_protocol另用上证/创业板官方双指数复验",
                "the 5000 CNY research order baseline rejects some high-priced shares before portfolio sizing",
                "daily OHLC cannot reconstruct intraday trigger ordering",
            ],
        },
        "report": build_report(events, train_end=train_end, validation_end=validation_end),
        "research_protocol": research_protocol,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay route B strong-trend P0/P1/SKIP shadow arms")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--train-end", default=DEFAULT_TRAIN_END)
    parser.add_argument("--validation-end", default=DEFAULT_VALIDATION_END)
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--max-codes", type=int)
    parser.add_argument("--skip-research-protocol", action="store_true")
    args = parser.parse_args()
    report = run_backtest(
        get_db_engine(),
        start_date=args.start_date,
        train_end=args.train_end,
        validation_end=args.validation_end,
        chunk_size=args.chunk_size,
        max_codes=args.max_codes,
        include_research_protocol=not args.skip_research_protocol,
    )
    print(json.dumps(report, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
