"""Point-in-time replay of the current post-selection execution policy."""
import json
from typing import Any, Dict

import pandas as pd
from sqlalchemy import bindparam, text

from core.execution_audit import classify_trade_blockers
from core.execution_labels import build_executable_labels
from core.outcome_calibration import load_scan_outcomes
from core.performance_metrics import return_metrics
from core.scanner import _apply_trade_execution_profile


REPLAY_VERSION = "execution-policy-replay-v1"
MIN_EVIDENCE_SIGNALS = 30


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
        "profit_factor": round(float(metrics.get("profit_loss_ratio") or 0), 2),
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
        "evidence_enforced_simulation": [],
    }
    policy_trades = {key: [] for key in policy_returns}
    executable_returns = {key: [] for key in policy_returns}
    unfilled_counts = {key: 0 for key in policy_returns}
    unfilled_reasons: Dict[str, int] = {}
    funnel = {"research": 0, "strict_strategy": 0, "strict_grade_a": 0, "current_profile_trade": 0}
    blocker_counts: Dict[str, int] = {}
    evidence_grades: Dict[str, int] = {}
    evidence_attribution: Dict[str, int] = {}
    replay_rows = []
    for _, row in candidates.iterrows():
        candidate = _row_candidate(row)
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
        decisions = {
            "current_policy": current,
            "hard_wait_a_b_shadow": common and not groups["hard"] and not groups["wait"],
            "hard_only_a_b_shadow": common and not groups["hard"],
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
               industry, resonance, strategy_type, sop_grade, pa_entry_price, pa_stop_price,
               pa_target_price, pa_risk_reward, pa_trade_action, pa_trade_setup, pa_risk_pct,
               price_action_score, price_action_regime, price_action_signal,
               price_action_pattern, price_action_entry_quality, price_action_detail
        FROM scan_history
        WHERE {date_filter} AND {research_filter}
    """), engine, params={"days": days})
    outcomes = load_scan_outcomes(engine, days=days)
    if not outcomes.empty:
        codes = outcomes["code"].astype(str).unique().tolist()
        start_date = str(pd.to_datetime(outcomes["signal_date"]).min().date())
        end_date = str((pd.to_datetime(outcomes["signal_date"]).max() + pd.Timedelta(days=20)).date())
        daily_query = text("""
            SELECT code, date AS "日期", open AS "开盘", high AS "最高", low AS "最低",
                   close AS "收盘", vol AS "成交量"
            FROM daily_k WHERE code IN :codes AND date >= :start_date AND date <= :end_date
            ORDER BY code, date
        """).bindparams(bindparam("codes", expanding=True))
        daily = pd.read_sql(daily_query, engine, params={"codes": codes, "start_date": start_date, "end_date": end_date})
        outcomes = build_executable_labels(
            outcomes, daily, max_hold_days=5, planned_order_value=5000,
            max_volume_share_pct=5.0, volume_in_lots=True,
        )
    report = build_execution_replay_report(candidates, outcomes)
    report["days"] = days
    return report
