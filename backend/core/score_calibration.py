from collections import defaultdict
from typing import Any, Dict, List


# 综合评分权重（求和=1.0）。历史胜率（historical_win_rate）专项 0.10，
# 让历史回测胜率高的标的在排序中获得加权优势，而非仅作二元 SOP 勾选。
# 便于调参：调整后请确保五项权重之和仍为 1.0。
# 修复 L-决策2: 板块影响双重计算。trade_opportunity 内部已含 sector_score(权重0.2×0.25=0.05)，
# 外部又加 sector_alignment(0.12)，总板块影响=0.17(17%)过高。
# 将 sector_alignment 从 0.12 降至 0.07，总板块影响=0.07+0.05=0.12(合理)，
# 释放的 0.05 转给 price_action(0.18→0.23)以加强个股技术面权重。
# 改动 A5：W_HISTORICAL_WIN_RATE 0.10→0.05（与 scanner 层 0.18 权重双重计入，
# 实际权重远超 0.28），释放给 W_TRADE_OPPORTUNITY(0.25→0.30)，后者信息密度更高
# （含市场+板块+资金+风险综合判断）。
W_STRATEGY_PERCENTILE = 0.35
W_PRICE_ACTION = 0.23
W_SECTOR_ALIGNMENT = 0.07
W_TRADE_OPPORTUNITY = 0.30
W_HISTORICAL_WIN_RATE = 0.05


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return default


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
    """从结果行解析历史胜率。scanner 以中文字符串 '58.0%' 存于 '历史胜率' 键。

    无胜率数据时返回中性值 50（不拉高也不拉低该标的的 win_rate 分量）。
    """
    raw = row.get("历史胜率")
    if raw is None:
        # 兼容可能的英文键
        raw = row.get("win_rate", row.get("historical_win_rate"))
    try:
        return _clamp(float(str(raw).replace("%", "").strip()), 50.0)
    except (TypeError, ValueError):
        return 50.0


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
            calibrated = round(
                components["strategy_percentile"] * W_STRATEGY_PERCENTILE
                + components["price_action"] * W_PRICE_ACTION
                + components["sector_alignment"] * W_SECTOR_ALIGNMENT
                + components["trade_opportunity"] * W_TRADE_OPPORTUNITY
                + components["historical_win_rate"] * W_HISTORICAL_WIN_RATE,
                1,
            )
            row["raw_score"] = round(raw_score, 2)
            row["calibrated_score"] = calibrated
            row["score_components"] = components
            row["Score"] = calibrated

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
