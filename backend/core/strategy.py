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


def check_pine_strategy(df, min_signals=3):
    """
    Pine Script 多指标共振策略

    基于 TradingView Pine Script 策略的多指标共振系统：
    - Range Filter (范围过滤器)
    - SuperTrend (超级趋势)
    - RQK (Rational Quadratic Kernel 核回归)
    - Half Trend (半趋势)
    - QQE Mod (量化指标)

    Args:
        df: 包含 Pine Script 指标的 DataFrame
        min_signals: 最少需要多少个指标共振才触发信号

    Returns:
        (is_match, debug_info): 是否匹配策略和调试信息
    """
    if df is None or df.empty:
        return False, {"reason": "数据为空"}

    # 检查必需的 Pine Script 指标列是否存在
    required_indicators = [
        'RF_Upward', 'RF_Downward',  # Range Filter
        'ST_Signal',  # SuperTrend
        'RQK_Up', 'RQK_Down',  # RQK
        'HT_Long', 'HT_Short',  # Half Trend
        'QQE_Long', 'QQE_Short'  # QQE Mod
    ]

    missing_indicators = [ind for ind in required_indicators if ind not in df.columns]
    if missing_indicators:
        return False, {"reason": f"缺少 Pine Script 指标: {', '.join(missing_indicators)}"}

    curr = df.iloc[-1]

    # 1. Range Filter 信号
    rf_bullish = curr['RF_Upward'] and not curr['RF_Downward']

    # 2. SuperTrend 信号
    st_bullish = curr['ST_Signal']

    # 3. RQK 信号 (检查最近趋势)
    rqk_bullish = curr['RQK_Up'] and not curr['RQK_Down']

    # 4. Half Trend 信号
    ht_bullish = curr['HT_Long'] and not curr['HT_Short']

    # 5. QQE 信号
    qqe_bullish = curr['QQE_Long'] and not curr['QQE_Short']

    # 统计看涨信号数量
    bullish_signals = sum([rf_bullish, st_bullish, rqk_bullish, ht_bullish, qqe_bullish])

    # 确认没有看跌信号主导 (至少 3 个指标共振)
    is_match = bullish_signals >= min_signals

    debug_info = {
        "RF": "看涨" if rf_bullish else "看跌/中性",
        "SuperTrend": "看涨" if st_bullish else "看跌",
        "RQK": "看涨" if rqk_bullish else "看跌/中性",
        "HalfTrend": "看涨" if ht_bullish else "看跌",
        "QQE": "看涨" if qqe_bullish else "看跌",
        "bullish_signals": bullish_signals,
        "min_required": min_signals
    }

    if is_match:
        # 计算评分 (基于信号数量和一致性)
        score = bullish_signals * 20  # 5 个指标，每个 20 分

        # 添加额外的评分因子
        prev = df.iloc[-2]

        # 价格动能 (最近 3 天涨幅)
        pct_change_3d = (curr['收盘'] - df['收盘'].iloc[-4]) / df['收盘'].iloc[-4] * 100 if len(df) >= 4 else 0

        # 量能确认
        vol_ratio = curr['成交量'] / df['成交量'].iloc[-20:].mean() if len(df) >= 20 else 1

        return True, {
            "Score": round(score, 1),
            "现价": curr['收盘'],
            "代码": curr.get('code', 'N/A'),
            "名称": curr.get('name', 'N/A'),
            "信号数": f"{bullish_signals}/5",
            "3日涨幅%": round(pct_change_3d, 2),
            "量能比": round(vol_ratio, 2),
            "RF": debug_info["RF"],
            "ST": debug_info["SuperTrend"],
            "RQK": debug_info["RQK"],
            "HT": debug_info["HalfTrend"],
            "QQE": debug_info["QQE"]
        }
    else:
        debug_info["reason"] = f"信号不足 ({bullish_signals}/{min_signals})"
        return False, debug_info


def calculate_pine_win_rate(df, min_signals=3):
    """
    计算 Pine Script 策略的历史胜率

    Args:
        df: 包含 Pine Script 指标的 DataFrame
        min_signals: 最少需要的共振信号数

    Returns:
        (win_rate, signal_count): 胜率和信号次数
    """
    if df.empty or len(df) < 60:
        return 0, 0

    try:
        # 检查是否已计算 Pine 指标
        if 'RF_Upward' not in df.columns:
            return 0, 0

        # 找出历史信号点 (需要至少 3 个指标共振)
        bullish_count = (
            (df['RF_Upward'] & ~df['RF_Downward']).astype(int) +
            (df['ST_Signal']).astype(int) +
            (df['RQK_Up'] & ~df['RQK_Down']).astype(int) +
            (df['HT_Long'] & ~df['HT_Short']).astype(int) +
            (df['QQE_Long'] & ~df['QQE_Short']).astype(int)
        )

        # 找出信号点 (至少需要 min_signals 个指标看涨)
        signal_indices = df.index[(bullish_count >= min_signals) & (df.index < len(df) - 5)]

        if len(signal_indices) == 0:
            return 0, 0

        success_count = 0
        for idx in signal_indices:
            entry_price = df.loc[idx, '收盘']
            # 未来 5 天最高价
            if idx + 5 < len(df):
                future_max = df.loc[idx+1:idx+5, '最高'].max()
                if (future_max - entry_price) / entry_price >= 0.03:
                    success_count += 1

        win_rate = round(success_count / len(signal_indices) * 100, 1)
        return win_rate, len(signal_indices)

    except Exception as e:
        return 0, 0
