import pandas as pd
import numpy as np

def check_strategy(df, threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_zero=True, use_bb_sqz=True, sqz_lookback=10):
    """执行选股策略逻辑 (v5.0 - Multi-Factor Resonance)"""
    if len(df) < 120: return False, {"reason": f"历史数据不足 ({len(df)}天)"}

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 1. 均线粘合 (was_squeeze_recent) ---
    ma_values = [df['EMA5'], df['EMA10'], df['EMA20'], df['EMA60']]
    ma_df = pd.concat(ma_values, axis=1)
    ma_max_all = ma_df.max(axis=1)
    ma_min_all = ma_df.min(axis=1)
    sqz_ratios = (ma_max_all - ma_min_all) / ma_min_all
    
    was_squeeze_recent = sqz_ratios.iloc[-sqz_lookback:].min() < threshold
    
    # --- 2. 突破动作 ---
    curr_ma_max = ma_max_all.iloc[-1]
    is_breakout = curr['收盘'] > curr_ma_max and curr['收盘'] > curr['EMA5'] and curr['收盘'] > curr['开盘']
    
    # --- 3. 趋势与量能 ---
    is_trending = curr['EMA20'] >= prev['EMA20'] and curr['收盘'] > curr['EMA60']
    vol_ratio = curr['成交量'] / curr['Vol_MA20'] if curr['Vol_MA20'] > 0 else 0
    is_volume = vol_ratio >= vol_multiplier
    
    # --- 4. 高级因子 (Resonance) ---
    is_rsi_ok = curr['RSI'] > rsi_min
    is_macd_ok = curr['MACD_DIF'] > 0 if use_macd_zero else True
    
    # --- 5. 相对强度 (RS) ---
    is_rs_ok = True
    if 'RS' in df.columns and 'RS_MA50' in df.columns:
        is_rs_ok = curr['RS'] > curr['RS_MA50']
    
    # --- 6. 波动率收缩 (BB) ---
    is_bb_ok = True
    if use_bb_sqz:
        bb_quantile_20 = df['BB_Width'].iloc[-120:].quantile(0.2)
        is_bb_ok = curr['BB_Width'] <= bb_quantile_20

    debug_info = {
        "squeeze": round(sqz_ratios.iloc[-1], 4),
        "vol_ratio": round(vol_ratio, 2),
        "rsi": round(curr['RSI'], 1),
        "is_breakout": is_breakout,
        "is_trending": is_trending,
        "is_volume": is_volume,
        "is_rsi_ok": is_rsi_ok,
        "is_macd_ok": is_macd_ok,
        "is_bb_ok": is_bb_ok,
        "was_sqz_recent": was_squeeze_recent,
        "is_rs_ok": is_rs_ok
    }

    if not was_squeeze_recent:
        debug_info["reason"] = "近期未现粘合"
        return False, debug_info

    if is_breakout and is_trending and is_volume and is_rsi_ok and is_macd_ok and is_bb_ok and is_rs_ok:
        pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
        score = (vol_ratio * 20) + ((0.2 - sqz_ratios.iloc[-1]) * 100 * 40) + (curr['RSI'] * 0.5)
        return True, {
            "Score": round(score, 2),
            "涨幅%": round(pct_change, 2),
            "现价": curr['收盘'],
            "代码": curr.get('code', 'N/A'),
            "名称": curr.get('name', 'N/A'),
            "粘合度": round(sqz_ratios.iloc[-1], 4),
            "RSI": round(curr['RSI'], 1),
            "DIF": round(curr['MACD_DIF'], 3),
            "BB": round(curr['BB_Width'], 4)
        }
    
    return False, debug_info

def calculate_historical_win_rate(df):
    """快速回测过去1年的胜率 (v5.0)"""
    if df.empty or len(df) < 130: return 0, 0
    signals = []
    
    for i in range(120, len(df) - 5):
        sub_df = df.iloc[:i+1]
        match, _ = check_strategy(sub_df)
        if match:
            # 检查未来 5 天最高价涨幅是否超过 3%
            future = df.iloc[i+1 : i+6]
            entry_price = sub_df.iloc[-1]['收盘']
            max_future = future['最高'].max()
            if (max_future - entry_price) / entry_price >= 0.03:
                signals.append(True)
            else:
                signals.append(False)
    
    if not signals: return 0, 0
    win_rate = round(sum(signals) / len(signals) * 100, 1)
    return win_rate, len(signals)
