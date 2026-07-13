"""Point-in-time executable labels for A-share signal research."""
from typing import Any, Dict

import pandas as pd


EXECUTION_MODEL_VERSION = "a-share-next-open-v2"


def daily_limit_pct(code: str, *, is_st: bool = False, limit_pct_override: float | None = None) -> float:
    if limit_pct_override is not None:
        return max(0.0, float(limit_pct_override))
    if is_st:
        return 5.0
    code = str(code or "")
    if code.startswith(("43", "83", "87", "88", "92")):
        return 30.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    return 10.0


def minimum_buy_shares(code: str) -> int:
    """Minimum initial buy quantity for the supported A-share boards."""
    return 200 if str(code or "").startswith(("688", "689")) else 100


def evaluate_execution_path(
    code: str,
    signal_close: float,
    future_bars: pd.DataFrame,
    *,
    stop_loss_pct: float = -8.0,
    take_profit_pct: float | None = None,
    max_hold_days: int = 10,
    max_open_gap_pct: float = 3.0,
    slippage_bps: float = 5.0,
    commission_rate: float = 0.00025,
    stamp_tax_rate: float = 0.0005,
    is_st: bool = False,
    limit_pct_override: float | None = None,
    adjustment_gap_pct: float = 20.0,
    planned_order_value: float | None = None,
    max_volume_share_pct: float = 5.0,
    commission_min: float = 5.0,
    volume_in_lots: bool = False,
) -> Dict[str, Any]:
    """Label one signal using next-open entry and conservative OHLC ordering.

    The first future bar is the intended entry day. A limit-up/high-gap entry is
    marked unfilled. When stop and target are both touched on one bar, stop wins.
    """
    empty = {
        "mature": False, "filled": False, "return_pct": None, "mfe_pct": None,
        "mae_pct": None, "exit_reason": None, "execution_model_version": EXECUTION_MODEL_VERSION,
    }
    if signal_close <= 0 or future_bars is None or future_bars.empty:
        return {**empty, "reason": "未来行情不足"}
    required = {"开盘", "最高", "最低", "收盘"}
    if not required.issubset(future_bars.columns):
        return {**empty, "reason": "未来行情字段不完整"}

    bars = future_bars.head(max(1, int(max_hold_days))).reset_index(drop=True)
    if len(bars) < max_hold_days:
        return {**empty, "reason": "持有期未成熟"}
    entry_raw = float(bars.loc[0, "开盘"])
    if entry_raw <= 0:
        return {**empty, "mature": True, "reason": "入场价无效"}
    open_gap = (entry_raw / signal_close - 1) * 100
    limit_pct = daily_limit_pct(code, is_st=is_st, limit_pct_override=limit_pct_override)
    first_close = float(bars.loc[0, "收盘"])
    intraday_move = abs((first_close / entry_raw - 1) * 100)
    if abs(open_gap) >= max(adjustment_gap_pct, limit_pct + 2) and intraday_move <= 8:
        return {**empty, "mature": True, "reason": "疑似复权断点", "open_gap_pct": round(open_gap, 2)}
    if open_gap >= limit_pct - 0.2:
        return {**empty, "mature": True, "reason": "涨停或近涨停无法成交", "open_gap_pct": round(open_gap, 2)}
    if open_gap > max_open_gap_pct:
        return {**empty, "mature": True, "reason": "高开超过入场上限", "open_gap_pct": round(open_gap, 2)}

    entry = entry_raw * (1 + max(0.0, slippage_bps) / 10000)
    shares = None
    participation_pct = None
    if planned_order_value is not None:
        minimum = minimum_buy_shares(code)
        shares = int(max(0.0, planned_order_value) / entry / 100) * 100
        if shares < minimum:
            return {**empty, "mature": True, "reason": "计划资金不足最低申报数量", "open_gap_pct": round(open_gap, 2)}
        if "成交量" in bars.columns:
            day_volume = float(bars.loc[0, "成交量"] or 0)
            available_shares = day_volume * 100 if volume_in_lots else day_volume
            participation_pct = shares / available_shares * 100 if available_shares > 0 else None
            if participation_pct is None or participation_pct > max(0.0, max_volume_share_pct):
                return {
                    **empty, "mature": True, "reason": "计划委托超过成交量容量上限",
                    "open_gap_pct": round(open_gap, 2),
                    "volume_participation_pct": round(participation_pct, 4) if participation_pct is not None else None,
                }
    stop = entry_raw * (1 + stop_loss_pct / 100)
    target = entry_raw * (1 + take_profit_pct / 100) if take_profit_pct is not None else None
    max_high = entry_raw
    min_low = entry_raw
    exit_raw = float(bars.loc[len(bars) - 1, "收盘"])
    exit_reason = "持有期结束"
    exit_day = len(bars)
    for idx, bar in bars.iterrows():
        low, high = float(bar["最低"]), float(bar["最高"])
        max_high, min_low = max(max_high, high), min(min_low, low)
        if idx == 0:
            # A-share T+1: the entry-day path affects MFE/MAE but cannot be sold that day.
            continue
        if low <= stop:
            # ponytail: daily OHLC cannot reveal intraday order; conservative stop-first is the ceiling.
            exit_raw = min(float(bar["开盘"]), stop) if float(bar["开盘"]) <= stop else stop
            exit_reason, exit_day = "固定止损", idx + 1
            break
        if target is not None and high >= target:
            exit_raw, exit_reason, exit_day = target, "固定止盈", idx + 1
            break

    exit_price = exit_raw * (1 - max(0.0, slippage_bps) / 10000)
    gross_return = exit_price / entry - 1
    if shares is None:
        net_return = gross_return - commission_rate * 2 - stamp_tax_rate
        fees = None
    else:
        buy_value, sell_value = entry * shares, exit_price * shares
        fees = max(buy_value * commission_rate, commission_min) + max(sell_value * commission_rate, commission_min) + sell_value * stamp_tax_rate
        net_return = (sell_value - buy_value - fees) / buy_value
    return {
        "mature": True,
        "filled": True,
        "entry_price": round(entry, 4),
        "exit_price": round(exit_price, 4),
        "return_pct": round(net_return * 100, 4),
        "mfe_pct": round((max_high / entry_raw - 1) * 100, 4),
        "mae_pct": round((min_low / entry_raw - 1) * 100, 4),
        "exit_reason": exit_reason,
        "exit_day": exit_day,
        "open_gap_pct": round(open_gap, 2),
        "shares": shares,
        "fees": round(fees, 2) if fees is not None else None,
        "volume_participation_pct": round(participation_pct, 4) if participation_pct is not None else None,
        "execution_model_version": EXECUTION_MODEL_VERSION,
    }


def build_executable_labels(signals: pd.DataFrame, daily_k: pd.DataFrame, **params: Any) -> pd.DataFrame:
    """Attach executable labels without reading beyond each signal date."""
    if signals.empty:
        return signals.copy()
    prices = daily_k.copy()
    prices["日期"] = pd.to_datetime(prices["日期"], errors="coerce")
    grouped_prices = {
        str(code): group.sort_values("日期").reset_index(drop=True)
        for code, group in prices.groupby(prices["code"].astype(str), sort=False)
    }
    output = []
    for _, signal in signals.iterrows():
        code = str(signal["code"])
        signal_date = pd.to_datetime(signal["signal_date"])
        code_prices = grouped_prices.get(code, pd.DataFrame())
        future = code_prices[code_prices["日期"] > signal_date] if not code_prices.empty else code_prices
        name = str(signal.get("name") or "")
        st_value = signal.get("is_st_or_delist")
        is_st = (pd.notna(st_value) and bool(st_value)) or "ST" in name.upper()
        label = evaluate_execution_path(code, float(signal["signal_close"]), future, is_st=is_st, **params)
        output.append({**signal.to_dict(), **{f"exec_{key}": value for key, value in label.items()}})
    return pd.DataFrame(output)
