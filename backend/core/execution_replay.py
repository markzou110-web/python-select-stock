"""Point-in-time replay of the current post-selection execution policy."""
import json
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import bindparam, inspect, text

from core.analytics import compute_profit_factor
from core.execution_audit import assess_persistent_b_shadow, classify_trade_blockers
from core.execution_labels import build_executable_labels, daily_limit_pct
from core.outcome_calibration import load_scan_outcomes
from core.performance_metrics import return_metrics
from core.risk_constants import BACKTEST_STOP_LOSS_PCT
from core.scanner import _apply_trade_execution_profile


REPLAY_VERSION = "execution-policy-replay-v1"
MIN_EVIDENCE_SIGNALS = 30
OPERATION_ADVICE_VERSION = "post-selection-operation-v1"
OPERATION_POLICIES = {
    "baseline": {"max_open_gap_pct": 3.0, "stop_loss_pct": -8.0, "max_hold_days": 5, "avoid_critical": False},
    "short_hold": {"max_open_gap_pct": 2.0, "stop_loss_pct": -6.0, "max_hold_days": 3, "avoid_critical": False},
    "no_chase": {"max_open_gap_pct": 1.0, "stop_loss_pct": -6.0, "max_hold_days": 3, "avoid_critical": False},
    "defensive_only": {"max_open_gap_pct": 2.0, "stop_loss_pct": -6.0, "max_hold_days": 3, "avoid_critical": True},
}


def _load_bark_instruction_events(engine, days: int) -> pd.DataFrame:
    if not inspect(engine).has_table("recommendation_events"):
        return pd.DataFrame()
    if engine.dialect.name == "sqlite":
        date_filter = "date(event_date) >= date('now', '-' || :days || ' days')"
    else:
        date_filter = "event_date >= CURRENT_DATE - (:days || ' days')::interval"
    return pd.read_sql(text(f"""
        SELECT event_date AS signal_date, event_time, source, code, name,
               strategy_type, recommendation_price AS signal_close,
               trade_bucket, trade_eligible, pa_entry_price, pa_stop_price,
               market_regime
        FROM recommendation_events
        WHERE {date_filter} AND source = 'bark'
        ORDER BY event_time
    """), engine, params={"days": days})


