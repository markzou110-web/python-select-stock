from typing import Any, Dict, List

import numpy as np
import pandas as pd

from core.strategy import (
    BACKTEST_ENGINE_VERSION,
    EXIT_RULE_VERSION,
    STRATEGY_LOGIC_VERSION,
    _find_consensus_signal_indices,
    _find_pine_signal_indices,
    _find_squeeze_signal_indices,
    _find_tv_zp_signal_indices,
    _get_signal_reason,
)


def _empty_result(reason: str = "无有效信号") -> Dict[str, Any]:
    return {
        "summary": {
            "signal_count": 0,
            "win_rate": 0,
            "avg_return": 0,
            "max_drawdown": 0,
            "profit_factor": 0,
            "avg_hold_days": 0,
            "total_return": 0,
            "final_equity": 100000,
        },
        "trades": [],
        "equity_curve": [],
        "reason": reason,
        "versions": {
            "strategy_logic_version": STRATEGY_LOGIC_VERSION,
            "backtest_engine_version": BACKTEST_ENGINE_VERSION,
            "exit_rule_version": EXIT_RULE_VERSION,
        },
    }


def _signal_indices(df: pd.DataFrame, strategy_type: str, params: Dict[str, Any]) -> List[int]:
    if strategy_type == "tv_zp":
        long_indices, _short_indices, _debug = _find_tv_zp_signal_indices(df)
        return long_indices
    if strategy_type == "pine":
        return _find_pine_signal_indices(df, int(params.get("pine_min_signals", 3)))
    if strategy_type == "consensus":
        return _find_consensus_signal_indices(df)
    return _find_squeeze_signal_indices(
        df,
        threshold=float(params.get("threshold", 0.12)),
        vol_multiplier=float(params.get("vol_multiplier", 1.5)),
        rsi_min=int(params.get("rsi_min", 55)),
        use_macd_filter=bool(params.get("use_macd_filter", True)),
        use_bb_sqz=bool(params.get("use_bb_sqz", True)),
        sqz_lookback=int(params.get("sqz_lookback", 10)),
        use_rs_filter=bool(params.get("use_rs_filter", True)),
    )


