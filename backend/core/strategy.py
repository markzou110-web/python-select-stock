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
        score = core_signals * 30 + (10 if st_bullish else 0) + (10 if rqk_bullish else 0)
        
        # 计算辅助显示数据 (用于前端表格)
        prev = df.iloc[-2]
        pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
        
        # 价格动能 (最近 3 天涨幅)
        pct_change_3d = (curr['收盘'] - df['收盘'].iloc[-4]) / df['收盘'].iloc[-4] * 100 if len(df) >= 4 else 0
        
        # 影线统计 (重复计算以便返回)
        return True, {
            "Score": round(float(score), 1),
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
    else:
        reasons = []
        if core_signals < min_signals: reasons.append(f"信号不足({core_signals}/3)")
        if not is_bull_candle: reasons.append("非阳线")
        if not is_shadow_ok: reasons.append(f"影线过长({shadow_ratio})")
        debug_info["reason"] = ",".join(reasons) if reasons else "条件冲突"
        return False, debug_info


def calculate_pine_win_rate(df, min_signals=3):
    """
    计算 Pine Script 策略的历史胜率 (基于 3 指标模型)
    """
    if df.empty or len(df) < 60:
        return 0, 0

    try:
        # 1. 核心共振点计算
        rf_bullish = (df['RF_Upward'] & ~df.get('RF_Downward', False)).astype(int)
        qqe_bullish = df.get('QQE_Long', False).astype(int)
        vol_bullish = (df['成交量'] > df.get('Vol_MA20', df['成交量'].rolling(20).mean())).astype(int)

        bullish_count = rf_bullish + qqe_bullish + vol_bullish

        # 找出信号点 (至少满足 min_signals 个)
        # 限制范围：离当前最新日期至少留出 5 天用于计算盈亏
        valid_range = df.index < len(df) - 5
        signal_mask = (bullish_count >= min_signals) & valid_range
        signal_indices = df.index[signal_mask]

        if len(signal_indices) == 0:
            return 0, 0

        # 2. 盈亏统计 (3% 目标价)
        success_count = 0
        close_vals = df['收盘'].values
        high_vals = df['最高'].values
        
        for idx in signal_indices:
            entry_price = close_vals[idx]
            # 未来 5 天内是否存在最高价涨幅达到 3% 的点?
            future_max = np.max(high_vals[idx+1 : idx+6])
            if (future_max - entry_price) / entry_price >= 0.03:
                success_count += 1

        win_rate = round(success_count / len(signal_indices) * 100, 1)
        return win_rate, len(signal_indices)

    except Exception as e:
        return 0, 0


def check_consensus_strategy(df, is_weekly_ok=True, vol_multiplier=1.8):
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
        score = (vol_ratio * 15) + (trend_quality * 5) + (25 if is_hh else 0) + (10 if curr['RSI'] > 60 else 0)
        return True, {
            "Score": round(float(score), 1),
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


def calculate_consensus_win_rate(df):
    """
    计算 Azul 共识策略的历史胜率
    """
    if df.empty or len(df) < 80: return 0, 0
    
    try:
        # 1. 预计算核心信号
        is_trend_up = (df['MA20'] > df['MA60']).astype(int)
        
        # HH 判断 (简化：收盘价 > 过去 20 天最高)
        high_20 = df['最高'].rolling(window=20).max().shift(1)
        is_hh = (df['收盘'] >= high_20).astype(int)
        
        is_big_bull = ((df['收盘'] > df['开盘'] * 1.025) & (df['收盘'] > df['开盘'])).astype(int)
        vol_ratio = df['成交量'] / df['Vol_MA20']
        is_vol = (vol_ratio >= 1.8).astype(int)
        is_quality = (df['Trend_Quality'] >= 2).astype(int)
        
        body = (df['收盘'] - df['开盘']).abs()
        upper_shadow = df['最高'] - df[['收盘', '开盘']].max(axis=1)
        is_shadow = (upper_shadow / body < 0.4).astype(int)
        
        # 信号掩码
        signals = is_trend_up & is_hh & is_big_bull & is_vol & is_quality & is_shadow
        # 限制范围：离当前最新日期至少留出 5 天用于计算盈亏
        valid_range = df.index < len(df) - 5
        signal_indices = df.index[signals == 1 & valid_range]
        
        if len(signal_indices) == 0: return 0, 0
        
        # 2. 统计胜率
        success_count = 0
        close_vals = df['收盘'].values
        high_vals = df['最高'].values
        
        for idx in signal_indices:
            entry_price = close_vals[idx]
            future_max = np.max(high_vals[idx+1 : idx+6])
            if (future_max - entry_price) / entry_price >= 0.03:
                success_count += 1
                
        return round(success_count / len(signal_indices) * 100, 1), len(signal_indices)
    except:
        return 0, 0
