import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from core.risk_constants import (
    FIXED_STOP_LOSS_PCT, TIER_HIGH_PROFIT_PCT,
    VOLUME_CLIMAX_MULTIPLIER, BACKTEST_MAX_HOLD_DAYS,
    BACKTEST_TRAILING_ATR_MULT,
    ATR_STOP_MULTIPLIER, ATR_STOP_MIN_PCT, ATR_STOP_MAX_PCT
)
from core.risk_engine import compute_paper_risk_levels, compute_paper_risk_levels_with_context

STRATEGY_LOGIC_VERSION = "2026.05-brooks-pine5"
BACKTEST_ENGINE_VERSION = "v7.0-friction-trailing-time-stop"
EXIT_RULE_VERSION = "fixed-stop-atr-trailing-time-stop"


def _empty_backtest_result() -> Dict[str, Any]:
    return {
        "win_rate": 0,
        "adjusted_win_rate": 0,
        "adjusted_win_rate_method": "wilson_lower_99",
        "confidence": 0,
        "expectancy": 0,
        "sample_warning": "无历史信号",
        "signal_count": 0,
        "avg_hold_days": 0,
        "avg_return": 0,
        "max_drawdown": 0,
        "profit_factor": 0,
        "stop_loss_hits": 0,
        "vol_skipped": 0,
        "time_stopped": 0,
    }


def _sample_confidence(signal_count: int) -> float:
    if signal_count >= 20:
        return 1.0
    if signal_count >= 10:
        return 0.7
    if signal_count >= 5:
        return 0.4
    if signal_count >= 1:
        return 0.15
    return 0.0


def _wilson_lower_win_rate(wins: int, total: int, z: float = 2.58) -> float:
    """Conservative Wilson lower bound for win probability, returned as percent."""
    if total <= 0:
        return 0.0
    p_hat = wins / total
    z2 = z * z
    denominator = 1 + z2 / total
    centre = p_hat + z2 / (2 * total)
    margin = z * np.sqrt((p_hat * (1 - p_hat) + z2 / (4 * total)) / total)
    return round(max(0.0, (centre - margin) / denominator) * 100, 1)


