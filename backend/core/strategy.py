import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from core.risk_constants import (
    FIXED_STOP_LOSS_PCT, TIER_HIGH_PROFIT_PCT, TIER_HIGH_TRAIL_RATIO,
    TIER_MID_PROFIT_PCT, TIER_MID_TRAIL_RATIO,
    CAPITAL_PROTECT_THRESHOLD_PCT, CAPITAL_PROTECT_FLOOR_PCT,
    VOLUME_CLIMAX_MULTIPLIER, BACKTEST_MAX_HOLD_DAYS,
    BACKTEST_TRAILING_ATR_MULT,
    ATR_STOP_MULTIPLIER, ATR_STOP_MIN_PCT, ATR_STOP_MAX_PCT
)


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
        if strategy_type == "pine":
            return _find_pine_signal_indices(df, min_signals)
        elif strategy_type == "consensus":
            return _find_consensus_signal_indices(df)
        else:
            return _find_squeeze_signal_indices(df, threshold, vol_multiplier, rsi_min)
    except Exception:
        return []


def _find_squeeze_signal_indices(df: pd.DataFrame, threshold: float, vol_multiplier: float, rsi_min: int) -> List[int]:
    """均线粘合策略的信号索引"""
    ma_cols = ['EMA5', 'EMA10', 'EMA20', 'EMA60']
    if not all(c in df.columns for c in ma_cols):
        return []

    ma_max = df[ma_cols].max(axis=1)
    c_breakout = (df['收盘'] >= ma_max) & (df['收盘'] > df['开盘'])
    c_volume = (df['成交量'] / df['Vol_MA20'].replace(0, np.nan)) >= vol_multiplier
    c_rsi = df['RSI'] >= rsi_min
    c_macd = df['MACD_DIF'] > df['MACD_DEA']
    c_sqz = df['Sqz_Ratio'].rolling(10).min() < threshold if 'Sqz_Ratio' in df.columns else pd.Series(False, index=df.index)

    mask = c_breakout & c_volume & c_rsi & c_macd & c_sqz
    valid = df.index[mask & (df.index >= 120)]
    return valid.tolist()


