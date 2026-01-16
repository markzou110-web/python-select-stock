import pandas as pd
import numpy as np
from .money_flow import get_individual_fund_flow, calculate_money_flow_score

def check_strategy(df, threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True, use_bb_sqz=False, sqz_lookback=10, use_rs_filter=True, use_money_flow_filter=False, money_flow_days=3):
    """执行无门问禅：A股均线粘合战法 (Pine Script v5.0 Alignment)

    新增参数:
    - use_money_flow_filter: 是否启用资金流过滤（默认 False 保持向后兼容）
    - money_flow_days: 资金流统计天数（默认 3 日）
    """
    if len(df) < 120: return False, {"reason": f"历史数据不足 ({len(df)}天)"}

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 1. 均线系统 (EMA 5, 10, 20, 60) ---
    ma_values = [df['EMA5'], df['EMA10'], df['EMA20'], df['EMA60']]
    ma_df = pd.concat(ma_values, axis=1)
    ma_max_all = ma_df.max(axis=1)
    ma_min_all = ma_df.min(axis=1)
    sqz_ratios = (ma_max_all - ma_min_all) / ma_min_all
    
    # 粘合判断 (最近 N 天内出现过粘合)
    was_squeeze_recent = sqz_ratios.iloc[-sqz_lookback:].min() < threshold
    
    # --- 2. 突破动作 (Close > All MAs AND Close > Open) ---
    curr_ma_max = ma_max_all.iloc[-1]
    is_breakout = (curr['收盘'] > curr_ma_max) and (curr['收盘'] > curr['开盘'])
    
    # --- 3. 趋势与量能 ---
    is_trending = curr['收盘'] > curr['EMA60']
    vol_ratio = curr['成交量'] / curr['Vol_MA20'] if curr['Vol_MA20'] > 0 else 0
    # SOP: 量比 > 1.5 (对齐 TV: volume > vol_ma * vol_multiplier)
    is_volume = (vol_ratio > vol_multiplier)
    
    # --- 5. RSI 强度 (对齐 Pine Script: rsi > rsi_min) ---
    is_rsi_ok = curr['RSI'] > rsi_min
    
    # --- 6. MACD 优化 (SOP: 快线 > 慢线，红柱) ---
    is_macd_ok = curr['MACD_DIF'] > curr['MACD_DEA'] if use_macd_filter else True
    
    # --- 7. 相对强度 (RS) vs 指数 ---
    is_rs_ok = True
    if use_rs_filter:
        if 'RS' in df.columns and 'RS_MA50' in df.columns:
            is_rs_ok = curr['RS'] > curr['RS_MA50']
        else:
            # 如果强制要求 RS 过滤但数据缺失，则视为不通过 (防止跳过过滤器)
            is_rs_ok = False
    
    # --- 8. 波动率收缩 (BB) ---
    is_bb_ok = True
    if use_bb_sqz:
        bb_quantile_20 = df['BB_Width'].iloc[-120:].quantile(0.2)
        is_bb_ok = curr['BB_Width'] <= bb_quantile_20

    # --- 9. 资金流向过滤 (新增) ---
    is_money_flow_ok = True
    recent_main_flow = 0

    if use_money_flow_filter:
        code = df.iloc[-1].get('code', '')

        # 优先从 DataFrame 中读取（如果已包含资金流数据）
        if 'main_net_inflow' in df.columns:
            recent_main_flow = df['main_net_inflow'].iloc[-money_flow_days:].sum()
            curr_main_flow = df['main_net_inflow'].iloc[-1]

            # 判断条件：
            # 1. 最近 N 日主力净流入为正，或
            # 2. 当日主力大幅流入（> 1000 万元）
            is_money_flow_ok = (recent_main_flow > 0) or (curr_main_flow > 1000)
        else:
            # 如果 DataFrame 中无资金流数据，尝试从数据库获取
            if code:
                df_flow = get_individual_fund_flow(code, days=money_flow_days)
                if not df_flow.empty:
                    recent_main_flow = df_flow['main_net_inflow'].sum()
                    is_money_flow_ok = (recent_main_flow > 0)
                else:
                    # 无数据时不通过
                    is_money_flow_ok = False
            else:
                is_money_flow_ok = False

    debug_info = {
        "squeeze": round(sqz_ratios.iloc[-1], 4),
        "vol_ratio": round(vol_ratio, 2),
        "rsi": round(curr['RSI'], 1),
        "is_breakout": is_breakout,
        "is_volume": is_volume,
        "is_rsi_ok": is_rsi_ok,
        "is_macd_ok": is_macd_ok,
        "is_bb_ok": is_bb_ok,
        "was_sqz_recent": was_squeeze_recent,
        "is_rs_ok": is_rs_ok,
        "is_money_flow_ok": is_money_flow_ok,
        "main_flow_3d": round(recent_main_flow, 2) if use_money_flow_filter else 0
    }

    if not was_squeeze_recent:
        debug_info["reason"] = "近期未现均线粘合"
        return False, debug_info

    # 综合判断 (对齐 TradeView: trend_weekly_ok and was_squeeze_recent and (close > ma_max) and (close > ema5) and vol_ok and (rsi > rsi_min) and rs_strong and macd_condition)
    # 注意：Pine Script 中 close > ma_max 已经涵盖了突破所有均线。
    is_ema20_ok = curr['收盘'] > curr['EMA20']
    
    # 计算是否为首日触发买点 (Signal vs Trend)
    # 首日买点定义：今天突破了均线簇，但昨天没有突破
    was_breakout = (prev['收盘'] > ma_max_all.iloc[-2])
    is_signal = is_breakout and not was_breakout

    if was_squeeze_recent and is_breakout and is_ema20_ok and is_volume and is_rsi_ok and is_macd_ok and is_bb_ok and is_rs_ok and is_money_flow_ok:
        pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
        
        # 计算影线比 (Upper Shadow / Body) 用于显示
        body = abs(curr['收盘'] - curr['开盘'])
        upper_shadow = curr['最高'] - max(curr['收盘'], curr['开盘'])
        shadow_ratio = round(upper_shadow / body, 2) if body > 0 else 0
        
        # SOP 评分权重调整：启用资金流时使用新权重
        flow_score = 0
        if use_money_flow_filter:
            # 新权重：量能(20%) + 粘合(40%) + RSI(20%) + 资金流(20%)
            if 'main_net_inflow' in df.columns:
                recent_flow_for_score = df['main_net_inflow'].iloc[-money_flow_days:].sum()
            else:
                code = curr.get('code', '')
                if code:
                    df_flow = get_individual_fund_flow(code, days=money_flow_days)
                    recent_flow_for_score = df_flow['main_net_inflow'].sum() if not df_flow.empty else 0
                else:
                    recent_flow_for_score = 0

            flow_score = calculate_money_flow_score(
                pd.DataFrame({'main_net_inflow': [recent_flow_for_score]})
            )
            score = (vol_ratio * 20) + ((threshold - sqz_ratios.iloc[-1]) * 100 * 40) + (curr['RSI'] * 0.20) + flow_score
        else:
            # 原始权重：量能(25%) + 粘合(50%) + RSI(25%)
            score = (vol_ratio * 25) + ((threshold - sqz_ratios.iloc[-1]) * 100 * 50) + (curr['RSI'] * 0.25)

        return True, {
            "Score": round(score, 2),
            "涨幅%": round(pct_change, 2),
            "现价": curr['收盘'],
            "代码": curr.get('code', 'N/A'),
            "名称": curr.get('name', 'N/A'),
            "粘合度": round(sqz_ratios.iloc[-1], 4),
            "RSI": round(curr['RSI'], 1),
            "DIF": round(curr['MACD_DIF'], 3),
            "BB": round(curr['BB_Width'], 4),
            "影线比": shadow_ratio,
            "主力净流入": round(recent_main_flow, 2) if use_money_flow_filter else None
        }

    # 详细失败原因 (SOP 术语)
    reasons = []
    if not is_breakout: reasons.append("未突破均线簇")
    if not is_volume: reasons.append("量能未爆发")
    if not is_rsi_ok: reasons.append("强度不足(RSI)")
    if not is_macd_ok: reasons.append("MACD未金叉")
    if not is_bb_ok: reasons.append("布林带未收缩")
    if not is_rs_ok: reasons.append("弱于大盘(RS)")
    if use_money_flow_filter and not is_money_flow_ok: reasons.append("主力资金流出")
    
    debug_info["reason"] = ",".join(reasons) if reasons else "多因子未共振"
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

