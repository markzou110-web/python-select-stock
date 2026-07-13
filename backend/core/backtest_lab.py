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
            "skipped_high_open": 0,
            "skipped_limit_up": 0,
            "skipped_adjustment_gap": 0,
            "skipped_capital": 0,
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


def _is_suspected_adjustment_gap(
    prev_close: float,
    open_price: float,
    close_price: float,
    threshold_pct: float,
) -> bool:
    if prev_close <= 0 or open_price <= 0 or close_price <= 0:
        return False
    close_gap = (close_price - prev_close) / prev_close * 100
    open_gap = (open_price - prev_close) / prev_close * 100
    intraday_move = abs((close_price - open_price) / open_price * 100)
    return abs(close_gap) >= threshold_pct and abs(open_gap) >= threshold_pct * 0.7 and intraday_move <= 8


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


def _compute_benchmark_fields(bench_df, strategy_total_return: float, trading_days: int):
    """改动 #15：计算基准收益/alpha/CAGR。返回 (benchmark_return, alpha, cagr)。

    无 bench_df 或数据不足时返回 (None, None, None)。无论策略是否有交易都可计算
    （benchmark 是市场收益，与策略成交与否无关）。
    """
    if bench_df is None or bench_df.empty or "收盘" not in bench_df.columns:
        return None, None, None
    try:
        import pandas as _pd
        bclose = _pd.to_numeric(bench_df["收盘"], errors="coerce").dropna()
        if len(bclose) < 2:
            return None, None, None
        benchmark_return = round((float(bclose.iloc[-1]) / float(bclose.iloc[0]) - 1) * 100, 2)
        alpha = round(strategy_total_return - benchmark_return, 2)
        # CAGR：按交易日数年化（约 244 交易日/年）
        years = trading_days / 244.0 if trading_days > 0 else 1.0
        cagr = None
        if years > 0 and strategy_total_return is not None:
            ratio = 1.0 + strategy_total_return / 100.0
            if ratio > 0:
                cagr = round((ratio ** (1.0 / years) - 1) * 100, 2)
        return benchmark_return, alpha, cagr
    except Exception:
        return None, None, None


