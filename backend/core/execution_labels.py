"""Point-in-time executable labels for A-share signal research."""
import json
from typing import Any, Dict

import pandas as pd


EXECUTION_MODEL_VERSION = "a-share-confirmation-trigger-v4"


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
    planned_entry_price: float | None = None,
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
    planned_stop_price: float | None = None,
    planned_target_price: float | None = None,
) -> Dict[str, Any]:
    """Label one signal using a T+1 entry plan and conservative OHLC ordering.

    The first future bar is the only valid entry day. Entry failures are mature
    once that bar exists; filled trades still need the full evaluation horizon.
    When stop and target are both touched on one bar, stop wins.
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
    first_open = float(bars.loc[0, "开盘"])
    if first_open <= 0:
        return {**empty, "mature": True, "reason": "入场价无效"}
    open_gap = (first_open / signal_close - 1) * 100
    limit_pct = daily_limit_pct(code, is_st=is_st, limit_pct_override=limit_pct_override)
    first_close = float(bars.loc[0, "收盘"])
    intraday_move = abs((first_close / first_open - 1) * 100)
    if abs(open_gap) >= max(adjustment_gap_pct, limit_pct + 2) and intraday_move <= 8:
        return {**empty, "mature": True, "reason": "疑似复权断点", "open_gap_pct": round(open_gap, 2)}
    if open_gap >= limit_pct - 0.2:
        return {**empty, "mature": True, "reason": "涨停或近涨停无法成交", "open_gap_pct": round(open_gap, 2)}
    planned_entry = float(planned_entry_price or 0)
    entry_extension_pct = None
    if planned_entry > 0:
        if first_open > planned_entry:
            entry_extension_pct = (first_open / planned_entry - 1) * 100
            if entry_extension_pct > max_open_gap_pct:
                return {
                    **empty,
                    "mature": True,
                    "reason": "开盘超过确认价偏离上限",
                    "open_gap_pct": round(open_gap, 2),
                    "entry_extension_pct": round(entry_extension_pct, 2),
                    "entry_trigger_price": round(planned_entry, 4),
                }
            entry_raw = first_open
        else:
            first_high = float(bars.loc[0, "最高"])
            if first_high < planned_entry:
                return {
                    **empty,
                    "mature": True,
                    "reason": "确认价未触及",
                    "open_gap_pct": round(open_gap, 2),
                    "entry_trigger_price": round(planned_entry, 4),
                }
            entry_raw = planned_entry
            entry_extension_pct = 0.0
    else:
        entry_raw = first_open
    if planned_entry <= 0 and open_gap > max_open_gap_pct:
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
    if len(bars) < max_hold_days:
        return {**empty, "reason": "持有期未成熟"}
    use_structural_stop = planned_stop_price is not None and 0 < float(planned_stop_price) < entry_raw
    use_structural_target = planned_target_price is not None and float(planned_target_price) > entry_raw
    stop = float(planned_stop_price) if use_structural_stop else entry_raw * (1 + stop_loss_pct / 100)
    target = (
        float(planned_target_price) if use_structural_target
        else entry_raw * (1 + take_profit_pct / 100) if take_profit_pct is not None
        else None
    )
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
            exit_reason, exit_day = "结构止损" if use_structural_stop else "固定止损", idx + 1
            break
        if target is not None and high >= target:
            exit_raw, exit_reason, exit_day = target, "结构目标" if use_structural_target else "固定止盈", idx + 1
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
        "entry_extension_pct": round(entry_extension_pct, 2) if entry_extension_pct is not None else None,
        "entry_trigger_price": round(planned_entry, 4) if planned_entry > 0 else None,
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
        call_params = dict(params)
        detail = signal.get("price_action_detail")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except (ValueError, TypeError):
                detail = {}
        detail = detail if isinstance(detail, dict) else {}
        if "planned_entry_price" not in call_params:
            planned_entry = next((signal.get(key) for key in ("planned_entry_price", "pa_entry_price", "confirmation_price") if signal.get(key) is not None and not pd.isna(signal.get(key))), None)
            if planned_entry is None:
                planned_entry = detail.get("pa_entry_price") or detail.get("entry_price")
            try:
                if planned_entry is not None and not pd.isna(planned_entry) and float(planned_entry) > 0:
                    call_params["planned_entry_price"] = float(planned_entry)
            except (TypeError, ValueError):
                pass
        for param, keys in (
            ("planned_stop_price", ("pa_stop_price", "planned_stop_price", "stop_price")),
            ("planned_target_price", ("pa_target_price", "planned_target_price", "target_price")),
        ):
            if param in call_params:
                continue
            value = next((signal.get(key) for key in keys if signal.get(key) is not None and not pd.isna(signal.get(key))), None)
            if value is None:
                value = next((detail.get(key) for key in keys if detail.get(key) is not None), None)
            try:
                if value is not None and float(value) > 0:
                    call_params[param] = float(value)
            except (TypeError, ValueError):
                pass
        label = evaluate_execution_path(code, float(signal["signal_close"]), future, is_st=is_st, **call_params)
        output.append({**signal.to_dict(), **{f"exec_{key}": value for key, value in label.items()}})
    return pd.DataFrame(output)
