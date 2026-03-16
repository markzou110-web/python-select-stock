import pandas as pd
import numpy as np

def check_strategy(df, threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True, use_bb_sqz=True, sqz_lookback=10, use_rs_filter=True):
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
        
        # 评分
        score = (vol_ratio * 25) + ((threshold - df['Sqz_Ratio'].iloc[-1]) * 100 * 50) + (curr['RSI'] * 0.4)
        return True, {
            "Score": round(score, 2),
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
    
    reasons = []
    if not is_breakout: reasons.append("未突破均线簇")
    if not is_volume: reasons.append("量能未爆发")
    if not is_rsi_ok: reasons.append("强度不足(RSI)")
    if not is_macd_ok: reasons.append("MACD未金叉")
    if not is_bb_ok: reasons.append("布林带未收缩")
    if not is_rs_ok: reasons.append("弱于大盘(RS)")
    
    debug_info["reason"] = ",".join(reasons) if reasons else "多因子未共振"
    return False, debug_info

def calculate_historical_win_rate(df):
    """向量化计算回测胜率 (Optimized v5.1)"""
    if df.empty or len(df) < 130: return 0, 0
    
    try:
        # 1. 预计算所有行的共振信号 (向量化)
        ma_cols = ['EMA5', 'EMA10', 'EMA20', 'EMA60']
        ma_max = df[ma_cols].max(axis=1)
        
        c_breakout = (df['收盘'] >= ma_max) & (df['收盘'] > df['开盘'])
        c_volume = (df['成交量'] / df['Vol_MA20']) >= 1.5
        c_rsi = df['RSI'] >= 55
        c_macd = df['MACD_DIF'] > df['MACD_DEA']
        c_sqz = df['Sqz_Ratio'].rolling(10).min() < 0.12
        
        # 核心优化：先用快速条件过滤出候选点，再对候选点进行昂贵的 BB Quantile 计算
        pre_signals = c_breakout & c_volume & c_rsi & c_macd & c_sqz
        valid_indices = df.index[120:-5]
        candidate_indices = df.index[pre_signals & (df.index.isin(valid_indices))]
        
        if len(candidate_indices) == 0:
            return 0, 0
            
        final_signal_indices = []
        for idx in candidate_indices:
            # 只有在候点才计算 120 天分位值
            bb_limit = df['BB_Width'].iloc[idx-119 : idx+1].quantile(0.2)
            if df.loc[idx, 'BB_Width'] <= bb_limit:
                final_signal_indices.append(idx)

        if not final_signal_indices:
            return 0, 0
            
        success_count = 0
        for idx in final_signal_indices:
            entry_price = df.loc[idx, '收盘']
            # 未来 5 天最高价
            future_max = df.loc[idx+1 : idx+5, '最高'].max()
            if (future_max - entry_price) / entry_price >= 0.03:
                success_count += 1
                
        win_rate = round(success_count / len(final_signal_indices) * 100, 1)
        return win_rate, len(final_signal_indices)
        
    except Exception as e:
        return 0, 0