def _load_execution_intent_audit(engine, days: int) -> Dict[str, Any]:
    if not inspect(engine).has_table("execution_intents"):
        return {"issued": 0, "states": {}, "items": []}
    if engine.dialect.name == "sqlite":
        date_filter = "date(signal_date) >= date('now', '-' || :days || ' days')"
    else:
        date_filter = "signal_date >= CURRENT_DATE - (:days || ' days')::interval"
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT state, COUNT(*) AS count
            FROM execution_intents WHERE {date_filter}
            GROUP BY state
        """), {"days": days}).mappings().all()
        items = conn.execute(text(f"""
            SELECT signal_date, issued_at AS event_time, source, code, name,
                   strategy_type, instruction, state,
                   planned_entry_price AS signal_close,
                   planned_entry_price AS pa_entry_price, stop_price AS pa_stop_price,
                   actual_price, filled_shares
            FROM execution_intents WHERE {date_filter}
            ORDER BY issued_at
        """), {"days": days}).mappings().all()
    states = {str(row["state"]): int(row["count"]) for row in rows}
    return {"issued": sum(states.values()), "states": states, "items": [dict(row) for row in items]}


def _row_candidate(row: pd.Series) -> Dict[str, Any]:
    detail = row.get("price_action_detail") or {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            detail = {}
    candidate = dict(detail) if isinstance(detail, dict) else {}
    mapping = {
        "代码": str(row.get("code") or ""), "名称": row.get("name"),
        "现价": row.get("price"), "涨幅%": row.get("pct"), "Score": row.get("score"),
        "行业": row.get("industry"), "共振": row.get("resonance"),
        "strategy_type": row.get("strategy_type"), "sop_grade": row.get("sop_grade"),
        "sop_quality_score": row.get("sop_quality_score"),
        "pa_entry_price": row.get("pa_entry_price"), "pa_stop_price": row.get("pa_stop_price"),
        "pa_target_price": row.get("pa_target_price"), "pa_risk_reward": row.get("pa_risk_reward"),
        "pa_trade_action": row.get("pa_trade_action"), "pa_trade_setup": row.get("pa_trade_setup"),
        "pa_risk_pct": row.get("pa_risk_pct"), "price_action_score": row.get("price_action_score"),
        "price_action_regime": row.get("price_action_regime"),
        "price_action_signal": row.get("price_action_signal"),
        "price_action_pattern": row.get("price_action_pattern"),
        "price_action_entry_quality": row.get("price_action_entry_quality"),
    }
    for key, value in mapping.items():
        if value is not None and not (isinstance(value, float) and pd.isna(value)):
            candidate[key] = value
    return candidate


def _metrics(values: pd.Series) -> Dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    metrics = return_metrics(clean)
    return {
        "signals": int(len(clean)),
        "win_rate": round(float(metrics.get("win_rate") or 0), 1),
        "avg_return": round(float(metrics.get("expected_return") or 0), 2),
        "median_return": round(float(clean.median()), 2) if len(clean) else None,
        "profit_factor": compute_profit_factor(clean.tolist()),
        "ci95_low": round(float(metrics.get("ci95_low") or 0), 2),
        "ci95_high": round(float(metrics.get("ci95_high") or 0), 2),
        "worst_return": round(float(clean.min()), 2) if len(clean) else None,
    }


def build_execution_replay_report(candidates: pd.DataFrame, outcomes: pd.DataFrame) -> Dict[str, Any]:
    if candidates.empty:
        return {
            "version": REPLAY_VERSION, "verdict": "INSUFFICIENT_DATA",
            "summary": {"point_in_time_candidates": 0}, "policies": [],
        }
    outcome_map = {
        (str(row.get("code")), str(row.get("signal_date"))[:10], str(row.get("strategy_type"))): row
        for _, row in outcomes.iterrows()
    }


    policy_returns = {
        "current_policy": [], "hard_wait_a_b_shadow": [], "hard_only_a_b_shadow": [],
        "persistent_b_shadow": [], "evidence_enforced_simulation": [],
    }
    policy_trades = {key: [] for key in policy_returns}
    executable_returns = {key: [] for key in policy_returns}
    unfilled_counts = {key: 0 for key in policy_returns}
    unfilled_reasons: Dict[str, int] = {}
    funnel = {"research": 0, "strict_strategy": 0, "strict_grade_a": 0, "current_profile_trade": 0}
    blocker_counts: Dict[str, int] = {}
    evidence_grades: Dict[str, int] = {}
    evidence_attribution: Dict[str, int] = {}
    persistent_shadow_candidates = 0
    persistent_shadow_failures: Dict[str, int] = {}
    replay_rows = []
    signal_dates = pd.to_datetime(candidates.get("signal_date"), errors="coerce")
    for idx, row in candidates.iterrows():
        candidate = _row_candidate(row)
        current_date = signal_dates.loc[idx] if idx in signal_dates.index else pd.NaT
        if pd.notna(current_date):
            same_code = candidates["code"].astype(str) == str(row.get("code"))
            prior = signal_dates[same_code & signal_dates.between(current_date - pd.Timedelta(days=10), current_date)]
            candidate["recent_push_days"] = int(prior.dt.date.nunique())
        _apply_trade_execution_profile(candidate)
        funnel["research"] += 1
        strict = str(row.get("strategy_type")) == "tv_dual_strict"
        grade = str(row.get("sop_grade") or "")
        funnel["strict_strategy"] += int(strict)
        funnel["strict_grade_a"] += int(strict and grade == "A")
        groups = classify_trade_blockers(candidate.get("trade_blockers") or [])
        opportunity = float(candidate.get("trade_opportunity_score") or 0)
        current = bool(candidate.get("trade_eligible")) and candidate.get("trade_bucket") == "TRADE"
        stage = str(candidate.get("market_sentiment_stage") or "").upper()
        mainline = str(candidate.get("sector_mainline") or "").upper()
        current = current and stage not in {"RETREAT", "ICE"} and mainline != "FADING" and opportunity >= 60
        health = candidate.get("strategy_health") or {}
        if isinstance(health, dict) and health.get("status") == "PAUSED" and not candidate.get("event_driven_candidate"):
            current = False
        funnel["current_profile_trade"] += int(current)
        common = strict and grade in {"A", "B"} and opportunity >= 60
        evidence_grade = str(candidate.get("evidence_grade") or "UNRATED").upper()
        evidence_grades[evidence_grade] = evidence_grades.get(evidence_grade, 0) + 1
        persistent_shadow = assess_persistent_b_shadow(candidate)
        if strict and grade == "B":
            persistent_shadow_candidates += 1
            for check, passed in persistent_shadow["checks"].items():
                if not passed:
                    persistent_shadow_failures[check] = persistent_shadow_failures.get(check, 0) + 1
        decisions = {
            "current_policy": current,
            "hard_wait_a_b_shadow": common and not groups["hard"] and not groups["wait"],
            "hard_only_a_b_shadow": common and not groups["hard"],
            "persistent_b_shadow": persistent_shadow["eligible"],
            "evidence_enforced_simulation": current and evidence_grade in {"A", "B"},
        }
        key = (str(row.get("code")), str(row.get("signal_date"))[:10], str(row.get("strategy_type")))
        outcome = outcome_map.get(key)
        future_return = outcome.get("ret_5d") if outcome is not None else None
        exec_filled = bool(outcome.get("exec_filled")) if outcome is not None and pd.notna(outcome.get("exec_filled")) else False
        exec_return = outcome.get("exec_return_pct") if outcome is not None else None
        exec_reason = str(outcome.get("exec_reason") or "未生成真实成交标签") if outcome is not None else "缺少结果样本"
        if current and outcome is not None:
            if not exec_filled:
                evidence_attribution["NO_FILL"] = evidence_attribution.get("NO_FILL", 0) + 1
            if future_return is not None and not pd.isna(future_return):
                direction = "DIRECTION_CORRECT" if float(future_return) > 0 else "DIRECTION_WRONG"
                evidence_attribution[direction] = evidence_attribution.get(direction, 0) + 1
                if evidence_grade in {"C", "D", "F"}:
                    gate_result = "RISK_GATE_MISSED_WINNER" if float(future_return) > 0 else "RISK_GATE_SAVED_LOSS"
                    evidence_attribution[gate_result] = evidence_attribution.get(gate_result, 0) + 1
        reason_codes = candidate.get("evidence_reason_codes") or []
        if any("STALE" in str(code) for code in reason_codes):
            evidence_attribution["DATA_STALE"] = evidence_attribution.get("DATA_STALE", 0) + 1
        if any(any(marker in str(code) for marker in ("MISSING", "UNAVAILABLE", "NOT_COLLECTED")) for code in reason_codes):
            evidence_attribution["DATA_MISSING"] = evidence_attribution.get("DATA_MISSING", 0) + 1
        for policy, selected in decisions.items():
            if selected:
                policy_returns[policy].append(future_return)
                risk_pct = float(candidate.get("pa_risk_pct") or 0)
                position_pct = min(5.0, 100.0 / risk_pct) if risk_pct > 0 else 5.0
                policy_trades[policy].append({"return": future_return, "position_pct": position_pct})
                if exec_filled:
                    executable_returns[policy].append(exec_return)
                else:
                    unfilled_counts[policy] += 1
                    if policy == "current_policy":
                        unfilled_reasons[exec_reason] = unfilled_reasons.get(exec_reason, 0) + 1
        for blocker in candidate.get("trade_blockers") or []:
            blocker_counts[str(blocker)] = blocker_counts.get(str(blocker), 0) + 1
        replay_rows.append({
            "code": key[0], "signal_date": key[1], "strategy_type": key[2],
            "grade": grade, "evidence_grade": evidence_grade,
            "current_selected": current, "evidence_selected": decisions["evidence_enforced_simulation"],
            "ret_5d": future_return,
        })
    policies = []
    for policy, values in policy_returns.items():
        metrics = _metrics(pd.Series(values, dtype="object"))
        policies.append({
            "policy": policy, "selected": len(values), "metrics_5d": metrics,
            "realistic_execution": {
                "filled": len(executable_returns[policy]),
                "unfilled": unfilled_counts[policy],
                "fill_rate_pct": round(len(executable_returns[policy]) / len(values) * 100, 1) if values else 0,
                "metrics": _metrics(pd.Series(executable_returns[policy], dtype="object")),
            },
        })
    portfolio_simulations = []
    for policy, trades in policy_trades.items():
        equity = 100000.0
        peak = equity
        max_drawdown = 0.0
        used = 0
        for trade in trades:
            if trade["return"] is None or pd.isna(trade["return"]):
                continue
            equity *= 1 + float(trade["return"]) / 100 * float(trade["position_pct"]) / 100
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, (equity - peak) / peak * 100)
            used += 1
        portfolio_simulations.append({
            "policy": policy, "mature_trades": used, "final_equity": round(equity, 2),
            "total_return_pct": round((equity / 100000 - 1) * 100, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "assumption": "按信号顺序、基础5%仓位，止损距离存在时受1%风险预算封顶",
        })
    current_metrics = next(item["metrics_5d"] for item in policies if item["policy"] == "current_policy")
    if current_metrics["signals"] < MIN_EVIDENCE_SIGNALS:
        verdict = "NOT_VALIDATED"
        reason = f"当前策略仅有 {current_metrics['signals']} 个5日成熟交易样本，少于 {MIN_EVIDENCE_SIGNALS}"
    elif current_metrics["avg_return"] > 0 and current_metrics["profit_factor"] > 1:
        verdict = "SUPPORTED"
        reason = "成熟样本达到要求，且5日期望与盈亏因子为正"
    else:
        verdict = "NOT_SUPPORTED"
        reason = "成熟样本达到要求，但5日期望或盈亏因子未通过"
    return {
        "version": REPLAY_VERSION,
        "verdict": verdict,
        "verdict_reason": reason,
        "evidence_requirement": {
            "min_mature_signals": MIN_EVIDENCE_SIGNALS,
            "avg_return_gt": 0,
            "profit_factor_gt": 1,
        },
        "summary": {"point_in_time_candidates": int(len(candidates)), "funnel": funnel},
        "evidence_quality": {
            "mode": "SHADOW",
            "grade_distribution": evidence_grades,
            "graded": int(len(candidates) - evidence_grades.get("UNRATED", 0)),
            "unrated": int(evidence_grades.get("UNRATED", 0)),
            "attribution": evidence_attribution,
            "note": "证据策略只做点时影子对照，不改变生产交易资格",
        },
        "persistent_b_shadow": {
            "mode": "SHADOW",
            "strict_b_candidates": persistent_shadow_candidates,
            "selected": len(policy_returns["persistent_b_shadow"]),
            "failed_checks": persistent_shadow_failures,
            "note": "连续B级只记录反事实结果，不改变Bark不可交易指令",
        },
        "policies": policies,
        "portfolio_simulations": portfolio_simulations,
        "top_blockers": [
            {"blocker": blocker, "count": count}
            for blocker, count in sorted(blocker_counts.items(), key=lambda item: -item[1])[:20]
        ],
        "top_unfilled_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(unfilled_reasons.items(), key=lambda item: -item[1])[:10]
        ],
        "selected_rows": [row for row in replay_rows if row["current_selected"]],
        "notes": [
            "所有准入字段来自信号当时保存的点时快照；未来5日收益仅用于结果评价。",
            "影子政策不改变生产规则，只用于检验门禁消融。",
        ],
    }


def _operation_policy_metrics(labels: pd.DataFrame, dates: set[pd.Timestamp], avoid_critical: bool) -> Dict[str, Any]:
    signal_dates = pd.to_datetime(labels.get("signal_date"), errors="coerce").dt.normalize()
    group = labels[signal_dates.isin(dates)].copy()
    if avoid_critical:
        regimes = group.get("market_regime", pd.Series("UNKNOWN", index=group.index)).astype(str)
        group = group[~regimes.eq("CRITICAL")]
    mature = group[group.get("exec_mature", pd.Series(False, index=group.index)).fillna(False).astype(bool)]
    filled = mature[mature.get("exec_filled", pd.Series(False, index=mature.index)).fillna(False).astype(bool)]
    return {
        "candidates": int(len(group)),
        "mature": int(len(mature)),
        "filled": int(len(filled)),
        "fill_rate_pct": round(len(filled) / len(mature) * 100, 1) if len(mature) else 0,
        **_metrics(filled.get("exec_return_pct", pd.Series(dtype=float))),
    }


def _actual_fill_evidence(intent_rows: pd.DataFrame, daily: pd.DataFrame, hold_days: int = 5) -> Dict[str, Any]:
    """Evaluate user-recorded fills from their actual entry price, then model only the exit."""
    empty = {
        "fills": 0, "mature": 0, "verdict": "INSUFFICIENT_DATA",
        "metrics": _metrics(pd.Series(dtype=float)),
    }
    if intent_rows.empty or daily.empty:
        return empty
    filled = intent_rows[
        intent_rows.get("state", pd.Series("", index=intent_rows.index)).astype(str).eq("FILLED")
        & pd.to_numeric(intent_rows.get("actual_price", pd.Series(index=intent_rows.index, dtype=float)), errors="coerce").gt(0)
        & pd.to_numeric(intent_rows.get("filled_shares", pd.Series(index=intent_rows.index, dtype=float)), errors="coerce").gt(0)
    ].copy()
    if filled.empty:
        return empty
    prices = daily.copy()
    prices["日期"] = pd.to_datetime(prices["日期"], errors="coerce")
    grouped = {
        str(code): group.sort_values("日期").reset_index(drop=True)
        for code, group in prices.groupby(prices["code"].astype(str), sort=False)
    }
    returns = []
    excluded_adjustment_gaps = 0
    for _, row in filled.iterrows():
        code = str(row.get("code") or "")
        signal_date = pd.to_datetime(row.get("signal_date"), errors="coerce")
        future = grouped.get(code, pd.DataFrame())
        future = future[future["日期"] > signal_date].head(hold_days).reset_index(drop=True) if not future.empty else future
        if len(future) < hold_days:
            continue
        entry = float(row["actual_price"])
        shares = int(row["filled_shares"])
        first_open = float(future.iloc[0]["开盘"])
        first_close = float(future.iloc[0]["收盘"])
        open_gap_pct = abs((first_open / entry - 1) * 100)
        intraday_move_pct = abs((first_close / first_open - 1) * 100) if first_open > 0 else 0
        if open_gap_pct >= max(20.0, daily_limit_pct(code) + 2.0) and intraday_move_pct <= 8.0:
            excluded_adjustment_gaps += 1
            continue
        planned_stop = pd.to_numeric(pd.Series([row.get("pa_stop_price")]), errors="coerce").iloc[0]
        stop = float(planned_stop) if pd.notna(planned_stop) and 0 < float(planned_stop) < entry else entry * 0.92
        exit_raw = float(future.iloc[-1]["收盘"])
        for _, bar in future.iterrows():
            if float(bar["最低"]) <= stop:
                exit_raw = min(float(bar["开盘"]), stop) if float(bar["开盘"]) <= stop else stop
                break
        exit_price = exit_raw * (1 - 5.0 / 10000)
        buy_value, sell_value = entry * shares, exit_price * shares
        fees = max(buy_value * 0.00025, 5.0) + max(sell_value * 0.00025, 5.0) + sell_value * 0.0005
        returns.append((sell_value - buy_value - fees) / buy_value * 100)
    metrics = _metrics(pd.Series(returns, dtype=float))
    verdict = (
        "INSUFFICIENT_DATA"
        if len(returns) < MIN_EVIDENCE_SIGNALS
        else "SUPPORTED"
        if metrics["avg_return"] > 0 and metrics["profit_factor"] > 1
        else "NOT_SUPPORTED"
    )
    return {
        "fills": int(len(filled)),
        "mature": int(len(returns)),
        "excluded_adjustment_gaps": excluded_adjustment_gaps,
        "metrics": metrics,
        "verdict": verdict,
        "measurement": "实际成交价买入；后续5个交易日按结构止损/8%回退止损和成本模拟退出",
    }


def build_bark_instruction_evidence(
    events: pd.DataFrame,
    daily: pd.DataFrame,
    intent_audit: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Evaluate only persisted Bark `可交易` instructions, never research candidates."""
    intent_audit = intent_audit or {"issued": 0, "states": {}, "items": []}
    intent_summary = {
        "issued": int(intent_audit.get("issued") or 0),
        "states": dict(intent_audit.get("states") or {}),
    }
    empty = {
        "status": "INSUFFICIENT_DATA",
        "persisted_candidates": 0,
        "tradable_instructions": 0,
        "audited_delivered_instructions": 0,
        "missing_planned_entry": 0,
        "mature": 0,
        "filled": 0,
        "required": MIN_EVIDENCE_SIGNALS,
        "metrics": _metrics(pd.Series(dtype=float)),
        "intent_audit": intent_summary,
        "actual_fill_evidence": {
            "fills": 0, "mature": 0, "verdict": "INSUFFICIENT_DATA",
            "metrics": _metrics(pd.Series(dtype=float)),
        },
    }
    normalized = events.copy()
    instructions = pd.DataFrame()
    if not normalized.empty:
        eligible_raw = normalized.get("trade_eligible", pd.Series(False, index=normalized.index))
        eligible = (
            pd.to_numeric(eligible_raw, errors="coerce").eq(1)
            | eligible_raw.fillna(False).astype(str).str.lower().isin({"true", "yes"})
        )
        bucket = normalized.get("trade_bucket", pd.Series("", index=normalized.index)).fillna("").astype(str).str.upper()
        strategy = normalized.get("strategy_type", pd.Series("", index=normalized.index)).fillna("").astype(str)
        instructions = normalized[eligible & bucket.eq("TRADE") & strategy.eq("tv_dual_strict")].copy()
        instructions["signal_date"] = pd.to_datetime(instructions.get("signal_date"), errors="coerce")
        instructions = instructions.dropna(subset=["signal_date", "signal_close"])
        instructions = instructions.sort_values("event_time").drop_duplicates(
            subset=["signal_date", "code", "strategy_type"], keep="first",
        )

    intent_rows = pd.DataFrame(intent_audit.get("items") or [])
    if not intent_rows.empty:
        intent_rows = intent_rows[
            intent_rows.get("instruction", pd.Series("", index=intent_rows.index)).astype(str).eq("可交易")
            & intent_rows.get("strategy_type", pd.Series("", index=intent_rows.index)).astype(str).eq("tv_dual_strict")
        ].copy()
        intent_rows["signal_date"] = pd.to_datetime(intent_rows["signal_date"], errors="coerce")
        intent_rows = intent_rows.dropna(subset=["signal_date"])
        intent_rows = intent_rows.sort_values("event_time").drop_duplicates(
            subset=["signal_date", "code", "strategy_type"], keep="first",
        )
    labelable_intents = intent_rows.dropna(subset=["signal_close"]) if not intent_rows.empty else pd.DataFrame()
    if daily.empty:
        return {
            **empty,
            "persisted_candidates": int(len(normalized)),
            "tradable_instructions": int(len(instructions)),
            "audited_delivered_instructions": int(len(intent_rows)),
            "missing_planned_entry": int(len(intent_rows) - len(labelable_intents)),
            "note": "缺少指令后的日线行情",
        }

    generated_labels = build_executable_labels(
        instructions,
        daily,
        max_open_gap_pct=3.0,
        stop_loss_pct=-8.0,
        max_hold_days=5,
        slippage_bps=5.0,
        planned_order_value=5000,
        max_volume_share_pct=5.0,
        volume_in_lots=True,
    ) if not instructions.empty else pd.DataFrame()
    generated_mature = generated_labels[
        generated_labels.get("exec_mature", pd.Series(False, index=generated_labels.index)).fillna(False).astype(bool)
    ] if not generated_labels.empty else pd.DataFrame()
    generated_filled = generated_mature[
        generated_mature.get("exec_filled", pd.Series(False, index=generated_mature.index)).fillna(False).astype(bool)
    ] if not generated_mature.empty else pd.DataFrame()
    audited_labels = build_executable_labels(
        labelable_intents,
        daily,
        max_open_gap_pct=3.0,
        stop_loss_pct=-8.0,
        max_hold_days=5,
        slippage_bps=5.0,
        planned_order_value=5000,
        max_volume_share_pct=5.0,
        volume_in_lots=True,
    ) if not labelable_intents.empty else pd.DataFrame()
    mature = audited_labels[
        audited_labels.get("exec_mature", pd.Series(False, index=audited_labels.index)).fillna(False).astype(bool)
    ] if not audited_labels.empty else pd.DataFrame()
    filled = mature[
        mature.get("exec_filled", pd.Series(False, index=mature.index)).fillna(False).astype(bool)
    ] if not mature.empty else pd.DataFrame()
    generated_keys = set()
    if not instructions.empty:
        generated_keys = set(
            instructions["signal_date"].dt.strftime("%Y-%m-%d")
            + "|" + instructions["code"].astype(str)
            + "|" + instructions["strategy_type"].astype(str)
        )
    audited_keys = set()
    if not intent_rows.empty:
        audited_keys = set(
            intent_rows["signal_date"].dt.strftime("%Y-%m-%d")
            + "|" + intent_rows["code"].astype(str)
            + "|" + intent_rows["strategy_type"].astype(str)
        )
    audit_gap = bool(generated_keys - audited_keys)
    actual_fills = _actual_fill_evidence(intent_rows, daily)
    instruction_status = "VALIDATED" if len(filled) >= MIN_EVIDENCE_SIGNALS else "INSUFFICIENT_DATA"
    status = (
        "VALIDATED"
        if int(actual_fills.get("mature") or 0) >= MIN_EVIDENCE_SIGNALS
        else "INSUFFICIENT_DATA"
    )
    return {
        **empty,
        "status": status,
        "instruction_status": instruction_status,
        "persisted_candidates": int(len(normalized)),
        "tradable_instructions": int(len(instructions)),
        "audited_delivered_instructions": int(len(intent_rows)),
        "missing_planned_entry": int(len(intent_rows) - len(labelable_intents)),
        "mature": int(len(mature)),
        "filled": int(len(filled)),
        "metrics": _metrics(filled.get("exec_return_pct", pd.Series(dtype=float))),
        "intent_audit": {
            **intent_summary,
            "generated_matched": int(len(generated_keys & audited_keys)),
            "next_day_or_other_audited": int(len(audited_keys - generated_keys)),
        },
        "generated_instruction_shadow": {
            "mature": int(len(generated_mature)),
            "filled": int(len(generated_filled)),
            "metrics": _metrics(generated_filled.get("exec_return_pct", pd.Series(dtype=float))),
        },
        "actual_fill_evidence": actual_fills,
        "audit_gap": audit_gap,
        "note": (
            "存在可交易事件但没有对应执行意图审计记录，暂不能证明实际执行质量"
            if audit_gap else "仅统计历史持久化的 Bark 可交易指令，并按次日可成交价格计入成本"
            if len(intent_rows) == len(labelable_intents)
            else f"有 {len(intent_rows) - len(labelable_intents)} 条已发送指令缺少计划价，保留计数但不纳入收益回测"
        ),
    }