def _find_pine_signal_indices(df: pd.DataFrame, min_signals: int) -> List[int]:
    """Pine Script 共振策略的信号索引"""
    rf_bullish = (df.get('RF_Upward', pd.Series(False, index=df.index)) & ~df.get('RF_Downward', pd.Series(False, index=df.index))).astype(int)
    qqe_bullish = df.get('QQE_Long', pd.Series(False, index=df.index)).astype(int)
    vol_bullish = (df['成交量'] > df.get('Vol_MA20', df['成交量'].rolling(20).mean()) * 1.2).astype(int)

    bullish_count = rf_bullish + qqe_bullish + vol_bullish
    is_bull_candle = df['收盘'] > df['开盘']

    mask = (bullish_count >= min_signals) & is_bull_candle
    valid = df.index[mask & (df.index >= 50)]
    return valid.tolist()


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
    if strategy_type == "pine":
        parts = []
        if row.get('RF_Upward', False): parts.append("RF看涨")
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
        parts.append(f"RSI={row.get('RSI', 0):.0f}")
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
        if strategy_type == "pine":
            min_signals = int(param_value) if param_name == "min_signals" else 3
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
        return {
            "win_rate": 0, "signal_count": 0, "avg_hold_days": 0,
            "avg_return": 0, "max_drawdown": 0, "profit_factor": 0,
            "stop_loss_hits": 0, "vol_skipped": 0, "time_stopped": 0
        }
    
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
    
    return {
        "win_rate": win_rate,
        "signal_count": total_trades,
        "avg_hold_days": avg_hold,
        "avg_return": avg_return,
        "max_drawdown": round(max_drawdown * 100, 2),  # 百分比
        "profit_factor": min(profit_factor, 99.0),  # cap for display
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


def check_strategy(df, threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True, use_bb_sqz=True, sqz_lookback=10, use_rs_filter=True, fund_data: dict = None):
    """执行无门问禅：A股均线粘合战法 (Optimized)"""
    if len(df) < 120: return False, {"reason": f"历史数据不足 ({len(df)}天)"}

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 1. 均线系统 (使用预计算的 Sqz_Ratio) ---
    if 'Sqz_Ratio' not in df.columns:
        ma_cols = ['EMA5', 'EMA10', 'EMA20', 'EMA60']
        ma_max = df[ma_cols].max(axis=1)
        ma_min = df[ma_cols].min(axis=1)
        df['Sqz_Ratio'] = (ma_max - ma_min) / ma_min
    
    # 粘合判断 (最近 N 天内出现过粘合)
    was_squeeze_recent = df['Sqz_Ratio'].iloc[-sqz_lookback:].min() < threshold
    
    # --- 2. 突破动作 ---
    curr_ma_max = df[['EMA5', 'EMA10', 'EMA20', 'EMA60']].iloc[-1].max()
    is_breakout = (curr['收盘'] >= curr_ma_max) and (curr['收盘'] > curr['开盘'])
    
    # --- 3. 趋势与量能 ---
    vol_ratio = curr['成交量'] / curr['Vol_MA20'] if curr['Vol_MA20'] > 0 else 0
    is_volume = (vol_ratio >= vol_multiplier)
    
    # --- 5. RSI 强度 ---
    is_rsi_ok = curr['RSI'] >= rsi_min
    
    # --- 6. MACD ---
    is_macd_ok = curr['MACD_DIF'] > curr['MACD_DEA'] if use_macd_filter else True
    
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
        "squeeze": round(df['Sqz_Ratio'].iloc[-1], 4),
        "vol_ratio": round(vol_ratio, 2),
        "rsi": round(curr['RSI'], 1),
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
        
        # 技术评分
        tech_score = (vol_ratio * 25) + ((threshold - df['Sqz_Ratio'].iloc[-1]) * 100 * 50) + (curr['RSI'] * 0.4)
        
        # 基本面加权
        fund_score, fund_ui_data = _calculate_fundamental_score(fund_data)
        total_score = tech_score + fund_score
        
        res = {
            "Score": round(total_score, 2),
            "涨幅%": round(pct_change, 2),
            "现价": curr['收盘'],
            "代码": curr.get('code', 'N/A'),
            "名称": curr.get('name', 'N/A'),
            "粘合度": round(df['Sqz_Ratio'].iloc[-1], 4),
            "RSI": round(curr['RSI'], 1),
            "DIF": round(curr['MACD_DIF'], 3),
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

def calculate_historical_win_rate(df, stop_loss_pct=-8.0):
    """向量化计算回测统计 (Enhanced v6.0 - 含止损/回撤/盈亏比)"""
    empty_result = {"win_rate": 0, "signal_count": 0, "avg_hold_days": 0, "avg_return": 0, "max_drawdown": 0, "profit_factor": 0, "stop_loss_hits": 0}
    if df.empty or len(df) < 130:
        return empty_result

    try:
        # 1. 预计算所有行的共振信号 (向量化)
        ma_cols = ['EMA5', 'EMA10', 'EMA20', 'EMA60']
        ma_max = df[ma_cols].max(axis=1)

        c_breakout = (df['收盘'] >= ma_max) & (df['收盘'] > df['开盘'])
        c_volume = (df['成交量'] / df['Vol_MA20']) >= 1.5
        c_rsi = df['RSI'] >= 55
        c_macd = df['MACD_DIF'] > df['MACD_DEA']
        c_sqz = df['Sqz_Ratio'].rolling(10).min() < 0.12

        pre_signals = c_breakout & c_volume & c_rsi & c_macd & c_sqz
        valid_indices = df.index[120:-5]
        candidate_indices = df.index[pre_signals & (df.index.isin(valid_indices))]

        if len(candidate_indices) == 0:
            return empty_result

        final_signal_indices = []
        for idx in candidate_indices:
            bb_limit = df['BB_Width'].iloc[idx-119 : idx+1].quantile(0.2)
            if df.loc[idx, 'BB_Width'] <= bb_limit:
                final_signal_indices.append(idx)

        if not final_signal_indices:
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
    Pine Script 多指标共振策略 (基于用户截图优化的 3 指标核心版)
    核心指标：Range Filter, Volume, QQE Mod
    """
    if df is None or df.empty:
        return False, {"reason": "数据为空"}

    # 1. 基础指标计算
    curr = df.iloc[-1]
    
    # --- 指标 1: Range Filter (必选) ---
    rf_bullish = False
    if 'RF_Upward' in df.columns:
        rf_bullish = curr['RF_Upward'] and not df.iloc[-1].get('RF_Downward', False)
    
    # --- 指标 2: QQE Mod (核心共振) ---
    qqe_bullish = False
    if 'QQE_Long' in df.columns:
        qqe_bullish = curr['QQE_Long']

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

    # 统计核心 3 指标看涨数量
    core_signals = sum([rf_bullish, qqe_bullish, vol_bullish])
    
    # 最终判断：共振信号足 + 是阳线 + 影线可接受
    is_match = (core_signals >= min_signals) and is_bull_candle and is_shadow_ok

    debug_info = {
        "RangeFilter": "✅" if rf_bullish else "❌",
        "QQE_Mod": "✅" if qqe_bullish else "❌",
        "Volume": "✅" if vol_bullish else "❌",
        "core_signals": f"{core_signals}/3",
        "is_bull": "✅" if is_bull_candle else "❌",
        "shadow_ok": "✅" if is_shadow_ok else "❌"
    }

    # 辅助判断 (用于加分)
    st_bullish = curr.get('ST_Signal', False)
    rqk_bullish = curr.get('RQK_Up', False)

    if is_match:
        # 计算评分 (基于信号数量和一致性)
        tech_score = core_signals * 30 + (10 if st_bullish else 0) + (10 if rqk_bullish else 0)
        
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
            "信号数": f"{core_signals}/3",
            "3日涨幅%": round(pct_change_3d, 2),
            "RSI": round(curr.get('RSI', 0), 1),
            "DIF": round(curr.get('MACD_DIF', 0), 3),
            "BB": round(curr.get('BB_Width', 0), 4),
            "粘合度": round(curr.get('Sqz_Ratio', 0), 4),
            "影线比": shadow_ratio,
            "RF": "看涨" if rf_bullish else "看跌",
            "QQE": "看涨" if qqe_bullish else "看跌",
            "成交量": "放量" if vol_bullish else "缩量"
        }
        res.update(fund_ui_data)
        return True, res
    else:
        reasons = []
        if core_signals < min_signals: reasons.append(f"信号不足({core_signals}/3)")
        if not is_bull_candle: reasons.append("非阳线")
        if not is_shadow_ok: reasons.append(f"影线过长({shadow_ratio})")
        debug_info["reason"] = ",".join(reasons) if reasons else "条件冲突"
        return False, debug_info


def calculate_pine_win_rate(df, min_signals=3, stop_loss_pct=-8.0):
    """
    计算 Pine Script 策略的回测统计 (Enhanced v6.0)
    """
    empty_result = {"win_rate": 0, "signal_count": 0, "avg_hold_days": 0, "avg_return": 0, "max_drawdown": 0, "profit_factor": 0, "stop_loss_hits": 0}
    if df.empty or len(df) < 60:
        return empty_result

    try:
        rf_bullish = (df['RF_Upward'] & ~df.get('RF_Downward', False)).astype(int)
        qqe_bullish = df.get('QQE_Long', False).astype(int)
        vol_bullish = (df['成交量'] > df.get('Vol_MA20', df['成交量'].rolling(20).mean())).astype(int)

        bullish_count = rf_bullish + qqe_bullish + vol_bullish

        valid_range = df.index < len(df) - 5
        signal_mask = (bullish_count >= min_signals) & valid_range
        signal_indices = df.index[signal_mask].tolist()

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
    empty_result = {"win_rate": 0, "signal_count": 0, "avg_hold_days": 0, "avg_return": 0, "max_drawdown": 0, "profit_factor": 0, "stop_loss_hits": 0}
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

    if pl_pct <= actual_stop_loss_pct:
        alerts.append({
            "level": "critical",
            "reason": f"触及风控止损位 ({round(actual_stop_loss_pct, 1)}%)",
            "suggestion": "触发风控底线，建议无条件平仓"
        })
        return alerts # 止损优先级最高，直接返回

    # --- 2. 保本逻辑 (Protect Capital) ---
    # 如果曾经盈利超过 CAPITAL_PROTECT_THRESHOLD_PCT%，但现在跌回 CAPITAL_PROTECT_FLOOR_PCT% 以内
    if max_pl_pct >= CAPITAL_PROTECT_THRESHOLD_PCT and pl_pct <= CAPITAL_PROTECT_FLOOR_PCT:
        alerts.append({
            "level": "critical",
            "reason": "触发保本机制（盈利后回撤至成本线）",
            "suggestion": "防止盈利转亏损，建议止损出局"
        })

    # --- 3. 阶梯移动止损 (Trailing Stop) ---
    # 盈利 > TIER_HIGH_PROFIT_PCT%: 允许从最高点回落
    if max_pl_pct >= TIER_HIGH_PROFIT_PCT:
        if curr_price < high_price * TIER_HIGH_TRAIL_RATIO:
            alerts.append({
                "level": "critical",
                "reason": f"高位大幅回撤 ({round(high_price/curr_price*100-100, 1)}%)",
                "suggestion": f"触发 {TIER_HIGH_PROFIT_PCT}% 档位移动止损，建议落袋为安"
            })
    # 盈利 > TIER_MID_PROFIT_PCT%: 允许从最高点回落
    elif max_pl_pct >= TIER_MID_PROFIT_PCT:
        if curr_price < high_price * TIER_MID_TRAIL_RATIO:
            alerts.append({
                "level": "warning",
                "reason": f"触及 {TIER_MID_PROFIT_PCT}% 档位移动止盈线",
                "suggestion": "建议减仓 50% 或收紧止损"
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
