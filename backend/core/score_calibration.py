from collections import defaultdict
from typing import Any, Dict, List
import math

from core.risk_constants import (
    A_MINUS_TRIAL_MAX_DAILY_RISE_PCT,
    SOP_A_GRADE_MAX_5D_GAIN_PCT,
)


# execution-first-v2：近期校准显示原始策略分和机会分存在排序倒挂，
# 因此只把它们作为辅助项，排序主要依赖价格行为与板块联动。
# 该分数只改变候选排序，不放宽任何交易许可门禁。
W_STRATEGY_PERCENTILE = 0.15
W_PRICE_ACTION = 0.50
W_SECTOR_ALIGNMENT = 0.20
W_TRADE_OPPORTUNITY = 0.10
W_HISTORICAL_WIN_RATE = 0.05
MAX_EXTENSION_PENALTY = 15.0


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return default


def apply_score_display_contract(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add bounded presentation scores without changing any internal score or ranking."""
    def display(value: Any) -> float | None:
        return round(_clamp(value), 1) if value not in (None, "") else None

    for row in results:
        signal_score = row.get("calibrated_score")
        if signal_score in (None, ""):
            signal_score = row.get("Score")
        row["display_signal_score"] = display(signal_score)
        row["display_quality_score"] = display(row.get("sop_quality_score"))
        row["display_opportunity_score"] = display(row.get("trade_opportunity_score"))
        row["display_rank_score"] = display(row.get("final_rank_score"))
        row["display_trade_score"] = display(row.get("final_trade_score"))
        row["score_display_scale"] = "0-100"
    return results


def _percentile_scores(values: List[float]) -> List[float]:
    if len(values) <= 1:
        return [50.0] * len(values)
    # 修复 BUG4：原用 list.index(value) 对并列分数返回首个索引，导致同分股票百分位错乱。
    # 改用 rank（每个值在排序序列中的位置），并列分数取平均排名。
    ordered = sorted(values)
    n = len(ordered)
    result = []
    for v in values:
        # 找所有等于 v 的位置，取平均排名
        positions = [i for i, ov in enumerate(ordered) if ov == v]
        avg_rank = sum(positions) / len(positions)
        result.append(round(100 * avg_rank / (n - 1), 1))
    return result


def _parse_win_rate(row: Dict[str, Any]) -> float:
    """从结果行解析历史胜率，优先用可靠性折扣后的 Wilson 下界胜率。

    优先级：
      1. 回测统计.adjusted_win_rate（Wilson 99% 下界，已含小样本惩罚）—— 与 scanner 层一致。
      2. 原始 历史胜率 字符串（回测统计缺失时兜底）。
      3. 无任何胜率数据 → 中性 50（不拉高也不拉低）。

    改动（提高胜率区分度）：原实现无视样本量，2 笔交易 100% 胜率 与 30 笔 100% 同分，
    且 0%（真无胜率数据的历史遗留）会被当作"真实 0%"严重压分。改为优先用 Wilson 下界，
    既惩罚小样本虚高、也避免把"无数据"误判为"必输"。
    """
    bt = row.get("回测统计") or {}
    adj = bt.get("adjusted_win_rate")
    if adj is not None:
        try:
            return _clamp(float(adj))
        except (TypeError, ValueError):
            pass
    raw = row.get("历史胜率")
    if raw is None:
        raw = row.get("win_rate", row.get("historical_win_rate"))
    try:
        return _clamp(float(str(raw).replace("%", "").strip()))
    except (TypeError, ValueError):
        return 50.0


def beta_binomial_probability(wins: float, trials: int, alpha: float = 2.0, beta: float = 2.0) -> Dict[str, float]:
    """Small-sample shrinkage with an approximate 95% credible interval."""
    trials = max(0, int(trials))
    wins = max(0.0, min(float(wins), trials))
    a, b = alpha + wins, beta + trials - wins
    mean = a / (a + b)
    variance = a * b / (((a + b) ** 2) * (a + b + 1))
    margin = 1.96 * math.sqrt(variance)
    return {
        "p_win": round(mean, 4),
        "ci95_low": round(max(0.0, mean - margin), 4),
        "ci95_high": round(min(1.0, mean + margin), 4),
        "trials": trials,
        "model_version": "beta-binomial-2-2-v1",
    }


def calibrate_scan_scores(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert strategy-specific raw scores into a comparable 0-100 score."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in results:
        grouped[str(row.get("strategy_type") or "unknown")].append(row)

    for rows in grouped.values():
        raw_values = [float(row.get("Score") or 0) for row in rows]
        percentiles = _percentile_scores(raw_values)
        for row, raw_score, percentile in zip(rows, raw_values, percentiles):
            plan = row.get("pa_trade_plan") or {}
            if not row.get("pa_trade_action") and plan.get("action"):
                row["pa_trade_action"] = plan["action"]
            structure = _clamp(row.get("pa_structure_score"), _clamp(row.get("price_action_score"), 50))
            execution = _clamp(row.get("pa_execution_score"), 50)
            safety = _clamp(row.get("pa_risk_score"), 50)
            price_action_composite = round(structure * 0.4 + execution * 0.35 + safety * 0.25, 1)
            historical_win_rate = _parse_win_rate(row)
            backtest_stats = row.get("回测统计") or {}
            # The Wilson bound is a ranking penalty, not an observed success rate.
            raw_rate = backtest_stats.get("win_rate")
            try:
                raw_rate = float(raw_rate)
                count = float(backtest_stats.get("signal_count") or backtest_stats.get("total_signals") or 0)
                raw_wins = float(backtest_stats.get("win_count")) if backtest_stats.get("win_count") is not None else None
                valid = (math.isfinite(raw_rate) and 0 <= raw_rate <= 100
                         and math.isfinite(count) and count > 0 and count.is_integer())
                if raw_wins is not None:
                    valid = valid and math.isfinite(raw_wins) and 0 <= raw_wins <= count
                trials = int(count) if valid else 0
            except (TypeError, ValueError, OverflowError):
                raw_wins = None
                trials = 0
            # New summaries provide exact win counts; legacy summaries only expose
            # a rounded rate, so use the fractional equivalent as a fallback.
            wins = raw_wins if trials and raw_wins is not None else (raw_rate / 100 * trials if trials else 0)
            probability = beta_binomial_probability(wins, trials)
            probability["evidence_source"] = "raw_backtest_win_rate" if trials else "prior_only"
            probability["model_version"] = "beta-binomial-raw-rate-v2"
            components = {
                "strategy_percentile": percentile,
                "price_action": price_action_composite,
                "price_action_structure": structure,
                "price_action_execution": execution,
                "price_action_safety": safety,
                "sector_alignment": _clamp(row.get("sector_alignment_score"), 50),
                "trade_opportunity": _clamp(row.get("trade_opportunity_score"), 50),
                "historical_win_rate": historical_win_rate,
            }
            pct_5d = _clamp(row.get("pct_5d"), 0)
            daily_rise = _clamp(row.get("涨幅%") or row.get("pct_chg"), 0)
            extension_penalty = min(
                MAX_EXTENSION_PENALTY,
                max(0.0, pct_5d - SOP_A_GRADE_MAX_5D_GAIN_PCT) * 1.5
                + (8.0 if daily_rise > A_MINUS_TRIAL_MAX_DAILY_RISE_PCT else 0.0),
            )
            components["extension_penalty"] = round(extension_penalty, 1)
            base_calibrated = (
                components["strategy_percentile"] * W_STRATEGY_PERCENTILE
                + components["price_action"] * W_PRICE_ACTION
                + components["sector_alignment"] * W_SECTOR_ALIGNMENT
                + components["trade_opportunity"] * W_TRADE_OPPORTUNITY
                + components["historical_win_rate"] * W_HISTORICAL_WIN_RATE
            )
            calibrated = round(max(0.0, base_calibrated - extension_penalty), 1)
            ranking_delta = round(calibrated - raw_score, 1)
            row["raw_score"] = round(raw_score, 2)
            row["calibrated_score"] = calibrated
            row["score_components"] = components
            row["score_model_version"] = "execution-first-v2"
            row["score_ranking_delta"] = ranking_delta
            row["win_probability"] = probability
            row["p_win"] = round(probability["p_win"] * 100, 1)
            row["Score"] = calibrated
            for rank_field in ("final_rank_score", "final_trade_score"):
                if row.get(rank_field) is not None:
                    row[rank_field] = round(float(row[rank_field]) + ranking_delta, 2)

            missing = []
            if not row.get("strategy_type"):
                missing.append("strategy_type")
            if not row.get("pa_trade_action"):
                missing.append("pa_trade_action")
            if str(row.get("trade_bucket") or "UNKNOWN") in {"UNKNOWN", ""}:
                missing.append("trade_bucket")
            if str(row.get("market_regime") or "UNKNOWN") in {"UNKNOWN", ""}:
                missing.append("market_regime")
            row["research_missing_fields"] = missing
            row["research_eligible"] = not missing
    return results