def get_signal_details(
    df: pd.DataFrame,
    strategy_type: str = "squeeze",
    stop_loss_pct: float = -8.0,
    take_profit_pct: float = 5.0,
    max_hold_days: int = 5,
    threshold: float = 0.12,
    vol_multiplier: float = 1.5,
    rsi_min: int = 55,
    min_signals: int = 3,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    逐日遍历历史数据，记录每次策略信号的买卖点明细。

    Returns:
        {"buy_signals": [...], "sell_signals": [...]}
        每个 signal 包含 time, price, reason
    """
    buy_signals: List[Dict[str, Any]] = []
    sell_signals: List[Dict[str, Any]] = []
    if df.empty or len(df) < 120:
        return {"buy_signals": buy_signals, "sell_signals": sell_signals}

    if strategy_type == "tv_zp":
        long_indices, short_indices, _debug = _find_tv_zp_signal_indices(df)
        for idx in long_indices:
            buy_signals.append({
                "time": str(df['日期'].iloc[idx])[:10],
                "price": round(float(df['收盘'].iloc[idx]), 2),
                "reason": _get_signal_reason(df, idx, strategy_type),
            })
        for idx in short_indices:
            sell_signals.append({
                "time": str(df['日期'].iloc[idx])[:10],
                "price": round(float(df['收盘'].iloc[idx]), 2),
                "reason": "TV-ZP short：Range Filter转空 + Volume/QQE确认",
            })
        return {
            "buy_signals": buy_signals,
            "sell_signals": sell_signals,
            "trailing_stops": [],
        }

    if strategy_type == "squeeze":
        signal_indices = _find_squeeze_signal_indices(df, threshold, vol_multiplier, rsi_min)
        for seq, idx in enumerate(signal_indices):
            entry_price = float(df['收盘'].iloc[idx])
            if entry_price <= 0:
                continue
            buy_signals.append({
                "time": str(df['日期'].iloc[idx])[:10],
                "price": round(entry_price, 2),
                "reason": _get_signal_reason(df, idx, strategy_type),
            })

            next_idx = signal_indices[seq + 1] if seq + 1 < len(signal_indices) else len(df)
            hit_profit = False
            hit_breakdown = False
            target_price = entry_price * 1.15
            for future_idx in range(idx + 1, min(next_idx, len(df))):
                row = df.iloc[future_idx]
                if not hit_profit and float(row.get('最高', 0)) >= target_price:
                    sell_signals.append({
                        "time": str(row['日期'])[:10],
                        "price": round(target_price, 2),
                        "reason": "止盈50%",
                        "pnl_pct": 15.0,
                        "hold_days": future_idx - idx,
                    })
                    hit_profit = True
                if not hit_breakdown and float(row.get('收盘', 0)) < float(row.get('EMA20', 0)):
                    exit_price = float(row.get('收盘', 0))
                    sell_signals.append({
                        "time": str(row['日期'])[:10],
                        "price": round(exit_price, 2),
                        "reason": "破位",
                        "pnl_pct": round((exit_price - entry_price) / entry_price * 100, 2),
                        "hold_days": future_idx - idx,
                    })
                    hit_breakdown = True
                if hit_profit and hit_breakdown:
                    break

        return {
            "buy_signals": buy_signals,
            "sell_signals": sell_signals,
            "trailing_stops": [],
        }

    close_vals = df['收盘'].values
    high_vals = df['最高'].values
    low_vals = df['最低'].values
    max_idx = len(close_vals) - 1
    stop_loss_ratio = stop_loss_pct / 100.0
    take_profit_ratio = take_profit_pct / 100.0

    # --- 找到所有买入信号日期 ---
    signal_indices = _find_all_signal_indices(df, strategy_type, threshold, vol_multiplier, rsi_min, min_signals)
    trailing_stops = []

    for idx in signal_indices:
        entry_price = close_vals[idx]
        if entry_price <= 0:
            continue

        date_str = str(df['日期'].iloc[idx])[:10]
        buy_signals.append({
            "time": date_str,
            "price": round(float(entry_price), 2),
            "reason": _get_signal_reason(df, idx, strategy_type),
        })

        # 模拟卖出 (同时收集移动止盈线轨迹)
        exit_price = entry_price
        exit_reason = "超时平仓"
        hold_days = max_hold_days
        hit_stop = False
        max_close_reached = entry_price
        atr = df['ATR'].iloc[idx] if 'ATR' in df.columns else entry_price * 0.03
        trailing_multiplier = 2.2 # 这里的倍数与后台设定的对齐

        for day in range(1, max_hold_days + 1):
            future_idx = idx + day
            if future_idx > max_idx:
                hold_days = day - 1
                if hold_days > 0:
                    exit_price = close_vals[min(idx + hold_days, max_idx)]
                break

            day_close = close_vals[future_idx]
            day_low = low_vals[future_idx]
            day_high = high_vals[future_idx]

            # 更新最高收盘价和止盈线 (同步收集轨迹数据)
            max_close_reached = max(max_close_reached, day_close)
            stop_level = max_close_reached - atr * trailing_multiplier
            trailing_stops.append({
                "time": str(df['日期'].iloc[future_idx])[:10],
                "value": round(float(stop_level), 2)
            })

            # 1. 止损检查
            day_low_return = (day_low - entry_price) / entry_price
            if day_low_return <= stop_loss_ratio:
                exit_price = entry_price * (1 + stop_loss_ratio)
                hold_days = day
                exit_reason = f"止损 {stop_loss_pct}%"
                hit_stop = True
                break
            
            # 2. 移动止盈检查
            if day_close < stop_level:
                exit_price = day_close
                hold_days = day
                exit_reason = "移动止盈"
                break

            if day == max_hold_days:
                exit_price = day_close
                hold_days = day

        # 3. 计入摩擦力 (用于显示真实的 PnL)
        pnl_pct_raw = (exit_price - entry_price) / entry_price * 100
        pnl_pct = round(pnl_pct_raw - 0.26, 2) # 固定扣除约 0.26% 的滑点与佣金
        exit_date = str(df['日期'].iloc[min(idx + hold_days, max_idx)])[:10]

        is_open = (idx + max_hold_days > max_idx) and (not hit_stop) and (exit_reason == "超时平仓")
        if not is_open:
            sell_signals.append({
                "time": exit_date,
                "price": round(float(exit_price), 2),
                "reason": exit_reason,
                "pnl_pct": pnl_pct,
                "hold_days": hold_days,
            })

    return {
        "buy_signals": buy_signals, 
        "sell_signals": sell_signals,
        "trailing_stops": trailing_stops
    }


def _find_all_signal_indices(
    df: pd.DataFrame, strategy_type: str,
    threshold: float, vol_multiplier: float, rsi_min: int, min_signals: int,
) -> List[int]:
    """找到历史中所有触发信号的索引（排除最后5天，留给回测）"""
    try:
        if strategy_type == "tv_zp":
            long_indices, _short_indices, _debug = _find_tv_zp_signal_indices(df)
            return long_indices
        if strategy_type == "pine":
            return _find_pine_signal_indices(df, min_signals)
        elif strategy_type == "consensus":
            return _find_consensus_signal_indices(df)
        else:
            return _find_squeeze_signal_indices(df, threshold, vol_multiplier, rsi_min)
    except Exception:
        return []


def _squeeze_tv_macd(df: pd.DataFrame) -> pd.DataFrame:
    close = df['收盘']
    dif = close.ewm(span=8, adjust=False).mean() - close.ewm(span=15, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2
    return pd.DataFrame({"dif": dif, "dea": dea, "hist": hist}, index=df.index)


def _squeeze_tv_rsi(df: pd.DataFrame) -> pd.Series:
    if 'RSI_WILDER' in df.columns:
        return df['RSI_WILDER']
    delta = df['收盘'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs.fillna(0)))
    rsi.loc[(avg_loss == 0) & (avg_gain > 0)] = 100
    return rsi


def _find_squeeze_signal_indices(
    df: pd.DataFrame,
    threshold: float,
    vol_multiplier: float,
    rsi_min: int,
    use_macd_filter: bool = True,
    use_bb_sqz: bool = False,
    sqz_lookback: int = 10,
    use_rs_filter: bool = False,
) -> List[int]:
    """均线粘合策略的信号索引，对齐 TradingView MACD 优化版。"""
    ma_cols = ['EMA5', 'EMA20', 'EMA60']
    if not all(c in df.columns for c in ma_cols):
        return []

    ma_max = df[ma_cols].max(axis=1)
    c_breakout = (df['收盘'] >= ma_max) & (df['收盘'] > df['开盘'])
    c_volume = (df['成交量'] / df['Vol_MA20'].replace(0, np.nan)) >= vol_multiplier
    c_rsi = _squeeze_tv_rsi(df) >= rsi_min
    macd = _squeeze_tv_macd(df)
    c_macd = ((macd['hist'] > 0) & (macd['hist'].shift(1) <= 0)) if use_macd_filter else pd.Series(True, index=df.index)
    sqz_ratio = (df[ma_cols].max(axis=1) - df[ma_cols].min(axis=1)) / df[ma_cols].min(axis=1).replace(0, np.nan)
    c_sqz = sqz_ratio.rolling(sqz_lookback).min() < threshold
    c_bb = pd.Series(True, index=df.index)
    if use_bb_sqz and 'BB_Width' in df.columns:
        c_bb = df['BB_Width'] <= df['BB_Width'].rolling(120).quantile(0.2)
    c_rs = pd.Series(True, index=df.index)
    if use_rs_filter and 'RS' in df.columns and 'RS_MA50' in df.columns:
        c_rs = df['RS'] > df['RS_MA50']

    mask = c_breakout & c_volume & c_rsi & c_macd & c_sqz & c_bb & c_rs
    valid = df.index[mask & (df.index >= 120)]
    return valid.tolist()


def _pine_bullish_signal_series(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """返回 Pine 五指标看涨序列，成交量作为单独确认条件。"""
    false_series = pd.Series(False, index=df.index)
    rf_up = df.get('RF_Upward', false_series).fillna(False).astype(bool)
    rf_down = df.get('RF_Downward', false_series).fillna(False).astype(bool)
    return {
        "rf": rf_up & ~rf_down,
        "st": df.get('ST_Signal', false_series).fillna(False).astype(bool),
        "rqk": df.get('RQK_Up', false_series).fillna(False).astype(bool),
        "half": df.get('HalfTrend_Up', false_series).fillna(False).astype(bool),
        "qqe": df.get('QQE_Long', false_series).fillna(False).astype(bool),
    }


def _pine_volume_confirm_series(df: pd.DataFrame) -> pd.Series:
    vol_ma = df.get('Vol_MA20', df['成交量'].rolling(20).mean()).replace(0, np.nan)
    return (df['成交量'] > vol_ma * 1.2).fillna(False)


def _find_pine_signal_indices(df: pd.DataFrame, min_signals: int) -> List[int]:
    """Pine Script 共振策略的信号索引"""
    min_signals = max(1, min(int(min_signals), 5))
    signals = _pine_bullish_signal_series(df)
    bullish_count = sum(s.astype(int) for s in signals.values())
    vol_bullish = _pine_volume_confirm_series(df)
    is_bull_candle = df['收盘'] > df['开盘']
    body = (df['收盘'] - df['开盘']).abs()
    upper_shadow = df['最高'] - df[['收盘', '开盘']].max(axis=1)
    shadow_ratio = upper_shadow / body.replace(0, np.nan)
    is_shadow_ok = (shadow_ratio < 0.5).fillna(True)

    mask = (bullish_count >= min_signals) & vol_bullish & is_bull_candle & is_shadow_ok
    valid = df.index[mask & (df.index >= 120)]
    return valid.tolist()


def _tv_rma(series: pd.Series, length: int) -> pd.Series:
    return series.astype(float).ewm(alpha=1 / length, adjust=False).mean()


def _tv_rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0).fillna(0)
    loss = (-delta.clip(upper=0)).fillna(0)
    avg_gain = _tv_rma(gain, length)
    avg_loss = _tv_rma(loss, length).replace(0, np.nan)
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _tv_zp_range_filter_default(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """TradingView ZP 默认 Range Filter: per=100, mult=3."""
    close = df["收盘"].astype(float).reset_index(drop=True)
    abs_diff = close.diff().abs().fillna(0)
    smooth_range = (
        abs_diff.ewm(span=100, adjust=False).mean()
        .ewm(span=199, adjust=False).mean()
        * 3.0
    )

    filt = np.zeros(len(close), dtype=float)
    upward = np.zeros(len(close), dtype=int)
    downward = np.zeros(len(close), dtype=int)
    if len(close) == 0:
        empty = pd.Series(dtype=bool, index=df.index)
        return empty, empty, pd.Series(dtype=float, index=df.index)

    filt[0] = close.iloc[0]
    for i in range(1, len(close)):
        prev = filt[i - 1]
        price = close.iloc[i]
        rng = smooth_range.iloc[i] if np.isfinite(smooth_range.iloc[i]) else 0.0
        if price > prev:
            filt[i] = prev if price - rng < prev else price - rng
        else:
            filt[i] = prev if price + rng > prev else price + rng

        if filt[i] > filt[i - 1]:
            upward[i] = upward[i - 1] + 1
            downward[i] = 0
        elif filt[i] < filt[i - 1]:
            downward[i] = downward[i - 1] + 1
            upward[i] = 0
        else:
            upward[i] = upward[i - 1]
            downward[i] = downward[i - 1]

    rf_up = (close > filt) & (pd.Series(upward) > 0)
    rf_down = (close < filt) & (pd.Series(downward) > 0)
    return (
        pd.Series(rf_up.to_numpy(), index=df.index).fillna(False),
        pd.Series(rf_down.to_numpy(), index=df.index).fillna(False),
        pd.Series(filt, index=df.index),
    )


def _tv_zp_qqe_trailing_line(rsi_ma: pd.Series, factor: float, rsi_period: int = 6) -> pd.Series:
    wilders = rsi_period * 2 - 1
    atr_rsi = (rsi_ma.shift(1) - rsi_ma).abs().fillna(0)
    dar = (
        atr_rsi.ewm(span=wilders, adjust=False).mean()
        .ewm(span=wilders, adjust=False).mean()
        * factor
    )

    longband = np.zeros(len(rsi_ma), dtype=float)
    shortband = np.zeros(len(rsi_ma), dtype=float)
    trend = np.ones(len(rsi_ma), dtype=int)
    fast = np.zeros(len(rsi_ma), dtype=float)
    values = rsi_ma.fillna(50).to_numpy(dtype=float)
    ranges = dar.fillna(0).to_numpy(dtype=float)

    for i in range(len(values)):
        rs = values[i]
        new_long = rs - ranges[i]
        new_short = rs + ranges[i]
        if i == 0:
            longband[i] = new_long
            shortband[i] = new_short
            fast[i] = longband[i]
            continue

        prev_rs = values[i - 1]
        prev_long = longband[i - 1]
        prev_short = shortband[i - 1]
        longband[i] = max(prev_long, new_long) if prev_rs > prev_long and rs > prev_long else new_long
        shortband[i] = min(prev_short, new_short) if prev_rs < prev_short and rs < prev_short else new_short

        cross_above_short = prev_rs <= prev_short and rs > prev_short
        cross_below_long = prev_rs >= prev_long and rs < prev_long
        if cross_above_short:
            trend[i] = 1
        elif cross_below_long:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]

        fast[i] = longband[i] if trend[i] == 1 else shortband[i]

    return pd.Series(fast, index=rsi_ma.index)


def _tv_zp_qqe_line_bar_series(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """QQE Mod Line & Bar confirmation from the TradingView ZP settings."""
    close = df["收盘"].astype(float)
    rsi_ma = _tv_rsi(close, 6).ewm(span=5, adjust=False).mean()
    fast_atr_rsi_tl = _tv_zp_qqe_trailing_line(rsi_ma, factor=3.0)
    bb_source = fast_atr_rsi_tl - 50
    basis = bb_source.rolling(50, min_periods=50).mean()
    dev = bb_source.rolling(50, min_periods=50).std() * 0.35
    upper = basis + dev
    lower = basis - dev

    rsi_ma2 = _tv_rsi(close, 6).ewm(span=5, adjust=False).mean()
    fast_atr_rsi2_tl = _tv_zp_qqe_trailing_line(rsi_ma2, factor=1.61)
    qqe_line = fast_atr_rsi2_tl - 50

    greenbar1 = (rsi_ma2 - 50) > 3
    greenbar2 = (rsi_ma - 50) > upper
    redbar1 = (rsi_ma2 - 50) < -3
    redbar2 = (rsi_ma - 50) < lower

    qqe_long = ((rsi_ma2 - 50) > 0) & greenbar1 & greenbar2 & (qqe_line > 0)
    qqe_short = ((rsi_ma2 - 50) < 0) & redbar1 & redbar2 & (qqe_line < 0)
    return qqe_long.fillna(False), qqe_short.fillna(False)


def _count_consecutive_true(series: pd.Series) -> pd.Series:
    counts = []
    current = 0
    for value in series.fillna(False).astype(bool):
        current = current + 1 if value else 0
        counts.append(current)
    return pd.Series(counts, index=series.index)


def _apply_tv_zp_expiry_and_alternate(
    leading_long: pd.Series,
    leading_short: pd.Series,
    long_cond: pd.Series,
    short_cond: pd.Series,
    expiry: int = 3,
) -> tuple[List[int], List[int]]:
    long_count = _count_consecutive_true(leading_long)
    short_count = _count_consecutive_true(leading_short)
    long_with_expiry = long_cond.fillna(False).astype(bool) & (long_count <= expiry)
    short_with_expiry = short_cond.fillna(False).astype(bool) & (short_count <= expiry)

    long_indices: List[int] = []
    short_indices: List[int] = []
    state = 0
    for idx, (is_long, is_short) in enumerate(zip(long_with_expiry, short_with_expiry)):
        if is_long and state != 1:
            long_indices.append(int(long_with_expiry.index[idx]))
            state = 1
        elif is_short and state != -1:
            short_indices.append(int(short_with_expiry.index[idx]))
            state = -1

    return long_indices, short_indices


def _find_tv_zp_signal_indices(df: pd.DataFrame) -> tuple[List[int], List[int], Dict[str, pd.Series]]:
    """
    TradingView DIY Custom Strategy Builder [ZP] 当前确认配置：
    Leading=Range Filter(Default 100/3), Confirmation=Volume above MA + QQE Line&Bar,
    Signal Expiry=3, Alternate Signal=true.
    """
    if df is None or df.empty or not {"收盘", "成交量"}.issubset(df.columns):
        return [], [], {}

    leading_long, leading_short, rf_filter = _tv_zp_range_filter_default(df)
    vol_ma = df.get("Vol_MA20", df["成交量"].rolling(20).mean()).replace(0, np.nan)
    volume_confirm = (df["成交量"] > vol_ma).fillna(False)
    qqe_long, qqe_short = _tv_zp_qqe_line_bar_series(df)

    long_cond = leading_long & volume_confirm & qqe_long
    short_cond = leading_short & volume_confirm & qqe_short
    long_indices, short_indices = _apply_tv_zp_expiry_and_alternate(
        leading_long=leading_long,
        leading_short=leading_short,
        long_cond=long_cond,
        short_cond=short_cond,
        expiry=3,
    )

    long_indices = [idx for idx in long_indices if idx >= 120]
    short_indices = [idx for idx in short_indices if idx >= 120]
    return long_indices, short_indices, {
        "rf_filter": rf_filter,
        "leading_long": leading_long,
        "leading_short": leading_short,
        "volume_confirm": volume_confirm,
        "qqe_long": qqe_long,
        "qqe_short": qqe_short,
    }


def _find_consensus_signal_indices(df: pd.DataFrame) -> List[int]:
    """Azul 共识策略的信号索引"""
    is_trend_up = df.get('MA20', 0) > df.get('MA60', 0)
    if isinstance(is_trend_up, (bool, int)):
        return []
    is_trend_up = is_trend_up.astype(int)

    high_20 = df['最高'].rolling(20).max().shift(1)
    is_hh = (df['收盘'] >= high_20).astype(int)
    is_big_bull = ((df['收盘'] > df['开盘'] * 1.025) & (df['收盘'] > df['开盘'])).astype(int)
    vol_ratio = df['成交量'] / df['Vol_MA20'].replace(0, np.nan)
    is_vol = (vol_ratio >= 1.8).astype(int)
    is_quality = (df.get('Trend_Quality', 0) >= 2).astype(int)

    body = (df['收盘'] - df['开盘']).abs()
    upper_shadow = df['最高'] - df[['收盘', '开盘']].max(axis=1)
    is_shadow = np.where(body > 0, (upper_shadow / body < 0.4).astype(int), 1).astype(int)

    mask = is_trend_up & is_hh & is_big_bull & is_vol & is_quality & is_shadow
    valid = df.index[(mask == 1) & (df.index >= 80)]
    return valid.tolist()


def _get_signal_reason(df: pd.DataFrame, idx: int, strategy_type: str) -> str:
    """生成信号触发原因描述"""
    row = df.iloc[idx]
    if strategy_type == "tv_zp":
        return "TV-ZP long：Range Filter转多 + Volume/QQE确认"
    if strategy_type == "pine":
        parts = []
        if row.get('RF_Upward', False): parts.append("RF看涨")
        if row.get('ST_Signal', False): parts.append("ST看涨")
        if row.get('RQK_Up', False): parts.append("RQK看涨")
        if row.get('HalfTrend_Up', False): parts.append("HalfTrend看涨")
        if row.get('QQE_Long', False): parts.append("QQE看涨")
        vol_ma = row.get('Vol_MA20', 0)
        if vol_ma > 0 and row['成交量'] > vol_ma * 1.2: parts.append("放量")
        return "+".join(parts) if parts else "Pine共振"
    elif strategy_type == "consensus":
        return "HH突破+大阳线+放量"
    else:
        parts = ["均线粘合突破"]
        vol_ma = row.get('Vol_MA20', 0)
        if vol_ma > 0:
            vr = row['成交量'] / vol_ma
            parts.append(f"量比{vr:.1f}")
        parts.append(f"RSI={row.get('RSI_WILDER', row.get('RSI', 0)):.0f}")
        parts.append("MACD共振")
        return "+".join(parts)


def run_optimization_grid(
    df: pd.DataFrame,
    strategy_type: str = "squeeze",
    param_x: str = "rsi_min",
    param_x_values: Optional[List] = None,
    param_y: str = "stop_loss_pct",
    param_y_values: Optional[List] = None,
) -> Dict[str, Any]:
    """
    参数寻优网格：对两个参数做笛卡尔积回测，返回胜率矩阵。

    Returns:
        {"x_labels": [...], "y_labels": [...], "values": [[win_rate, ...], ...], "metric": "win_rate"}
    """
    if param_x_values is None:
        param_x_values = [50, 55, 60, 65]
    if param_y_values is None:
        param_y_values = [-5, -8, -10, -12]

    x_labels = [str(v) for v in param_x_values]
    y_labels = [str(v) for v in param_y_values]

    values = []
    for y_val in param_y_values:
        row_results = []
        for x_val in param_x_values:
            signal_indices = _find_signal_indices_with_params(df, strategy_type, param_x, x_val)
            if len(signal_indices) == 0:
                row_results.append(0)
                continue
            bt = _simulate_backtest(
                close_vals=df['收盘'].values,
                high_vals=df['最高'].values,
                low_vals=df['最低'].values,
                signal_indices=signal_indices,
                stop_loss_pct=float(y_val),
                atr_vals=df['ATR'].values if 'ATR' in df.columns else None
            )
            row_results.append(bt["win_rate"])
        values.append(row_results)

    # 找到最佳参数组合
    best_wr = 0
    best_x, best_y = param_x_values[0], param_y_values[0]
    for i, y_val in enumerate(param_y_values):
        for j, x_val in enumerate(param_x_values):
            if values[i][j] > best_wr:
                best_wr = values[i][j]
                best_x, best_y = x_val, y_val

    return {
        "x_labels": x_labels,
        "y_labels": y_labels,
        "values": values,
        "metric": "win_rate",
        "best": {"param_x": best_x, "param_y": best_y, "win_rate": best_wr},
    }


def _find_signal_indices_with_params(df: pd.DataFrame, strategy_type: str, param_name: str, param_value: Any) -> List[int]:
    """根据指定参数值找信号索引"""
    try:
        if strategy_type == "tv_zp":
            long_indices, _short_indices, _debug = _find_tv_zp_signal_indices(df)
            return long_indices
        if strategy_type == "pine":
            min_signals = int(param_value) if param_name in {"min_signals", "pine_min_signals"} else 3
            return _find_pine_signal_indices(df, min_signals)
        elif strategy_type == "consensus":
            return _find_consensus_signal_indices(df)
        else:
            # squeeze 策略
            threshold = 0.12
            vol_multiplier = 1.5
            rsi_min = 55
            if param_name == "rsi_min":
                rsi_min = int(param_value)
            elif param_name == "vol_multiplier":
                vol_multiplier = float(param_value)
            elif param_name == "threshold":
                threshold = float(param_value)
            return _find_squeeze_signal_indices(df, threshold, vol_multiplier, rsi_min)
    except Exception:
        return []


def _simulate_backtest(
    close_vals, high_vals, low_vals, signal_indices, 
    stop_loss_pct=-8.0, max_hold_days=BACKTEST_MAX_HOLD_DAYS,
    atr_vals=None, vol_vals=None,
    use_trailing_stop=True, trailing_multiplier=BACKTEST_TRAILING_ATR_MULT, capital=100000,
    vol_cap_pct=0.05, time_stop_days=None,
) -> Dict[str, Any]:
    """
    通用回测模拟引擎 (v7.0 - 真实摩擦模型)
    
    对每个信号点模拟买入，按以下规则退出：
    1. 止损: 持仓期间某日最低价跌破入场价 stop_loss_pct%
    2. 移动止盈: 收盘价跌破 (持仓期最高收盘 - N*ATR)
    3. 时间止损: 持有 time_stop_days 天且未盈利 → 强制平仓
    4. 超时: 持有 max_hold_days 天后按收盘价结算
    
    Args:
        close_vals: 收盘价 numpy array
        high_vals: 最高价 numpy array  
        low_vals: 最低价 numpy array
        signal_indices: 信号点索引列表
        stop_loss_pct: 止损百分比 (负数, 如 -8.0)
        take_profit_pct: 止盈百分比 (正数, 如 5.0)
        max_hold_days: 最大持有天数
        atr_vals: ATR 序列 (用于移动止损和头寸计算)
        vol_vals: 成交量序列 (用于流动性约束)
        use_trailing_stop: 是否启用移动止盈
        trailing_multiplier: 移动止盈 ATR 倍数
        capital: 初始模拟资金 (用于头寸计算)
        vol_cap_pct: 成交额占比上限 (默认 5%)
        time_stop_days: 时间止损天数 (None=不启用; 如设为 5 则持仓 5 天未盈利自动平仓)
        
    Returns:
        回测统计字典
    """
    if len(signal_indices) == 0:
        return _empty_backtest_result()
    
    # --- A 股真实费率常量 ---
    STAMP_TAX_RATE = 0.0005    # 印花税 0.05% (仅卖出, 2023年减半)
    COMMISSION_RATE = 0.00025  # 券商佣金 万2.5 (买卖双向)
    COMMISSION_MIN = 5.0       # 最低佣金 5 元
    
    wins = 0
    losses = 0
    total_profit = 0.0
    total_loss = 0.0
    stop_loss_hits = 0
    vol_skipped = 0
    time_stopped = 0
    max_drawdown = 0.0
    returns = []
    hold_days_list = []
    
    max_idx = len(close_vals) - 1
    stop_loss_ratio = stop_loss_pct / 100.0   # e.g. -0.08
    
    for idx in signal_indices:
        entry_price = close_vals[idx]
        if entry_price <= 0:
            continue
            
        # --- 头寸计算 (2% 风险模型) ---
        atr = atr_vals[idx] if atr_vals is not None and not np.isnan(atr_vals[idx]) else entry_price * 0.03
        risk_per_share = max(atr * 2, entry_price * 0.05) 
        ideal_shares = int((capital * 0.02) / risk_per_share) if risk_per_share > 0 else 0
        
        # --- 成交量约束 (Volume Constraint) ---
        if vol_vals is not None and idx < len(vol_vals):
            day_vol = vol_vals[idx]
            if day_vol > 0:
                day_turnover = day_vol * entry_price  # 近似日成交额
                max_invest = day_turnover * vol_cap_pct
                max_shares = int(max_invest / entry_price)
                actual_shares = min(ideal_shares, max_shares)
                # 不满一手 (100 股) 则跳过
                if actual_shares < 100:
                    vol_skipped += 1
                    continue
                ideal_shares = actual_shares
        
        # 确保至少一手
        shares = max(ideal_shares, 100)
        
        exit_return = 0.0
        hold_days = max_hold_days
        hit_stop = False
        max_close_since_entry = entry_price
        exit_price = entry_price
        
        for day in range(1, max_hold_days + 1):
            future_idx = idx + day
            if future_idx > max_idx:
                hold_days = day - 1
                if hold_days > 0:
                    exit_price = close_vals[min(idx + hold_days, max_idx)]
                    exit_return = (exit_price - entry_price) / entry_price
                break
            
            day_close = close_vals[future_idx]
            day_high = high_vals[future_idx]
            day_low = low_vals[future_idx]
            
            # 1. 固定止损检查 (盘中触发)
            day_low_return = (day_low - entry_price) / entry_price
            if day_low_return <= stop_loss_ratio:
                exit_price = entry_price * (1 + stop_loss_ratio)
                exit_return = stop_loss_ratio
                hold_days = day
                hit_stop = True
                break
            
            # 2. 移动止盈逻辑
            if use_trailing_stop:
                max_close_since_entry = max(max_close_since_entry, day_close)
                current_stop_level = max_close_since_entry - (atr * trailing_multiplier)
                if day_close < current_stop_level:
                    exit_price = day_close
                    exit_return = (day_close - entry_price) / entry_price
                    hold_days = day
                    break
            
            # 3. 时间止损: 持仓 N 天未盈利 → 强制平仓
            if time_stop_days and day >= time_stop_days:
                current_return = (day_close - entry_price) / entry_price
                if current_return <= 0:
                    exit_price = day_close
                    exit_return = current_return
                    hold_days = day
                    time_stopped += 1
                    break

            # 最后一天按收盘价结算
            if day == max_hold_days:
                exit_price = day_close
                exit_return = (day_close - entry_price) / entry_price
                hold_days = day
        
        # --- A 股动态税费模型 ---
        buy_cost = entry_price * shares
        sell_cost = exit_price * shares
        buy_commission = max(buy_cost * COMMISSION_RATE, COMMISSION_MIN)
        sell_commission = max(sell_cost * COMMISSION_RATE, COMMISSION_MIN)
        stamp_tax = sell_cost * STAMP_TAX_RATE
        total_friction = (buy_commission + sell_commission + stamp_tax) / buy_cost if buy_cost > 0 else 0
        exit_return -= total_friction
        
        # 统计
        returns.append(exit_return * 100)  # 转为百分比
        hold_days_list.append(hold_days)
        
        if hit_stop:
            stop_loss_hits += 1
        
        if exit_return > 0:
            wins += 1
            total_profit += exit_return
        elif exit_return < 0:
            losses += 1
            total_loss += abs(exit_return)
        
        # 最大回撤
        if exit_return < max_drawdown:
            max_drawdown = exit_return
    
    total_trades = len(returns)
    win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0
    avg_return = round(sum(returns) / total_trades, 2) if total_trades > 0 else 0
    avg_hold = round(sum(hold_days_list) / total_trades, 1) if total_trades > 0 else 0
    profit_factor = round(total_profit / total_loss, 2) if total_loss > 0 else (999.0 if total_profit > 0 else 0)

    # ── 胜率计算改进 ──
    # 1. 样本可信度：保留给前端解释风险，不直接作为胜率乘数
    confidence = _sample_confidence(total_trades)
    # 2. 有效胜率：使用 99% Wilson 下界，避免小样本高胜率误导评分
    adjusted_win_rate = _wilson_lower_win_rate(wins, total_trades)

    # 3. 期望收益（Expectancy）：每笔交易的统计期望
    avg_win = total_profit / wins if wins > 0 else 0
    avg_loss = total_loss / losses if losses > 0 else 0
    win_prob = wins / total_trades if total_trades > 0 else 0
    loss_prob = losses / total_trades if total_trades > 0 else 0
    expectancy = round((win_prob * avg_win - loss_prob * avg_loss) * 100, 2)

    # 4. 样本量警告
    if total_trades == 0:
        sample_warning = "无历史信号"
    elif total_trades < 5:
        sample_warning = f"仅{total_trades}笔交易，胜率仅供参考"
    elif total_trades < 10:
        sample_warning = f"样本偏少({total_trades}笔)，胜率可信度一般"
    else:
        sample_warning = ""

    # 5. profit_factor 小样本封顶（避免1笔盈利=999误导）
    if total_trades < 5 and profit_factor > 3.0:
        profit_factor = 3.0
    
    return {
        "win_rate": win_rate,
        "adjusted_win_rate": adjusted_win_rate,
        "adjusted_win_rate_method": "wilson_lower_99",
        "confidence": confidence,
        "expectancy": expectancy,
        "sample_warning": sample_warning,
        "signal_count": total_trades,
        "avg_hold_days": avg_hold,
        "avg_return": avg_return,
        "max_drawdown": round(max_drawdown * 100, 2),
        "profit_factor": min(profit_factor, 99.0),
        "stop_loss_hits": stop_loss_hits,
        "vol_skipped": vol_skipped,
        "time_stopped": time_stopped
    }

def _calculate_fundamental_score(fund_data: Optional[Dict[str, Any]]) -> tuple[float, dict]:
    """计算基本面加权分数并返回 UI 展示所需的数据"""
    fund_score = 0.0
    roe = 0.0
    net_profit_yoy = 0.0
    
    if fund_data:
        roe = float(fund_data.get('roe', 0))
        net_profit_yoy = float(fund_data.get('net_profit_yoy', 0))
        
        # 规则 1: 稳定高 ROE (戴维斯双击潜质)
        if roe >= 15:
            fund_score += 15
        elif roe >= 8:
            fund_score += 5
            
        # 规则 2: 净利润高增 (成长极速/断层潜质)
        if net_profit_yoy >= 30:
            fund_score += 20
        elif net_profit_yoy >= 15:
            fund_score += 10
            
    # 只有当 fund_data 确实存在时，才返回具体数值（允许 0.0 和负数显示）
    # 如果 fund_data 为 None，则返回 None 供前端渲染 '---'
    return fund_score, {
        "ROE": round(roe, 2) if fund_data is not None else None,
        "净利YOY": round(net_profit_yoy, 2) if fund_data is not None else None
    }


def check_strategy(df, threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True, use_bb_sqz=False, sqz_lookback=10, use_rs_filter=False, fund_data: dict = None):
    """执行无门问禅：A股均线粘合战法 (Optimized)"""
    if len(df) < 120: return False, {"reason": f"历史数据不足 ({len(df)}天)"}

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 1. 均线系统：对齐 TradingView 图例 EMA5/EMA20/EMA60 ---
    ma_cols = ['EMA5', 'EMA20', 'EMA60']
    ma_max_series = df[ma_cols].max(axis=1)
    ma_min_series = df[ma_cols].min(axis=1).replace(0, np.nan)
    sqz_ratio = (ma_max_series - ma_min_series) / ma_min_series
    
    # 粘合判断 (最近 N 天内出现过粘合)
    was_squeeze_recent = sqz_ratio.iloc[-sqz_lookback:].min() < threshold
    
    # --- 2. 突破动作 ---
    curr_ma_max = ma_max_series.iloc[-1]
    is_breakout = (curr['收盘'] >= curr_ma_max) and (curr['收盘'] > curr['开盘'])
    
    # --- 3. 趋势与量能 ---
    vol_ratio = curr['成交量'] / curr['Vol_MA20'] if curr['Vol_MA20'] > 0 else 0
    is_volume = (vol_ratio >= vol_multiplier)
    
    # --- 5. RSI 强度 ---
    tv_rsi = _squeeze_tv_rsi(df)
    curr_rsi = float(tv_rsi.iloc[-1])
    is_rsi_ok = curr_rsi >= rsi_min
    
    # --- 6. MACD ---
    # 修复 L2: 原逻辑要求精确同日金叉(hist[-1]>0 AND hist[-2]<=0)，拒绝了大量有效突破
    # (MACD 1-2日前金叉、当前已确认多头)。改为"近3日内曾金叉且当前hist>0"。
    tv_macd = _squeeze_tv_macd(df)
    if use_macd_filter:
        hist_now = tv_macd['hist'].iloc[-1]
        # 近3日内是否有金叉(hist从<=0翻正)
        recent_cross = any(
            tv_macd['hist'].iloc[i] > 0 and tv_macd['hist'].iloc[i - 1] <= 0
            for i in range(max(1, len(tv_macd) - 3), len(tv_macd))
        )
        is_macd_ok = hist_now > 0 and recent_cross
    else:
        is_macd_ok = True
    
    # --- 7. 相对强度 ---
    is_rs_ok = True
    if use_rs_filter and 'RS' in df.columns and 'RS_MA50' in df.columns:
        is_rs_ok = curr['RS'] > curr['RS_MA50']
    
    # --- 8. 波动率收缩 ---
    is_bb_ok = True
    if use_bb_sqz:
        bb_quantile_20 = df['BB_Width'].iloc[-120:].quantile(0.2)
        is_bb_ok = curr['BB_Width'] <= bb_quantile_20

    debug_info = {
        "squeeze": round(sqz_ratio.iloc[-1], 4),
        "vol_ratio": round(vol_ratio, 2),
        "rsi": round(curr_rsi, 1),
        "is_breakout": is_breakout,
        "is_volume": is_volume,
        "is_rsi_ok": is_rsi_ok,
        "is_macd_ok": is_macd_ok,
        "is_bb_ok": is_bb_ok,
        "was_sqz_recent": was_squeeze_recent,
        "is_rs_ok": is_rs_ok
    }

    if not was_squeeze_recent:
        debug_info["reason"] = "近期未现均线粘合"
        return False, debug_info

    if is_breakout and is_volume and is_rsi_ok and is_macd_ok and is_bb_ok and is_rs_ok:
        pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
        body = abs(curr['收盘'] - curr['开盘'])
        upper_shadow = curr['最高'] - max(curr['收盘'], curr['开盘'])
        shadow_ratio = round(upper_shadow / body, 2) if body > 0 else 0
        
        # 技术评分：使用近期最深粘合度，避免突破当天均线发散后被反向扣分；量比封顶，避免极端成交量吞没其他维度。
        recent_sqz_min = float(sqz_ratio.iloc[-sqz_lookback:].min())
        sqz_depth = max(0.0, threshold - recent_sqz_min)
        vol_score = min(vol_ratio, 3.0) * 25
        sqz_score = sqz_depth * 100 * 50
        rsi_score = min(curr_rsi, 80.0) * 0.4
        tech_score = vol_score + sqz_score + rsi_score
        
        # 基本面加权
        fund_score, fund_ui_data = _calculate_fundamental_score(fund_data)
        total_score = tech_score + fund_score
        
        res = {
            "Score": round(total_score, 2),
            "涨幅%": round(pct_change, 2),
            "现价": curr['收盘'],
            "代码": curr.get('code', 'N/A'),
            "名称": curr.get('name', 'N/A'),
            "粘合度": round(sqz_ratio.iloc[-1], 4),
            "RSI": round(curr_rsi, 1),
            "DIF": round(tv_macd['dif'].iloc[-1], 3),
            "BB": round(curr['BB_Width'], 4),
            "影线比": shadow_ratio
        }
        res.update(fund_ui_data)
        return True, res
    
    reasons = []
    if not is_breakout: reasons.append("未突破均线簇")
    if not is_volume: reasons.append("量能未爆发")
    if not is_rsi_ok: reasons.append("强度不足(RSI)")
    if not is_macd_ok: reasons.append("MACD未金叉")
    if not is_bb_ok: reasons.append("布林带未收缩")
    if not is_rs_ok: reasons.append("弱于大盘(RS)")
    
    debug_info["reason"] = ",".join(reasons) if reasons else "多因子未共振"
    return False, debug_info

def calculate_historical_win_rate(
    df,
    stop_loss_pct=-8.0,
    threshold=0.12,
    vol_multiplier=1.5,
    rsi_min=55,
    use_macd_filter=True,
    use_bb_sqz=False,
    sqz_lookback=10,
    use_rs_filter=False,
):
    """向量化计算回测统计 (Enhanced v6.0 - 含止损/回撤/盈亏比)"""
    empty_result = _empty_backtest_result()
    if df.empty or len(df) < 130:
        return empty_result

    try:
        final_signal_indices = [
            idx for idx in _find_squeeze_signal_indices(
                df,
                threshold=threshold,
                vol_multiplier=vol_multiplier,
                rsi_min=rsi_min,
                use_macd_filter=use_macd_filter,
                use_bb_sqz=use_bb_sqz,
                sqz_lookback=sqz_lookback,
                use_rs_filter=use_rs_filter,
            )
            if idx < len(df) - 5
        ]

        if len(final_signal_indices) == 0:
            return empty_result

        return _simulate_backtest(
            close_vals=df['收盘'].values,
            high_vals=df['最高'].values,
            low_vals=df['最低'].values,
            signal_indices=final_signal_indices,
            stop_loss_pct=stop_loss_pct,
            atr_vals=df['ATR'].values if 'ATR' in df.columns else None,
            vol_vals=df['成交量'].values if '成交量' in df.columns else None
        )

    except Exception as e:
        return empty_result


def check_pine_strategy(df, min_signals=3, fund_data: dict = None):
    """
    Pine Script 多指标共振策略
    五指标：Range Filter, SuperTrend, RQK, HalfTrend, QQE Mod
    成交量、阳线和上影线作为交易确认过滤。
    """
    if df is None or df.empty:
        return False, {"reason": "数据为空"}

    # 1. 基础指标计算
    curr = df.iloc[-1]
    min_signals = max(1, min(int(min_signals), 5))
    
    # --- 五指标共振 ---
    rf_bullish = bool(curr.get('RF_Upward', False)) and not bool(curr.get('RF_Downward', False))
    st_bullish = bool(curr.get('ST_Signal', False))
    rqk_bullish = bool(curr.get('RQK_Up', False))
    half_bullish = bool(curr.get('HalfTrend_Up', False))
    qqe_bullish = bool(curr.get('QQE_Long', False))

    # --- 指标 3: Volume (量能确认) ---
    vol_bullish = False
    if '成交量' in df.columns and 'Vol_MA20' in df.columns:
        # 更加严格：至少放量 20%
        vol_bullish = curr['成交量'] > curr['Vol_MA20'] * 1.2
    
    # --- 核心过滤 (SOP 规范) ---
    # 1. 阳线过滤 (必须是阳线)
    is_bull_candle = curr['收盘'] > curr['开盘']
    
    # 2. 影线过滤 (上影线不能过长)
    body = abs(curr['收盘'] - curr['开盘'])
    upper_shadow = curr['最高'] - max(curr['收盘'], curr['开盘'])
    shadow_ratio = round(upper_shadow / body, 2) if body > 0 else 0
    is_shadow_ok = shadow_ratio < 0.5

    signal_flags = [rf_bullish, st_bullish, rqk_bullish, half_bullish, qqe_bullish]
    signal_count = sum(signal_flags)
    
    # 最终判断：共振信号足 + 是阳线 + 影线可接受
    is_match = (signal_count >= min_signals) and vol_bullish and is_bull_candle and is_shadow_ok

    debug_info = {
        "RangeFilter": "✅" if rf_bullish else "❌",
        "SuperTrend": "✅" if st_bullish else "❌",
        "RQK": "✅" if rqk_bullish else "❌",
        "HalfTrend": "✅" if half_bullish else "❌",
        "QQE_Mod": "✅" if qqe_bullish else "❌",
        "Volume": "✅" if vol_bullish else "❌",
        "core_signals": f"{signal_count}/5",
        "is_bull": "✅" if is_bull_candle else "❌",
        "shadow_ok": "✅" if is_shadow_ok else "❌"
    }

    if is_match:
        # 计算评分 (基于信号数量和一致性)
        tech_score = signal_count * 20 + (10 if vol_bullish else 0)
        
        # 基本面加分与高管背书
        fund_score, fund_ui_data = _calculate_fundamental_score(fund_data)
        total_score = tech_score + fund_score
        
        # 计算辅助显示数据 (用于前端表格)
        prev = df.iloc[-2]
        pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
        
        # 价格动能 (最近 3 天涨幅)
        pct_change_3d = (curr['收盘'] - df['收盘'].iloc[-4]) / df['收盘'].iloc[-4] * 100 if len(df) >= 4 else 0
        
        # 影线统计 (重复计算以便返回)
        res = {
            "Score": round(float(total_score), 1),
            "现价": curr['收盘'],
            "代码": curr.get('code', 'N/A'),
            "名称": curr.get('name', 'N/A'),
            "涨幅%": round(pct_change, 2),
            "信号数": f"{signal_count}/5",
            "3日涨幅%": round(pct_change_3d, 2),
            "RSI": round(curr.get('RSI', 0), 1),
            "DIF": round(curr.get('MACD_DIF', 0), 3),
            "BB": round(curr.get('BB_Width', 0), 4),
            "粘合度": round(curr.get('Sqz_Ratio', 0), 4),
            "影线比": shadow_ratio,
            "RF": "看涨" if rf_bullish else "看跌",
            "ST": "看涨" if st_bullish else "看跌",
            "RQK": "看涨" if rqk_bullish else "看跌",
            "HalfTrend": "看涨" if half_bullish else "看跌",
            "QQE": "看涨" if qqe_bullish else "看跌",
            "成交量": "放量" if vol_bullish else "缩量"
        }
        res.update(fund_ui_data)
        return True, res
    else:
        reasons = []
        if signal_count < min_signals: reasons.append(f"信号不足({signal_count}/5)")
        if not vol_bullish: reasons.append("量能未确认")
        if not is_bull_candle: reasons.append("非阳线")
        if not is_shadow_ok: reasons.append(f"影线过长({shadow_ratio})")
        debug_info["reason"] = ",".join(reasons) if reasons else "条件冲突"
        return False, debug_info


def check_tv_zp_strategy(df, fund_data: dict = None):
    """
    TradingView ZP 当前实盘配置策略。
    只在最新K线出现 long 信号时入选，short 信号作为风险提示返回。
    """
    if df is None or df.empty or len(df) < 130:
        return False, {"reason": f"历史数据不足({0 if df is None else len(df)})"}

    long_indices, short_indices, debug = _find_tv_zp_signal_indices(df)
    curr_idx = len(df) - 1
    is_long_now = curr_idx in long_indices
    is_short_now = curr_idx in short_indices
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100 if prev['收盘'] else 0

    volume_confirm = bool(debug.get("volume_confirm", pd.Series(False, index=df.index)).iloc[-1])
    qqe_long = bool(debug.get("qqe_long", pd.Series(False, index=df.index)).iloc[-1])
    qqe_short = bool(debug.get("qqe_short", pd.Series(False, index=df.index)).iloc[-1])
    leading_long = bool(debug.get("leading_long", pd.Series(False, index=df.index)).iloc[-1])
    leading_short = bool(debug.get("leading_short", pd.Series(False, index=df.index)).iloc[-1])

    debug_info = {
        "RangeFilter": "多" if leading_long else ("空" if leading_short else "中性"),
        "Volume": "✅" if volume_confirm else "❌",
        "QQE_Long": "✅" if qqe_long else "❌",
        "QQE_Short": "✅" if qqe_short else "❌",
        "signal": "short" if is_short_now else ("long" if is_long_now else "none"),
    }

    if not is_long_now:
        reasons = []
        if not leading_long: reasons.append("Range Filter未转多")
        if not volume_confirm: reasons.append("量能未过20日均量")
        if not qqe_long: reasons.append("QQE Line&Bar未确认")
        if is_short_now: reasons.append("出现short信号")
        debug_info["reason"] = ",".join(reasons) if reasons else "最新K线无long信号"
        return False, debug_info

    fund_score, fund_ui_data = _calculate_fundamental_score(fund_data)
    # 信号强度连续化（替代固定 75）：量能越强、涨幅越大→分越高。
    # 量能倍数在作用域内重建（与 check_squeeze 的 vol_ratio 同模式），区间约 70~90。
    vol_ratio = curr['成交量'] / curr['Vol_MA20'] if curr.get('Vol_MA20', 0) > 0 else 0
    signal_strength = min(vol_ratio, 3.0) * 5 + min(max(pct_change, 0), 5) * 1
    res = {
        "Score": round(70 + signal_strength + fund_score, 1),
        "现价": curr['收盘'],
        "涨幅%": round(pct_change, 2),
        "RSI": round(curr.get('RSI', 0), 1),
        "DIF": round(curr.get('MACD_DIF', 0), 3),
        "BB": round(curr.get('BB_Width', 0), 4),
        "粘合度": round(curr.get('Sqz_Ratio', 0), 4),
        "影线比": 0,
        "signal": "long",
        "reason": "TV-ZP long：Range Filter转多 + Volume/QQE确认",
        "signal_count": len(long_indices),
    }
    res.update(fund_ui_data)
    return True, res


def check_tv_dual_strategy(
    df,
    threshold=0.12,
    vol_multiplier=1.5,
    rsi_min=55,
    use_macd_filter=True,
    sqz_lookback=10,
    signal_window=3,
    require_both=False,
    fund_data: dict = None,
):
    """TradingView 对齐扫描：最近 N 根K线出现均线 B 共振和/或 TV-ZP long 即入选。"""
    if df is None or df.empty or len(df) < 130:
        return False, {"reason": f"历史数据不足({0 if df is None else len(df)})"}

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    start_idx = max(0, len(df) - max(1, int(signal_window)))

    ma_indices = _find_squeeze_signal_indices(
        df,
        threshold=threshold,
        vol_multiplier=vol_multiplier,
        rsi_min=rsi_min,
        use_macd_filter=use_macd_filter,
        use_bb_sqz=False,
        sqz_lookback=sqz_lookback,
        use_rs_filter=False,
    )
    zp_long_indices, zp_short_indices, _debug = _find_tv_zp_signal_indices(df)

    recent_ma = [idx for idx in ma_indices if idx >= start_idx]
    recent_zp = [idx for idx in zp_long_indices if idx >= start_idx]
    latest_long = max(recent_ma + recent_zp) if (recent_ma or recent_zp) else None
    latest_short = max(zp_short_indices) if zp_short_indices else None

    if require_both and (not recent_ma or not recent_zp):
        return False, {
            "reason": f"最近{signal_window}根K线未同时出现均线B共振和TV-ZP long",
            "tv_ma_signal": "B共振" if recent_ma else "无",
            "tv_zp_signal": "long" if recent_zp else "无",
            "tv_match": "未双命中",
        }
    if latest_long is None:
        return False, {"reason": f"最近{signal_window}根K线无均线B共振或TV-ZP long"}
    if latest_short is not None and latest_short > latest_long:
        return False, {"reason": "最近TV-ZP short晚于long，暂不入选", "signal": "short"}

    fund_score, fund_ui_data = _calculate_fundamental_score(fund_data)
    ma_hit = bool(recent_ma)
    zp_hit = bool(recent_zp)
    pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100 if prev['收盘'] else 0
    # 信号强度连续化：在双命中/单命中基准上叠加量能与涨幅，让"强突破"高于"弱突破"。
    # 不改变入选门槛（哪些股票被选中不变），只影响候选之间的相对排名。
    vol_ratio = curr['成交量'] / curr['Vol_MA20'] if curr.get('Vol_MA20', 0) > 0 else 0
    signal_strength = min(vol_ratio, 3.0) * 4 + min(max(pct_change, 0), 5) * 1
    base_score = 82 if ma_hit and zp_hit else 72
    score = base_score + signal_strength

    res = {
        "Score": round(score + fund_score, 1),
        "现价": curr['收盘'],
        "涨幅%": round(pct_change, 2),
        "RSI": round(curr.get('RSI_WILDER', curr.get('RSI', 0)), 1),
        "DIF": round(_squeeze_tv_macd(df)['dif'].iloc[-1], 3),
        "BB": round(curr.get('BB_Width', 0), 4),
        "粘合度": round(curr.get('Sqz_Ratio', 0), 4),
        "影线比": 0,
        "signal": "强共振" if require_both and ma_hit and zp_hit else ("双策略" if ma_hit and zp_hit else ("B共振" if ma_hit else "long")),
        "tv_ma_signal": "B共振" if ma_hit else "无",
        "tv_zp_signal": "long" if zp_hit else "无",
        "tv_match": "双命中" if ma_hit and zp_hit else "单命中",
        "reason": ("TV双策略强共振：" if require_both else "TV双策略对齐：") + " + ".join(
            part for part in [
                "均线B共振" if ma_hit else "",
                "TV-ZP long" if zp_hit else "",
            ] if part
        ),
        "signal_count": len(set(ma_indices + zp_long_indices)),
    }
    res.update(fund_ui_data)
    return True, res


def calculate_tv_zp_win_rate(df, stop_loss_pct=-8.0):
    empty_result = _empty_backtest_result()
    if df.empty or len(df) < 130:
        return empty_result

    try:
        long_indices, _short_indices, _debug = _find_tv_zp_signal_indices(df)
        signal_indices = [idx for idx in long_indices if idx < len(df) - 5]
        if len(signal_indices) == 0:
            return empty_result
        return _simulate_backtest(
            close_vals=df['收盘'].values,
            high_vals=df['最高'].values,
            low_vals=df['最低'].values,
            signal_indices=signal_indices,
            stop_loss_pct=stop_loss_pct,
            atr_vals=df['ATR'].values if 'ATR' in df.columns else None,
            vol_vals=df['成交量'].values if '成交量' in df.columns else None
        )
    except Exception:
        return empty_result


def calculate_tv_dual_win_rate(
    df,
    stop_loss_pct=-8.0,
    threshold=0.12,
    vol_multiplier=1.5,
    rsi_min=55,
    use_macd_filter=True,
    sqz_lookback=10,
    require_both=False,
    signal_window=3,
):
    empty_result = _empty_backtest_result()
    if df.empty or len(df) < 130:
        return empty_result

    try:
        ma_indices = _find_squeeze_signal_indices(
            df,
            threshold=threshold,
            vol_multiplier=vol_multiplier,
            rsi_min=rsi_min,
            use_macd_filter=use_macd_filter,
            use_bb_sqz=False,
            sqz_lookback=sqz_lookback,
            use_rs_filter=False,
        )
        zp_indices, _short_indices, _debug = _find_tv_zp_signal_indices(df)
        if require_both:
            signal_indices = sorted({
                max(ma_idx, zp_idx)
                for ma_idx in ma_indices
                for zp_idx in zp_indices
                if abs(ma_idx - zp_idx) < max(1, int(signal_window)) and max(ma_idx, zp_idx) < len(df) - 5
            })
        else:
            signal_indices = sorted({idx for idx in ma_indices + zp_indices if idx < len(df) - 5})
        if len(signal_indices) == 0:
            return empty_result
        return _simulate_backtest(
            close_vals=df['收盘'].values,
            high_vals=df['最高'].values,
            low_vals=df['最低'].values,
            signal_indices=signal_indices,
            stop_loss_pct=stop_loss_pct,
            atr_vals=df['ATR'].values if 'ATR' in df.columns else None,
            vol_vals=df['成交量'].values if '成交量' in df.columns else None
        )
    except Exception:
        return empty_result


def calculate_pine_win_rate(df, min_signals=3, stop_loss_pct=-8.0):
    """
    计算 Pine Script 策略的回测统计 (Enhanced v6.0)
    """
    empty_result = _empty_backtest_result()
    if df.empty or len(df) < 130:
        return empty_result

    try:
        signal_indices = [
            idx for idx in _find_pine_signal_indices(df, min_signals)
            if idx < len(df) - 5
        ]

        if len(signal_indices) == 0:
            return empty_result

        return _simulate_backtest(
            close_vals=df['收盘'].values,
            high_vals=df['最高'].values,
            low_vals=df['最低'].values,
            signal_indices=signal_indices,
            stop_loss_pct=stop_loss_pct,
            atr_vals=df['ATR'].values if 'ATR' in df.columns else None,
            vol_vals=df['成交量'].values if '成交量' in df.columns else None
        )

    except Exception as e:
        return empty_result


def check_consensus_strategy(df, is_weekly_ok=True, vol_multiplier=1.8, fund_data: dict = None):
    """
    Azul "共识" 策略 (场景博弈与沉积验证法)
    核心逻辑：周线向多 (MA60w), 日线 MA20 > MA60, HH (高点拾升), 放量突破大阳线, 趋势体质优
    """
    if len(df) < 60: return False, {"reason": "历史数据不足"}
    
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 1. 周线过滤 (由外部传入)
    if not is_weekly_ok:
        return False, {"reason": "月/周线大趋势未走强 (MA60w)"}
    
    # 2. 日线趋势状态：MA20 > MA60 (验证期/扩张期)
    is_trend_up = curr.get('MA20', 0) > curr.get('MA60', 0)
    
    # 3. 高低点结构：突破近期 20 日高点 (HH)
    recent_high = df['最高'].iloc[-21:-1].max()
    is_hh = curr['收盘'] >= recent_high
    
    # 4. 放量突破大阳线
    # 实体涨幅超过 2.5% 且是阳线
    is_big_bull = (curr['收盘'] > curr['开盘'] * 1.025) and (curr['收盘'] > curr['开盘'])
    vol_ratio = curr['成交量'] / curr['Vol_MA20'] if curr['Vol_MA20'] > 0 else 0
    is_vol_confirmed = vol_ratio >= vol_multiplier
    
    # 5. 趋势体质 (沉积截面)：过去 5 天中放量上涨的天数
    trend_quality = curr.get('Trend_Quality', 0)
    is_quality_ok = trend_quality >= 2
    
    # 6. 影线过滤
    body = abs(curr['收盘'] - curr['开盘'])
    upper_shadow = curr['最高'] - max(curr['收盘'], curr['开盘'])
    shadow_ratio = upper_shadow / body if body > 0 else 0
    is_shadow_ok = shadow_ratio < 0.4
    
    match = is_trend_up and is_hh and is_big_bull and is_vol_confirmed and is_quality_ok and is_shadow_ok
    
    debug_info = {
        "MA20>MA60": "✅" if is_trend_up else "❌",
        "HH突破": "✅" if is_hh else "❌",
        "大阳线": "✅" if is_big_bull else "❌",
        "量能确认": f"{vol_ratio:.1f}倍",
        "沉积体质": f"{trend_quality}/5",
        "shadow": f"{shadow_ratio:.2f}",
        "weekly": "✅" if is_weekly_ok else "❌"
    }
    
    if match:
        tech_score = (vol_ratio * 15) + (trend_quality * 5) + (25 if is_hh else 0) + (10 if curr['RSI'] > 60 else 0)
        
        fund_score, fund_ui_data = _calculate_fundamental_score(fund_data)
        total_score = tech_score + fund_score
        
        res = {
            "Score": round(float(total_score), 1),
            "代码": curr.get('code', 'N/A'),
            "名称": curr.get('name', 'N/A'),
            "现价": curr['收盘'],
            "涨幅%": round((curr['收盘'] - prev['收盘']) / prev['收盘'] * 100, 2),
            "结构": "HH突破",
            "体质": f"{trend_quality}/5",
            "成交量": f"{vol_ratio:.1f}x",
            "RSI": round(curr.get('RSI', 0), 1),
            "DIF": round(curr.get('MACD_DIF', 0), 3),
            "BB": round(curr.get('BB_Width', 0), 4),
            "影线比": round(shadow_ratio, 2)
        }
        res.update(fund_ui_data)
        return True, res
    else:
        reasons = []
        if not is_trend_up: reasons.append("均线未多头")
        if not is_hh: reasons.append("未越前高")
        if not is_big_bull: reasons.append("实体不够")
        if not is_vol_confirmed: reasons.append(f"量能欠缺({vol_ratio:.1f})")
        if not is_quality_ok: reasons.append(f"体质欠佳({trend_quality}/5)")
        if not is_shadow_ok: reasons.append("上影偏长")
        debug_info["reason"] = ",".join(reasons)
        return False, debug_info


def calculate_consensus_win_rate(df, stop_loss_pct=-8.0):
    """
    计算 Azul 共识策略的回测统计 (Enhanced v6.0)
    """
    empty_result = _empty_backtest_result()
    if df.empty or len(df) < 80:
        return empty_result
    
    try:
        is_trend_up = (df['MA20'] > df['MA60']).astype(int)
        
        high_20 = df['最高'].rolling(window=20).max().shift(1)
        is_hh = (df['收盘'] >= high_20).astype(int)
        
        is_big_bull = ((df['收盘'] > df['开盘'] * 1.025) & (df['收盘'] > df['开盘'])).astype(int)
        vol_ratio = df['成交量'] / df['Vol_MA20']
        is_vol = (vol_ratio >= 1.8).astype(int)
        is_quality = (df['Trend_Quality'] >= 2).astype(int)
        
        body = (df['收盘'] - df['开盘']).abs()
        upper_shadow = df['最高'] - df[['收盘', '开盘']].max(axis=1)
        is_shadow = np.where(body > 0, (upper_shadow / body < 0.4).astype(int), 1).astype(int)
        
        signals = is_trend_up & is_hh & is_big_bull & is_vol & is_quality & is_shadow
        valid_range = df.index < len(df) - 5
        signal_indices = df.index[(signals == 1) & valid_range].tolist()
        
        if len(signal_indices) == 0:
            return empty_result
        
        return _simulate_backtest(
            close_vals=df['收盘'].values,
            high_vals=df['最高'].values,
            low_vals=df['最低'].values,
            signal_indices=signal_indices,
            stop_loss_pct=stop_loss_pct,
            atr_vals=df['ATR'].values if 'ATR' in df.columns else None,
            vol_vals=df['成交量'].values if '成交量' in df.columns else None
        )
    except Exception:
        return empty_result

def evaluate_exit_signals(
    df: pd.DataFrame, 
    entry_price: float, 
    high_since_entry: float,
    stop_loss_pct: float = FIXED_STOP_LOSS_PCT
) -> List[Dict[str, str]]:
    """
    高度优化的卖出/预警评估引擎。
    支持：保本逻辑、阶梯移动止损、趋势破位、量能见顶。
    
    Returns:
        List of {"level": "warning"|"critical", "reason": "...", "suggestion": "..."}
    """
    if df.empty or entry_price <= 0:
        return []

    latest = df.iloc[-1]
    curr_price = float(latest['收盘'])
    high_price = max(high_since_entry, float(latest['最高']))
    
    pl_pct = (curr_price - entry_price) / entry_price * 100
    max_pl_pct = (high_price - entry_price) / entry_price * 100
    
    alerts = []
    risk = compute_paper_risk_levels_with_context(entry_price, high_price, curr_price, None, code)
    active_stop = risk.get("active_stop_price") or risk.get("stop_price") or 0

    # --- 1. 绝对止损 & ATR 自适应止损 (Survival First) ---
    actual_stop_loss_pct = stop_loss_pct
    atr = latest.get('ATR')
    if atr and not pd.isna(atr) and atr > 0:
        # 动态止损位 = entry - N * ATR
        atr_stop_price = entry_price - (atr * ATR_STOP_MULTIPLIER)
        atr_stop_pct = (atr_stop_price - entry_price) / entry_price * 100
        
        # 限制自适应止损在合理区间
        atr_stop_pct = max(ATR_STOP_MAX_PCT, min(ATR_STOP_MIN_PCT, atr_stop_pct))
        actual_stop_loss_pct = atr_stop_pct

    if curr_price <= active_stop:
        alerts.append({
            "level": "critical",
            "reason": f"触及执行风控价 ¥{active_stop:.2f}（{risk.get('risk_stage', '分阶段风控')}）",
            "suggestion": "触发当前风控底线，建议无条件平仓"
        })
        return alerts

    if pl_pct <= actual_stop_loss_pct:
        alerts.append({
            "level": "critical",
            "reason": f"触及风控止损位 ({round(actual_stop_loss_pct, 1)}%)",
            "suggestion": "触发风控底线，建议无条件平仓"
        })
        return alerts # 止损优先级最高，直接返回

    # --- 2. 保本逻辑 (Protect Capital) ---
    if risk.get("capital_protect_price") and curr_price <= risk["capital_protect_price"]:
        alerts.append({
            "level": "critical",
            "reason": "触发保本机制（盈利后回撤至成本线）",
            "suggestion": "防止盈利转亏损，建议止损出局"
        })

    # --- 3. 阶梯移动止损 (Trailing Stop) ---
    if risk.get("moving_stop_price") and curr_price < risk["moving_stop_price"]:
        alerts.append({
            "level": "critical" if max_pl_pct >= TIER_HIGH_PROFIT_PCT else "warning",
            "reason": f"触及{risk.get('risk_stage', '移动风控')}线 ¥{risk['moving_stop_price']:.2f}",
            "suggestion": "建议减仓或落袋，避免盈利回吐"
        })

    # --- 4. 技术趋势破位 (Trend Break) ---
    ema5 = latest.get('EMA5', 0)
    ema20 = latest.get('EMA20', 0)
    
    if curr_price < ema20 and ema20 > 0:
        if curr_price < ema5:
            alerts.append({
                "level": "warning",
                "reason": "双均线破位 (EMA5+EMA20)",
                "suggestion": "短期趋势走坏，建议观察是否有反抽卖点"
            })
        else:
            alerts.append({
                "level": "none", # 仅提醒
                "reason": "跌破 20 日波段线",
                "suggestion": "关注支撑强度"
            })

    # MACD 死叉检测
    macd_dif = latest.get('MACD_DIF', 0)
    macd_dea = latest.get('MACD_DEA', 0)
    if len(df) >= 2:
        prev_macd_dif = df.iloc[-2].get('MACD_DIF', 0)
        prev_macd_dea = df.iloc[-2].get('MACD_DEA', 0)
        
        # 如果今天死叉 (前一天 DIF >= DEA, 今天 DIF < DEA)
        if prev_macd_dif >= prev_macd_dea and macd_dif < macd_dea:
            level = "warning" if macd_dif > 0 else "critical"  # 零轴下死叉更严重
            alerts.append({
                "level": level,
                "reason": "日线 MACD 死叉",
                "suggestion": "波段见顶信号，建议减仓或清仓"
            })

    # --- 5. 情绪/量能见顶 (Climax) ---
    vol = float(latest['成交量'])
    vol_ma = latest.get('Vol_MA20', 0)
    is_red = curr_price < float(latest['开盘'])
    
    if vol > vol_ma * VOLUME_CLIMAX_MULTIPLIER and is_red:
        alerts.append({
            "level": "warning",
            "reason": "放量滞涨/阴线 (3倍巨量)",
            "suggestion": "疑似主力高位出货，建议先行了结"
        })

    return alerts
