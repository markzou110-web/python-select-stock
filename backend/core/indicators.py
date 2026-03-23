import pandas as pd
import numpy as np
import akshare as ak
from core.logging_config import logger

def calculate_indicators(df, current_price=None, current_vol=None, current_open=None, periods=[5, 10, 20, 60], bench_df=None, enable_pine_indicators=False):
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

    # Pine Script 策略指标 (可选启用)
    if enable_pine_indicators:
        df = calculate_pine_indicators(df)

    return df

def calculate_pine_indicators(df):
    """
    计算 Pine Script 策略中的核心指标 (v6.0 - TradingView 算法精确对齐版)
    对应 TV 指标: Range Filter [DonovanWall], QQE Mod, SuperTrend
    """
    if df.empty or len(df) < 60:
        return df

    close = df['收盘'].values
    high = df['最高'].values
    low = df['最低'].values
    open_p = df['开盘'].values
    
    # --- 1. Range Filter (DonovanWall 精确实现) ---
    # TV 参数: Sampling Period = 50, Range Multiplier = 3.0
    wper = 50
    avgt = 3.0
    
    abs_diff = np.abs(close - np.roll(close, 1))
    abs_diff[0] = 0
    # 使用 SMA 计算平均波动量
    rng = pd.Series(abs_diff).rolling(window=wper).mean().values * avgt
    
    rf_filter = np.zeros(len(close))
    rf_filter[0] = close[0]
    
    # 递归过滤算法 (创建步进效果)
    for i in range(1, len(close)):
        curr_rng = rng[i] if not np.isnan(rng[i]) else 0
        if close[i] > rf_filter[i-1]:
            # 价格上涨时，过滤线跟随上涨 (取最大值)
            rf_filter[i] = max(rf_filter[i-1], close[i] - curr_rng)
        else:
            # 价格下跌时，过滤线跟随下跌 (取最小值)
            rf_filter[i] = min(rf_filter[i-1], close[i] + curr_rng)

    # 状态判断 (TV 逻辑: 价格穿透过滤器)
    # 本次更新重点：只有价格在过滤线上方 且 过滤线不再下降时才算 Up
    rf_up = (close > rf_filter) & (rf_filter >= np.roll(rf_filter, 1))
    rf_down = (close < rf_filter) & (rf_filter <= np.roll(rf_filter, 1))
    
    # --- 2. QQE Mod (3-9-21-55 灵敏版本) ---
    # 对齐截图参数: RSI=3, Smoothing=5, Factor=3
    rsi_period = 3
    smoothing = 5
    qqe_factor = 3

    # 计算 RSI
    delta = df['收盘'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=rsi_period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=rsi_period).mean()
    rsi_val = 100 - (100 / (1 + (gain / loss.replace(0, np.nan)).fillna(0)))
    
    # RSI 平滑 (RSI_MA)
    rsi_ma = rsi_val.ewm(span=smoothing, adjust=False).mean()
    
    # 计算 ATR of RSI (用于动态带宽)
    atr_rsi = rsi_ma.diff().abs().rolling(window=rsi_period * 2 + 1).mean()
    dar = atr_rsi.ewm(span=rsi_period * 2 + 1, adjust=False).mean() * qqe_factor
    
    rsi_ma_v = rsi_ma.values
    dar_v = dar.values
    qqe_trend = np.ones(len(close))
    
    tr = 1
    upper_band = 50.0
    lower_band = 50.0
    
    for i in range(1, len(close)):
        if np.isnan(rsi_ma_v[i]) or np.isnan(dar_v[i]):
            continue
            
        new_upper = rsi_ma_v[i] + dar_v[i]
        new_lower = rsi_ma_v[i] - dar_v[i]
        
        if tr == 1: 
            lower_band = max(lower_band, new_lower)
            if rsi_ma_v[i] < lower_band:
                tr = -1
                upper_band = new_upper
        else: 
            upper_band = min(upper_band, new_upper)
            if rsi_ma_v[i] > upper_band:
                tr = 1
                lower_band = new_lower
        qqe_trend[i] = tr

    # QQE 买点优化：必须在趋势内且 RSI_MA > 50 (TV QQE Mod 常用逻辑)
    df['QQE_Long'] = (qqe_trend == 1) & (rsi_ma > 50)

    # --- 3. 其他辅助指标 ---
    # SuperTrend 保持默认 (10, 3.0)
    # RQK 保持默认
    # Half Trend 保持默认

    df['RF_Filter'] = rf_filter
    df['RF_Upward'] = rf_up
    df['RF_Downward'] = rf_down
    
    # 重新计算 SuperTrend (使用默认 10, 3.0)
    atr_st = pd.Series(high - low).rolling(window=10).mean().values
    st_trend = np.ones(len(close))
    st_l, st_u = close - 3.0*atr_st, close + 3.0*atr_st
    for i in range(1, len(close)):
        if st_trend[i-1] == 1:
            if close[i] < st_l[i-1]: st_trend[i] = -1
        else:
            if close[i] > st_u[i-1]: st_trend[i] = 1
        
        if st_trend[i] == 1: st_l[i] = max(st_l[i], st_l[i-1]) if not np.isnan(st_l[i-1]) else st_l[i]
        else: st_u[i] = min(st_u[i], st_u[i-1]) if not np.isnan(st_u[i-1]) else st_u[i]
    
    df['ST_Signal'] = st_trend == 1
    
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