def range_filter(close_prices, period=100, multiplier=3.0):
    """
    计算 Range Filter 线 (移植自 Pine Script)
    1. 平滑波动范围 (Smooth Range)
    2. 递归生成 Filter 线
    """
    if len(close_prices) < 2:
        return close_prices.tolist()
        
    # 1. 计算平滑波动范围 (Smooth Range)
    diff = abs(close_prices - close_prices.shift(1))
    avrng = diff.ewm(span=period, adjust=False).mean() 
    wper = period * 2 - 1
    smooth_rng = avrng.ewm(span=wper, adjust=False).mean() * multiplier

    # 2. 计算 Range Filter 线
    rf = [close_prices.iloc[0]] # 初始化
    cp_list = close_prices.tolist()
    sr_list = smooth_rng.tolist()
    
    for i in range(1, len(cp_list)):
        x = cp_list[i]
        r = sr_list[i]
        prev_rf = rf[-1]
        
        if x > prev_rf:
            if (x - r) < prev_rf:
                rf.append(prev_rf)
            else:
                rf.append(x - r)
        else: # x <= prev_rf
            if (x + r) > prev_rf:
                rf.append(prev_rf)
            else:
                rf.append(x + r)
    
    return rf

def check_range_filter_strategy(df, period=100, multiplier=3.0, rsi_min=55):
    """
    Range Filter 策略检查
    逻辑：当 收盘价 > RF线 且 昨天的收盘价 <= 昨天的RF线 (上穿) -> 买点
    """
    min_len = max(period * 2 + 5, 120)
    if len(df) < min_len:
        return False, {"reason": f"Range Filter 需要更多历史数据 (当前 {len(df)}/需求 {min_len})"}

    close_prices = df['收盘']
    rf_line = range_filter(close_prices, period=period, multiplier=multiplier)
    df = df.copy()
    df['RF'] = rf_line
    
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 信号触发：上穿
    is_buy_signal = (curr['收盘'] > curr['RF']) and (prev['收盘'] <= prev['RF'])
    
    # 辅助过滤：RSI 强度
    is_rsi_ok = curr['RSI'] > rsi_min
    
    debug_info = {
        "price": round(curr['收盘'], 2),
        "rf": round(curr['RF'], 2),
        "prev_price": round(prev['收盘'], 2),
        "prev_rf": round(prev['RF'], 2),
        "rsi": round(curr['RSI'], 1),
        "is_rsi_ok": is_rsi_ok,
        "is_buy_signal": is_buy_signal
    }
    
    if is_buy_signal and is_rsi_ok:
        pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
        # 评分逻辑：距离 RF 线的距离 + RSI 强度
        score = ((curr['收盘'] - curr['RF']) / curr['RF'] * 1000) + (curr['RSI'] * 0.5)
        
        return True, {
            "Score": round(score, 2),
            "涨幅%": round(pct_change, 2),
            "现价": curr['收盘'],
            "代码": curr.get('code', 'N/A'),
            "名称": curr.get('name', 'N/A'),
            "RSI": round(curr['RSI'], 1),
            "RF线": round(curr['RF'], 2),
            "粘合度": 0, # Range Filter 无此指标
            "DIF": round(curr.get('MACD_DIF', 0), 3),
            "BB": round(curr.get('BB_Width', 0), 4),
            "is_signal": True # Range Filter 匹配即为上穿信号
        }

    reasons = []
    if not is_buy_signal: reasons.append("未触发RF上穿信号")
    if not is_rsi_ok: reasons.append("RSI强度不足")
    
    debug_info["reason"] = ",".join(reasons)
    return False, debug_info
