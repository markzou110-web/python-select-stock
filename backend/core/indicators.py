import pandas as pd
import numpy as np
import akshare as ak

def calculate_indicators(df, current_price=None, current_vol=None, current_open=None, periods=[5, 10, 20, 60]):
    """计算 EMA，支持注入当前快照价格以对齐 (v2.6.5 修复阳线判断)"""
    if df.empty: return df
    
    # 注入实时数据
    if current_price is not None:
        last_row = df.iloc[-1].copy()
        last_row['收盘'] = float(current_price)
        if current_vol is not None: last_row['成交量'] = float(current_vol)
        if current_open is not None: last_row['开盘'] = float(current_open)
        # 如果日期相同则覆盖，否则追加（通常用于盘中实时对齐）
        df.iloc[-1] = last_row

    for p in periods:
        df[f'EMA{p}'] = df['收盘'].ewm(span=p, adjust=False).mean()
    
    # RSI (14)
    delta = df['收盘'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # MACD
    ema12 = df['收盘'].ewm(span=12, adjust=False).mean()
    ema26 = df['收盘'].ewm(span=26, adjust=False).mean()
    df['MACD_DIF'] = ema12 - ema26
    df['MACD_DEA'] = df['MACD_DIF'].ewm(span=9, adjust=False).mean()
    df['MACD_HIST'] = (df['MACD_DIF'] - df['MACD_DEA']) * 2
    
    # Bollinger Bands
    df['BB_Mid'] = df['收盘'].rolling(window=20).mean()
    std = df['收盘'].rolling(window=20).std()
    df['BB_Upper'] = df['BB_Mid'] + 2 * std
    df['BB_Lower'] = df['BB_Mid'] - 2 * std
    df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid']
    
    df['Vol_MA20'] = df['成交量'].rolling(window=20).mean()
    
    # Relative Strength (RS) vs SSE (000001) - 简易版
    try:
        from .data import get_index_hist
        bench_df = get_index_hist("000001")
        if not bench_df.empty:
            # 对齐日期
            df = df.merge(bench_df[['日期', '收盘']], on='日期', suffixes=('', '_bench'), how='left')
            df['RS'] = df['收盘'] / df['收盘_bench']
            df['RS_MA50'] = df['RS'].rolling(window=50).mean()
    except: pass

    return df

def get_weekly_indicators(code, df=None, local_only=False):
    """获取周线趋势指标 (v5.1 - 支持本地重采样)"""
    try:
        if local_only and df is not None and not df.empty:
            # --- 核心优化：从本地日线重采样为周线 ---
            temp_df = df.copy()
            temp_df['日期'] = pd.to_datetime(temp_df['日期'])
            temp_df.set_index('日期', inplace=True)
            df_w = temp_df['收盘'].resample('W').last().dropna().to_frame()
        else:
            df_w = ak.stock_zh_a_hist(symbol=code, period="weekly", adjust="qfq")
            
        if len(df_w) < 30: return False
            
        df_w['EMA10w'] = df_w['收盘'].ewm(span=10, adjust=False).mean()
        df_w['EMA30w'] = df_w['收盘'].ewm(span=30, adjust=False).mean()
        
        # 对齐 TradeView [1] 逻辑：使用上一周锁定的数据，防止周中漂移
        if len(df_w) < 2: return False
        prev_week = df_w.iloc[-2]
        return prev_week['EMA10w'] > prev_week['EMA30w']
    except Exception as e:
        print(f"⚠️ Weekly indicator failed for {code}: {e}")
        return False
