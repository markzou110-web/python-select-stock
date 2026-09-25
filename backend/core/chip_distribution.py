"""Estimated A-share holder-cost distribution from OHLC and daily turnover."""

from __future__ import annotations

from typing import Any

import pandas as pd


MODEL_VERSION = "turnover_decay_v1"
DEFAULT_LOOKBACK_DAYS = 120
DEFAULT_BINS = 80
MIN_HISTORY_DAYS = 20
MIN_TURNOVER_COVERAGE = 0.8


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason, "model": MODEL_VERSION}


def _weighted_price(prices: list[float], weights: list[float], quantile: float) -> float:
    target = sum(weights) * quantile
    cumulative = 0.0
    for price, weight in zip(prices, weights):
        cumulative += weight
        if cumulative >= target:
            return price
    return prices[-1]


def _calculate_profile(frame: pd.DataFrame, bins: int) -> tuple[list[float], list[float]]:
    price_min = float(frame["最低"].min())
    price_max = float(frame["最高"].max())
    if price_min <= 0 or price_max <= price_min:
        return [], []
    step = (price_max - price_min) / (bins - 1)
    prices = [price_min + step * index for index in range(bins)]
    weights = [0.0] * bins

    for _, row in frame.iterrows():
        low = float(row["最低"])
        high = float(row["最高"])
        average = (float(row["开盘"]) + float(row["收盘"]) + high + low) / 4
        turnover = min(1.0, max(0.0, float(row["换手率"]) / 100))
        weights = [weight * (1 - turnover) for weight in weights]
        if turnover <= 0:
            continue

        if high <= low:
            index = min(bins - 1, max(0, round((average - price_min) / step)))
            weights[index] += turnover
            continue

        day_weights = []
        for price in prices:
            if price < low or price > high:
                day_weights.append(0.0)
            elif price <= average:
                width = max(average - low, step)
                day_weights.append(max(0.0, (price - low + step / 2) / width))
            else:
                width = max(high - average, step)
                day_weights.append(max(0.0, (high - price + step / 2) / width))
        day_total = sum(day_weights)
        if day_total <= 0:
            index = min(bins - 1, max(0, round((average - price_min) / step)))
            weights[index] += turnover
        else:
            weights = [
                existing + turnover * day_weight / day_total
                for existing, day_weight in zip(weights, day_weights)
            ]

    total = sum(weights)
    if total <= 0:
        return [], []
    return prices, [weight / total for weight in weights]


def _dominant_peaks(prices: list[float], weights: list[float], limit: int = 3) -> list[dict[str, float]]:
    candidates = []
    for index, weight in enumerate(weights):
        left = weights[index - 1] if index > 0 else -1
        right = weights[index + 1] if index + 1 < len(weights) else -1
        if weight >= left and weight >= right:
            candidates.append((index, weight))
    selected = []
    for index, weight in sorted(candidates, key=lambda item: item[1], reverse=True):
        if any(abs(index - chosen) < 4 for chosen, _ in selected):
            continue
        selected.append((index, weight))
        if len(selected) >= limit:
            break
    return [
        {"price": round(prices[index], 2), "weight": round(weight, 6)}
        for index, weight in selected
    ]


def _derive_impacts(*, current_price: float, average_cost: float, cost_70_low: float,
                    cost_70_high: float, cost_90_low: float, migration: str,
                    concentration_70: float) -> dict[str, Any]:
    """Translate chip migration into bounded decision hints, never orders."""
    if migration == "数据积累中":
        return {"buy_impact": "数据不足", "holding_impact": "观察", "score_delta": 0,
                "buy_reason": "筹码迁移历史不足，不能用于确认买点",
                "holding_reason": "继续使用价格、均线和硬止损规则"}
    in_zone = cost_70_low <= current_price <= cost_70_high
    below_90 = current_price < cost_90_low
    concentrated = concentration_70 <= 0.35
    if migration == "筹码上移" and in_zone and concentrated:
        buy_impact, score_delta = "确认加分", 6
        buy_reason = "筹码峰上移且价格回到集中成本区，回踩买点质量较好"
    elif migration == "筹码下移" or below_90:
        buy_impact, score_delta = "抑制买入", -8
        buy_reason = "筹码峰下移或价格跌破90%成本下沿，买点风险偏高"
    elif current_price > cost_70_high:
        buy_impact, score_delta = "等待回踩", -2
        buy_reason = "价格已高于主要成本区，等待缩量回踩后再确认"
    else:
        buy_impact, score_delta = "中性观察", 0
        buy_reason = "筹码迁移未形成明确优势，等待价格行为确认"
    if migration == "筹码上移" and current_price >= average_cost:
        holding_impact, holding_reason = "持有", "成本中枢上移且价格位于平均成本上方"
    elif migration == "筹码下移" and below_90:
        holding_impact, holding_reason = "止损复核", "筹码下移并跌破90%成本下沿，优先复核硬止损"
    else:
        holding_impact, holding_reason = "收紧风控", "价格与筹码成本区共振偏弱，降低加仓意愿并收紧跟踪止损"
    return {"buy_impact": buy_impact, "holding_impact": holding_impact,
            "score_delta": score_delta, "buy_reason": buy_reason,
            "holding_reason": holding_reason}


