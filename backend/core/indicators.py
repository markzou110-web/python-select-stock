import pandas as pd
import numpy as np
import akshare as ak

def calculate_indicators(df, current_price=None, current_vol=None, current_open=None, periods=[5, 10, 20, 60], bench_df=None):
    """计算 EMA, MACD, BB, RSI 和 RS (Optimized)"""
    if df.empty: return df
    
    # 注入实时数据
    if current_price is not None:
        last_row = df.iloc[-1].copy()
        last_row['收盘'] = float(current_price)
        if current_vol is not None: last_row['成交量'] = float(current_vol)
        if current_open is not None: last_row['开盘'] = float(current_open)
        df.iloc[-1] = last_row

    for p in periods:
        df[f'EMA{p}'] = df['收盘'].ewm(span=p, adjust=False).mean()
    
    # RSI (14)
    delta = df['收盘'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs_raw = gain / loss.replace(0, np.nan) # Avoid division by zero
    df['RSI'] = 100 - (100 / (1 + rs_raw.fillna(0)))
    
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
    df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid'].replace(0, np.nan)
    
    df['Vol_MA20'] = df['成交量'].rolling(window=20).mean()
    
    # Squeeze Ratio Pre-calculation
    if all(col in df.columns for col in ['EMA5', 'EMA10', 'EMA20', 'EMA60']):
        ma_cols = ['EMA5', 'EMA10', 'EMA20', 'EMA60']
        df['Sqz_Ratio'] = (df[ma_cols].max(axis=1) - df[ma_cols].min(axis=1)) / df[ma_cols].min(axis=1).replace(0, np.nan)

    # Relative Strength (RS) vs SSE (000001) - Optimized with pre-filtered bench_df
    try:
        if bench_df is not None and not bench_df.empty:
            df = df.merge(bench_df, on='日期', suffixes=('', '_bench'), how='left')
            df['RS'] = df['收盘'] / df['收盘_bench'].ffill()
            df['RS_MA50'] = df['RS'].rolling(window=50).mean()
        else:
            # Fallback (mostly for K-line where bench_df might not be passed)
            from .data import get_index_hist
            b_df = get_index_hist("000001")
            if not b_df.empty:
                min_date, max_date = df['日期'].min(), df['日期'].max()
                b_slice = b_df[(b_df['日期'] >= min_date) & (b_df['日期'] <= max_date)][['日期', '收盘']]
                df = df.merge(b_slice, on='日期', suffixes=('', '_bench'), how='left')
                df['RS'] = df['收盘'] / df['收盘_bench'].ffill()
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
            
            # 重采样逻辑 (周五作为收盘参考)
            df_w = temp_df['收盘'].resample('W').last().dropna().to_frame()
            if len(df_w) < 30: return False
        else:
            # 只有在非本地模式下才去拉取
            df_w = ak.stock_zh_a_hist(symbol=code, period="weekly", adjust="qfq")
            if len(df_w) < 30: return False
            
        df_w['EMA10w'] = df_w['收盘'].ewm(span=10, adjust=False).mean()
        df_w['EMA30w'] = df_w['收盘'].ewm(span=30, adjust=False).mean()
        
        curr = df_w.iloc[-1]
        return curr['EMA10w'] > curr['EMA30w']
    except Exception as e:
        logger.debug(f"Weekly indicator failed for {code}: {e}")
        return False