def run_single_stock_backtest(
    df: pd.DataFrame,
    strategy_type: str = "squeeze",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Event-style single-stock backtest with one open position at a time.

    The engine uses same strategy signal functions as scanning, applies fixed stop,
    ATR trailing stop, optional time stop, and A-share friction costs.
    """
    params = params or {}
    if df is None or df.empty:
        return _empty_result("历史行情为空")

    required_cols = {"日期", "开盘", "最高", "最低", "收盘", "成交量"}
    if not required_cols.issubset(df.columns):
        return _empty_result("历史行情字段不完整")

    df = df.reset_index(drop=True)
    if len(df) < 80:
        return _empty_result("历史数据不足")

    max_hold_days = int(params.get("max_hold_days", 10))
    stop_loss_pct = float(params.get("stop_loss_pct", -8.0))
    trailing_multiplier = float(params.get("trailing_multiplier", 2.2))
    initial_capital = float(params.get("capital", 100000))
    time_stop_days = params.get("time_stop_days")
    time_stop_days = int(time_stop_days) if time_stop_days else None

    raw_signals = [idx for idx in _signal_indices(df, strategy_type, params) if idx < len(df) - 1]
    if not raw_signals:
        return _empty_result("回测区间内无策略信号")

    close_vals = df["收盘"].astype(float).to_numpy()
    high_vals = df["最高"].astype(float).to_numpy()
    low_vals = df["最低"].astype(float).to_numpy()
    atr_vals = df["ATR"].astype(float).to_numpy() if "ATR" in df.columns else None
    dates = df["日期"].astype(str).str[:10].tolist()

    stamp_tax_rate = 0.0005
    commission_rate = 0.00025
    commission_min = 5.0

    equity = initial_capital
    trades: List[Dict[str, Any]] = []
    equity_curve = [{"date": dates[0], "equity": round(equity, 2)}]
    next_allowed_idx = 0
    max_equity = initial_capital
    max_drawdown = 0.0

    for idx in raw_signals:
        if idx < next_allowed_idx:
            continue

        entry_price = close_vals[idx]
        if entry_price <= 0:
            continue

        atr = atr_vals[idx] if atr_vals is not None and not np.isnan(atr_vals[idx]) else entry_price * 0.03
        stop_ratio = stop_loss_pct / 100.0
        max_close_since_entry = entry_price
        exit_price = entry_price
        exit_reason = "超时平仓"
        hold_days = 0
        hit_stop = False
        time_stopped = False

        for day in range(1, max_hold_days + 1):
            future_idx = idx + day
            if future_idx >= len(df):
                hold_days = max(day - 1, 0)
                exit_price = close_vals[min(idx + hold_days, len(df) - 1)]
                exit_reason = "数据结束"
                break

            day_close = close_vals[future_idx]
            day_low = low_vals[future_idx]
            hold_days = day

            if (day_low - entry_price) / entry_price <= stop_ratio:
                exit_price = entry_price * (1 + stop_ratio)
                exit_reason = f"固定止损 {stop_loss_pct}%"
                hit_stop = True
                break

            max_close_since_entry = max(max_close_since_entry, day_close)
            trailing_stop = max_close_since_entry - atr * trailing_multiplier
            if day_close < trailing_stop:
                exit_price = day_close
                exit_reason = "ATR移动止盈"
                break

            current_return = (day_close - entry_price) / entry_price
            if time_stop_days and day >= time_stop_days and current_return <= 0:
                exit_price = day_close
                exit_reason = "时间止损"
                time_stopped = True
                break

            if day == max_hold_days:
                exit_price = day_close

        exit_idx = min(idx + hold_days, len(df) - 1)
        shares = max(int(equity / entry_price / 100) * 100, 100)
        buy_cost = entry_price * shares
        sell_cost = exit_price * shares
        buy_commission = max(buy_cost * commission_rate, commission_min)
        sell_commission = max(sell_cost * commission_rate, commission_min)
        stamp_tax = sell_cost * stamp_tax_rate
        gross_pnl = sell_cost - buy_cost
        net_pnl = gross_pnl - buy_commission - sell_commission - stamp_tax
        return_pct = net_pnl / buy_cost * 100 if buy_cost > 0 else 0
        equity += net_pnl
        max_equity = max(max_equity, equity)
        drawdown = (equity - max_equity) / max_equity * 100 if max_equity > 0 else 0
        max_drawdown = min(max_drawdown, drawdown)

        trades.append({
            "entry_date": dates[idx],
            "exit_date": dates[exit_idx],
            "entry_price": round(float(entry_price), 2),
            "exit_price": round(float(exit_price), 2),
            "return_pct": round(float(return_pct), 2),
            "hold_days": hold_days,
            "exit_reason": exit_reason,
            "signal_reason": _get_signal_reason(df, idx, strategy_type),
            "net_pnl": round(float(net_pnl), 2),
            "hit_stop": hit_stop,
            "time_stopped": time_stopped,
        })
        equity_curve.append({"date": dates[exit_idx], "equity": round(float(equity), 2)})
        next_allowed_idx = exit_idx + 1

    if not trades:
        return _empty_result("信号因流动性或仓位约束未成交")

    returns = [trade["return_pct"] for trade in trades]
    wins = [ret for ret in returns if ret > 0]
    losses = [ret for ret in returns if ret < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0)

    return {
        "summary": {
            "signal_count": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "avg_return": round(float(np.mean(returns)), 2),
            "max_drawdown": round(float(max_drawdown), 2),
            "profit_factor": min(profit_factor, 99.0),
            "avg_hold_days": round(float(np.mean([trade["hold_days"] for trade in trades])), 1),
            "total_return": round((equity - initial_capital) / initial_capital * 100, 2),
            "final_equity": round(float(equity), 2),
            "stop_loss_hits": sum(1 for trade in trades if trade["hit_stop"]),
            "time_stopped": sum(1 for trade in trades if trade["time_stopped"]),
        },
        "trades": trades,
        "equity_curve": equity_curve,
        "reason": "",
        "versions": {
            "strategy_logic_version": STRATEGY_LOGIC_VERSION,
            "backtest_engine_version": BACKTEST_ENGINE_VERSION,
            "exit_rule_version": EXIT_RULE_VERSION,
        },
    }