def build_chip_distribution(
    frame: pd.DataFrame,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    bins: int = DEFAULT_BINS,
) -> dict[str, Any]:
    """Build a normalized cost profile; never infer chips without turnover data."""
    if frame is None or frame.empty or "换手率" not in frame.columns:
        return _unavailable("historical_turnover_unavailable")
    required = ["日期", "开盘", "最高", "最低", "收盘", "换手率"]
    if any(column not in frame.columns for column in required):
        return _unavailable("incomplete_ohlc_data")

    work = frame[required].copy()
    for column in required[1:]:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=["开盘", "最高", "最低", "收盘"])
    work = work[work["收盘"] > 0].tail(max(MIN_HISTORY_DAYS, int(lookback_days)))
    if len(work) < MIN_HISTORY_DAYS:
        return _unavailable("insufficient_history")
    turnover_coverage = float(work["换手率"].notna().mean())
    if turnover_coverage < MIN_TURNOVER_COVERAGE:
        return _unavailable("historical_turnover_unavailable")
    work["换手率"] = work["换手率"].fillna(0).clip(lower=0, upper=100)

    prices, weights = _calculate_profile(work, max(50, min(160, int(bins))))
    if not prices:
        return _unavailable("profile_calculation_failed")

    current_price = float(work.iloc[-1]["收盘"])
    average_cost = sum(price * weight for price, weight in zip(prices, weights))
    cost_70_low = _weighted_price(prices, weights, 0.15)
    cost_70_high = _weighted_price(prices, weights, 0.85)
    cost_90_low = _weighted_price(prices, weights, 0.05)
    cost_90_high = _weighted_price(prices, weights, 0.95)
    profit_ratio = sum(weight for price, weight in zip(prices, weights) if price <= current_price)
    peaks = _dominant_peaks(prices, weights)
    peak_price = peaks[0]["price"] if peaks else round(prices[weights.index(max(weights))], 2)

    prior_peak = None
    peak_change_pct = None
    migration = "数据积累中"
    if len(work) >= 40:
        prior_prices, prior_weights = _calculate_profile(work.iloc[:-20], len(prices))
        if prior_prices:
            prior_peak = prior_prices[prior_weights.index(max(prior_weights))]
            peak_change_pct = (peak_price - prior_peak) / prior_peak * 100 if prior_peak > 0 else 0
            migration = "筹码上移" if peak_change_pct >= 1.5 else "筹码下移" if peak_change_pct <= -1.5 else "筹码稳定"

    concentration_70 = (cost_70_high - cost_70_low) / max(cost_70_high + cost_70_low, 0.01)
    impacts = _derive_impacts(
        current_price=current_price,
        average_cost=average_cost,
        cost_70_low=cost_70_low,
        cost_70_high=cost_70_high,
        cost_90_low=cost_90_low,
        migration=migration,
        concentration_70=concentration_70,
    )

    return {
        "available": True,
        "model": MODEL_VERSION,
        "source": "estimated_from_turnover",
        "as_of": str(work.iloc[-1]["日期"]),
        "lookback_days": len(work),
        "current_price": round(current_price, 2),
        "price_min": round(prices[0], 2),
        "price_max": round(prices[-1], 2),
        "peak_price": round(peak_price, 2),
        "average_cost": round(average_cost, 2),
        "profit_ratio": round(profit_ratio, 4),
        "cost_70_low": round(cost_70_low, 2),
        "cost_70_high": round(cost_70_high, 2),
        "cost_90_low": round(cost_90_low, 2),
        "cost_90_high": round(cost_90_high, 2),
        "concentration_70": round(concentration_70, 4),
        "concentration_90": round((cost_90_high - cost_90_low) / max(cost_90_high + cost_90_low, 0.01), 4),
        "migration": migration,
        "peak_change_20d_pct": round(peak_change_pct, 2) if peak_change_pct is not None else None,
        "previous_peak_price": round(prior_peak, 2) if prior_peak is not None else None,
        **impacts,
        "peaks": peaks,
        "bars": [
            {
                "price": round(price, 2),
                "weight": round(weight, 8),
                "profitable": price <= current_price,
            }
            for price, weight in zip(prices, weights)
        ],
        "disclaimer": "筹码分布由成交量和换手率估算，不代表真实账户持仓或主力成本。",
    }
