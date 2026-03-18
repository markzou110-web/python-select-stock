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
def batch_calculate_indicators(df, snapshot_df=None, periods=[5, 10, 20, 60], bench_df=None):
    """
    全市场批量向量化指标计算 (v6.0 - 极致提速)
    df: 包含所有股票历史的大 DataFrame，必须按 [code, 日期] 排序
    snapshot_df: 今日实时快照 (可选)
    bench_df: 大盘指数历史 (用于计算 RS)
    """
    if df.empty: return df
    
    # --- 1. 数据对齐与准备 ---
    # 确保日期格式统一
    if not pd.api.types.is_datetime64_any_dtype(df['日期']):
         df['日期'] = pd.to_datetime(df['日期'])
    
    # 按照代码和日期严格排序，这对于 ewm 和 rolling 很重要
    df = df.sort_values(['code', '日期'])
    
    # 分组对象
    group = df.groupby('code', sort=False)
    
    # --- 2. 向量化计算 EMA ---
    for p in periods:
        # 使用 pandas 原生的 groupby.ewm，速度极快
        df[f'EMA{p}'] = group['收盘'].ewm(span=p, adjust=False).mean().reset_index(level=0, drop=True)
    
    # --- 3. 向量化计算 RSI (14) ---
    delta = group['收盘'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    # 临时列用于 rolling 计算
    df['_gain'] = gain
    df['_loss'] = loss
    
    avg_gain = df.groupby('code', sort=False)['_gain'].rolling(window=14).mean().reset_index(level=0, drop=True)
    avg_loss = df.groupby('code', sort=False)['_loss'].rolling(window=14).mean().reset_index(level=0, drop=True)
    
    rs_raw = avg_gain / avg_loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs_raw.fillna(0)))
    df.drop(columns=['_gain', '_loss'], inplace=True)
    
    # --- 4. 向量化计算 MACD ---
    ema12 = group['收盘'].ewm(span=12, adjust=False).mean().reset_index(level=0, drop=True)
    ema26 = group['收盘'].ewm(span=26, adjust=False).mean().reset_index(level=0, drop=True)
    df['MACD_DIF'] = ema12 - ema26
    df['MACD_DEA'] = df.groupby('code', sort=False)['MACD_DIF'].ewm(span=9, adjust=False).mean().reset_index(level=0, drop=True)
    df['MACD_HIST'] = (df['MACD_DIF'] - df['MACD_DEA']) * 2
    
    # --- 5. 向量化计算布林带 (20) ---
    df['BB_Mid'] = group['收盘'].rolling(window=20).mean().reset_index(level=0, drop=True)
    std = group['收盘'].rolling(window=20).std().reset_index(level=0, drop=True)
    df['BB_Upper'] = df['BB_Mid'] + 2 * std
    df['BB_Lower'] = df['BB_Mid'] - 2 * std
    df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid'].replace(0, np.nan)
    
    # 量能均线
    df['Vol_MA20'] = group['成交量'].rolling(window=20).mean().reset_index(level=0, drop=True)
    
    # --- 6. 均线粘合度 (Squeeze Ratio) ---
    # 这里不需要 groupby，因为是同一行不同列的操作
    ma_cols = [f'EMA{p}' for p in periods]
    df['Sqz_Ratio'] = (df[ma_cols].max(axis=1) - df[ma_cols].min(axis=1)) / df[ma_cols].min(axis=1).replace(0, np.nan)
    
    # --- 7. 相对强度 (RS) vs 基准 ---
    if bench_df is not None and not bench_df.empty:
        # 指数日期对齐
        bench_df = bench_df.copy()
        if not pd.api.types.is_datetime64_any_dtype(bench_df['日期']):
             bench_df['日期'] = pd.to_datetime(bench_df['日期'])
        
        # 准备合并
        df = df.merge(bench_df[['日期', '收盘']], on='日期', suffixes=('', '_bench'), how='left')
        df['RS'] = df['收盘'] / df['收盘_bench'].ffill()
        # RS MA50 也需要分组
        df['RS_MA50'] = df.groupby('code', sort=False)['RS'].rolling(window=50).mean().reset_index(level=0, drop=True)
        df.drop(columns=['收盘_bench'], inplace=True)
        
    return df