def build_operation_advice_validation(
    outcomes: pd.DataFrame,
    daily: pd.DataFrame,
    bark_events: pd.DataFrame | None = None,
    intent_audit: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Validate post-selection execution advice without changing stock selection."""
    empty = {
        "version": OPERATION_ADVICE_VERSION,
        "verdict": "INSUFFICIENT_DATA",
        "production_logic_changed": False,
        "policies": [],
        "actual_bark_evidence": build_bark_instruction_evidence(
            bark_events if bark_events is not None else pd.DataFrame(), daily, intent_audit,
        ),
    }
    if outcomes.empty or daily.empty:
        return empty
    research = outcomes.get("research_eligible", pd.Series(False, index=outcomes.index)).fillna(False).astype(bool)
    cohort = outcomes[
        research
        & outcomes.get("strategy_type", pd.Series("", index=outcomes.index)).astype(str).eq("tv_dual_strict")
        & outcomes.get("sop_grade", pd.Series("", index=outcomes.index)).astype(str).isin({"A", "B", "M"})
    ].copy()
    if cohort.empty:
        return {**empty, "cohort": {"rule": "tv_dual_strict and grade in A/B/M", "signals": 0}}

    common_params = {
        "slippage_bps": 5.0,
        "planned_order_value": 5000,
        "max_volume_share_pct": 5.0,
        "volume_in_lots": True,
    }
    labels_by_policy = {
        name: build_executable_labels(
            cohort,
            daily,
            max_open_gap_pct=params["max_open_gap_pct"],
            stop_loss_pct=params["stop_loss_pct"],
            max_hold_days=params["max_hold_days"],
            **common_params,
        )
        for name, params in OPERATION_POLICIES.items()
    }
    baseline = labels_by_policy["baseline"]
    mature_mask = baseline.get("exec_mature", pd.Series(False, index=baseline.index)).fillna(False).astype(bool)
    mature_dates = sorted(
        pd.to_datetime(baseline.loc[mature_mask, "signal_date"], errors="coerce").dropna().dt.normalize().unique()
    )
    if len(mature_dates) < 5:
        return {
            **empty,
            "cohort": {
                "rule": "tv_dual_strict and grade in A/B/M",
                "signals": int(len(cohort)),
                "mature_dates": len(mature_dates),
            },
        }
    train_end = max(1, int(len(mature_dates) * 0.6))
    validation_end = min(max(train_end + 1, int(len(mature_dates) * 0.8)), len(mature_dates) - 1)
    splits = {
        "train": set(mature_dates[:train_end]),
        "validation": set(mature_dates[train_end:validation_end]),
        "test": set(mature_dates[validation_end:]),
    }
    policy_rows = [{
        "policy": name,
        "params": params,
        "splits": {
            split: _operation_policy_metrics(labels_by_policy[name], dates, params["avoid_critical"])
            for split, dates in splits.items()
        },
    } for name, params in OPERATION_POLICIES.items()]
    baseline_row = next(row for row in policy_rows if row["policy"] == "baseline")
    validation_candidates = [
        row for row in policy_rows
        if row["policy"] != "baseline" and row["splits"]["validation"]["signals"] >= 5
    ]
    if not validation_candidates:
        return {
            **empty,
            "cohort": {
                "rule": "research_eligible and tv_dual_strict and grade in A/B/M",
                "signals": int(len(cohort)),
                "mature_dates": len(mature_dates),
                "split_dates": {name: len(values) for name, values in splits.items()},
            },
            "policies": policy_rows,
        }
    selected = max(
        validation_candidates,
        key=lambda row: (
            row["splits"]["validation"]["avg_return"],
            row["splits"]["validation"]["win_rate"],
        ),
    )
    baseline_test = baseline_row["splits"]["test"]
    challenger_test = selected["splits"]["test"]
    enough_test = len(splits["test"]) >= 4 and challenger_test["signals"] >= MIN_EVIDENCE_SIGNALS
    supported = (
        enough_test
        and challenger_test["avg_return"] > 0
        and challenger_test["profit_factor"] > 1
        and challenger_test["avg_return"] > baseline_test["avg_return"]
        and challenger_test["win_rate"] >= baseline_test["win_rate"]
    )
    verdict = "SUPPORTED_CHALLENGER" if supported else "KEEP_CURRENT_GATES" if enough_test else "INSUFFICIENT_DATA"
    recommendation = (
        f"样本外支持 {selected['policy']}，仅可用于操作建议层灰度验证"
        if supported
        else "没有操作规则在样本外同时取得正期望和盈亏因子>1；保持当前确认门禁，不放宽买入"
        if enough_test
        else "测试段日期或成交样本不足，不调整生产操作建议；继续积累点时样本"
    )
    return {
        "version": OPERATION_ADVICE_VERSION,
        "verdict": verdict,
        "recommendation": recommendation,
        "production_logic_changed": False,
        "actual_bark_evidence": empty["actual_bark_evidence"],
        "cohort": {
            "rule": "research_eligible and tv_dual_strict and grade in A/B/M",
            "signals": int(len(cohort)),
            "mature_dates": len(mature_dates),
            "split_dates": {name: len(values) for name, values in splits.items()},
        },
        "selected_challenger": selected["policy"],
        "acceptance_gate": {
            "min_test_dates": 4,
            "min_test_trades": MIN_EVIDENCE_SIGNALS,
            "avg_return_gt": 0,
            "profit_factor_gt": 1,
            "win_rate_not_below_baseline": True,
        },
        "policies": policy_rows,
        "notes": [
            "影子股票池完全来自原选股结果；仅比较入选后的高开限制、止损、持有期和极端市场回避。",
            "所有策略使用相同成熟日期，参数选择只看验证段，最终结论只看最新测试段。",
            "包含双边佣金、印花税、最低佣金、5bp滑点、T+1和成交量容量约束。",
            "actual_bark_evidence 单独统计历史真实持久化的“可交易”指令，不与研究候选混算。",
        ],
    }


def run_historical_execution_replay(engine, days: int = 120) -> Dict[str, Any]:
    days = max(1, min(int(days), 3650))
    if engine.dialect.name == "sqlite":
        date_filter = "date(COALESCE(data_date, date)) >= date('now', '-' || :days || ' days')"
        research_filter = "COALESCE(json_extract(price_action_detail, '$.research_eligible'), 0) IN (1, 'true', 'TRUE')"
    else:
        date_filter = "COALESCE(data_date, date) >= CURRENT_DATE - (:days || ' days')::interval"
        research_filter = "COALESCE((price_action_detail->>'research_eligible')::boolean, false) = true"
    candidates = pd.read_sql(text(f"""
        SELECT code, name, COALESCE(data_date, date) AS signal_date, price, pct, score,
               industry, resonance, strategy_type, sop_grade, sop_quality_score, pa_entry_price, pa_stop_price,
               pa_target_price, pa_risk_reward, pa_trade_action, pa_trade_setup, pa_risk_pct,
               price_action_score, price_action_regime, price_action_signal,
               price_action_pattern, price_action_entry_quality, price_action_detail
        FROM scan_history
        WHERE {date_filter} AND {research_filter}
    """), engine, params={"days": days})
    outcomes = load_scan_outcomes(engine, days=days)
    operation_outcomes = outcomes.copy()
    bark_events = _load_bark_instruction_events(engine, days)
    intent_audit = _load_execution_intent_audit(engine, days)
    intent_rows = pd.DataFrame(intent_audit.get("items") or [])
    daily = pd.DataFrame()
    source_frames = [frame for frame in (outcomes, bark_events, intent_rows) if not frame.empty]
    codes = sorted({str(code) for frame in source_frames for code in frame["code"].dropna().tolist()})
    signal_dates = pd.concat(
        [pd.to_datetime(frame["signal_date"], errors="coerce") for frame in source_frames],
        ignore_index=True,
    ).dropna() if source_frames else pd.Series(dtype="datetime64[ns]")
    if codes and not signal_dates.empty:
        start_date = str(signal_dates.min().date())
        end_date = str((signal_dates.max() + pd.Timedelta(days=20)).date())
        daily_query = text("""
            SELECT code, date AS "日期", open AS "开盘", high AS "最高", low AS "最低",
                   close AS "收盘", vol AS "成交量"
            FROM daily_k WHERE code IN :codes AND date >= :start_date AND date <= :end_date
            ORDER BY code, date
        """).bindparams(bindparam("codes", expanding=True))
        daily = pd.read_sql(daily_query, engine, params={"codes": codes, "start_date": start_date, "end_date": end_date})
    if not outcomes.empty and not daily.empty:
        outcomes = build_executable_labels(
            outcomes, daily, max_hold_days=5, stop_loss_pct=BACKTEST_STOP_LOSS_PCT,
            planned_order_value=5000,
            max_volume_share_pct=5.0, volume_in_lots=True,
        )
    report = build_execution_replay_report(candidates, outcomes)
    report["operation_advice_validation"] = build_operation_advice_validation(
        operation_outcomes, daily, bark_events, intent_audit,
    )
    report["days"] = days
    return report