def run_single_stock_backtest(
    df: pd.DataFrame,
    strategy_type: str = "squeeze",
    params: Dict[str, Any] | None = None,
    bench_df: pd.DataFrame | None = None,
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
    entry_mode = str(params.get("entry_mode") or "signal_close")
    max_open_gap_pct = float(params.get("max_open_gap_pct", 3.0))
    limit_up_gap_pct = float(params.get("limit_up_gap_pct", 9.5))
    time_stop_days = params.get("time_stop_days")
    time_stop_days = int(time_stop_days) if time_stop_days else None
    slippage_bps = max(0.0, float(params.get("slippage_bps", 5.0)))
    position_pct = min(1.0, max(0.05, float(params.get("position_pct", 1.0))))
    lot_size = max(1, int(params.get("lot_size", 100)))
    skip_adjustment_gaps = bool(params.get("skip_adjustment_gaps", True))
    adjustment_gap_pct = max(10.0, float(params.get("adjustment_gap_pct", 20.0)))

    raw_signals = [idx for idx in _signal_indices(df, strategy_type, params) if idx < len(df) - 1]
    signal_start_date = params.get("signal_start_date")
    signal_end_date = params.get("signal_end_date")
    if signal_start_date or signal_end_date:
        signal_dates = df["日期"].astype(str).str[:10]
        raw_signals = [
            idx for idx in raw_signals
            if (not signal_start_date or signal_dates.iloc[idx] >= str(signal_start_date)[:10])
            and (not signal_end_date or signal_dates.iloc[idx] <= str(signal_end_date)[:10])
        ]
    if not raw_signals:
        # 改动 #15：即使无信号也返回 benchmark（市场收益与策略信号无关）
        _br, _al, _cg = _compute_benchmark_fields(bench_df, 0.0, len(df))
        _empty = _empty_result("回测区间内无策略信号")
        _empty["summary"].update({"benchmark_return": _br, "alpha": _al, "cagr": _cg})
        return _empty

    close_vals = df["收盘"].astype(float).to_numpy()
    open_vals = df["开盘"].astype(float).to_numpy()
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
    skipped_high_open = 0
    skipped_limit_up = 0
    skipped_adjustment_gap = 0
    skipped_capital = 0

    for idx in raw_signals:
        if idx < next_allowed_idx:
            continue

        signal_price = close_vals[idx]
        entry_idx = idx
        entry_price = signal_price
        open_gap_pct = None
        if entry_mode == "next_open_confirm":
            entry_idx = idx + 1
            if entry_idx >= len(df):
                continue
            entry_price = open_vals[entry_idx]
            if signal_price > 0:
                open_gap_pct = (entry_price - signal_price) / signal_price * 100
                if open_gap_pct >= limit_up_gap_pct:
                    skipped_limit_up += 1
                    next_allowed_idx = entry_idx + 1
                    continue
                if open_gap_pct > max_open_gap_pct:
                    skipped_high_open += 1
                    next_allowed_idx = entry_idx + 1
                    continue

        if entry_price <= 0:
            continue
        if (
            skip_adjustment_gaps
            and entry_idx > 0
            and _is_suspected_adjustment_gap(
                close_vals[entry_idx - 1],
                open_vals[entry_idx],
                close_vals[entry_idx],
                adjustment_gap_pct,
            )
        ):
            skipped_adjustment_gap += 1
            next_allowed_idx = entry_idx + 1
            continue

        atr = atr_vals[entry_idx] if atr_vals is not None and not np.isnan(atr_vals[entry_idx]) else entry_price * 0.03
        stop_ratio = stop_loss_pct / 100.0
        max_close_since_entry = entry_price
        exit_price = entry_price
        exit_reason = "超时平仓"
        hold_days = 0
        hit_stop = False
        time_stopped = False
        invalid_adjustment_gap = False

        for day in range(1, max_hold_days + 1):
            future_idx = entry_idx + day
            if future_idx >= len(df):
                hold_days = max(day - 1, 0)
                exit_price = close_vals[min(entry_idx + hold_days, len(df) - 1)]
                exit_reason = "数据结束"
                break

            if (
                skip_adjustment_gaps
                and _is_suspected_adjustment_gap(
                    close_vals[future_idx - 1],
                    open_vals[future_idx],
                    close_vals[future_idx],
                    adjustment_gap_pct,
                )
            ):
                skipped_adjustment_gap += 1
                invalid_adjustment_gap = True
                next_allowed_idx = future_idx + 1
                break

            day_close = close_vals[future_idx]
            day_low = low_vals[future_idx]
            hold_days = day

            # 改动 #16：缺口穿越止损建模。若当日开盘已击穿止损线（跳空缺口），
            # 按当日开盘成交（更差），而非止损线——更真实地反映滑点，不再高估收益。
            if (day_low - entry_price) / entry_price <= stop_ratio:
                stop_line = entry_price * (1 + stop_ratio)
                day_open = open_vals[future_idx]
                exit_price = min(stop_line, day_open) if day_open <= stop_line else stop_line
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

        if invalid_adjustment_gap:
            continue

        exit_idx = min(entry_idx + hold_days, len(df) - 1)
        entry_exec_price = entry_price * (1 + slippage_bps / 10000)
        exit_exec_price = exit_price * (1 - slippage_bps / 10000)
        cash_to_use = equity * position_pct
        shares = int(cash_to_use / entry_exec_price / lot_size) * lot_size
        if shares <= 0:
            skipped_capital += 1
            next_allowed_idx = exit_idx + 1
            continue

        buy_cost = entry_exec_price * shares
        sell_cost = exit_exec_price * shares
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
            "signal_date": dates[idx],
            "entry_date": dates[entry_idx],
            "exit_date": dates[exit_idx],
            "entry_price": round(float(entry_exec_price), 2),
            "exit_price": round(float(exit_exec_price), 2),
            "raw_entry_price": round(float(entry_price), 2),
            "raw_exit_price": round(float(exit_price), 2),
            "shares": int(shares),
            "return_pct": round(float(return_pct), 2),
            "hold_days": hold_days,
            "exit_reason": exit_reason,
            "signal_reason": _get_signal_reason(df, idx, strategy_type),
            "net_pnl": round(float(net_pnl), 2),
            "hit_stop": hit_stop,
            "time_stopped": time_stopped,
            "entry_mode": entry_mode,
            "open_gap_pct": round(float(open_gap_pct), 2) if open_gap_pct is not None else None,
            "slippage_bps": slippage_bps,
            "position_pct": position_pct,
        })
        equity_curve.append({"date": dates[exit_idx], "equity": round(float(equity), 2)})
        next_allowed_idx = exit_idx + 1

    if not trades:
        # 改动 #15：即使无交易也计算 benchmark（市场收益与策略成交与否无关）
        _br, _al, _cg = _compute_benchmark_fields(bench_df, 0.0, len(df))
        result = _empty_result("信号因流动性、复权断点或仓位约束未成交")
        result["summary"].update({
            "skipped_high_open": skipped_high_open,
            "skipped_limit_up": skipped_limit_up,
            "skipped_adjustment_gap": skipped_adjustment_gap,
            "skipped_capital": skipped_capital,
            "entry_mode": entry_mode,
            "slippage_bps": slippage_bps,
            "position_pct": position_pct,
            "lot_size": lot_size,
            "benchmark_return": _br,
            "alpha": _al,
            "cagr": _cg,
        })
        return result

    returns = [trade["return_pct"] for trade in trades]
    # 改动 #13：profit_factor / win_rate 改调规范 helper（毛额口径，cap 99）
    from core.analytics import compute_profit_factor, compute_win_rate
    profit_factor = compute_profit_factor(returns, cap=99.0)
    win_rate = compute_win_rate(returns, ndigits=1)

    # 改动 #15：基准 alpha / CAGR（沪深300）。无 bench_df 时为 None，前端兜底为 "-"。
    strategy_total_return = (equity - initial_capital) / initial_capital * 100
    benchmark_return, alpha, cagr = _compute_benchmark_fields(
        bench_df, strategy_total_return, len(df)
    )

    return {
        "summary": {
            "signal_count": len(trades),
            "win_rate": win_rate,
            "avg_return": round(float(np.mean(returns)), 2),
            "max_drawdown": round(float(max_drawdown), 2),
            "profit_factor": profit_factor,
            "avg_hold_days": round(float(np.mean([trade["hold_days"] for trade in trades])), 1),
            "total_return": round((equity - initial_capital) / initial_capital * 100, 2),
            "final_equity": round(float(equity), 2),
            "stop_loss_hits": sum(1 for trade in trades if trade["hit_stop"]),
            "time_stopped": sum(1 for trade in trades if trade["time_stopped"]),
            "skipped_high_open": skipped_high_open,
            "skipped_limit_up": skipped_limit_up,
            "skipped_adjustment_gap": skipped_adjustment_gap,
            "skipped_capital": skipped_capital,
            "entry_mode": entry_mode,
            "slippage_bps": slippage_bps,
            "position_pct": position_pct,
            "lot_size": lot_size,
            "benchmark_return": benchmark_return,
            "alpha": alpha,
            "cagr": cagr,
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
