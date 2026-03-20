import pandas as pd
from core.db import get_db_engine
from sqlalchemy import text
from core.indicators import batch_calculate_indicators
from core.strategy import check_strategy
import json
import numpy as np

engine = get_db_engine()
query = text('SELECT code, date as "日期", open as "开盘", high as "最高", low as "最低", close as "收盘", vol as "成交量" FROM daily_k WHERE code IN (\'000001\', \'600000\', \'600519\', \'300750\') ORDER BY date ASC')
with engine.connect() as conn:
    df = pd.read_sql(query, conn)

if pd.api.types.is_datetime64_any_dtype(df['日期']):
    df['日期'] = df['日期'].dt.strftime('%Y-%m-%d')
elif df['日期'].dtype == 'object':
    df['日期'] = df['日期'].astype(str).str[:10]

master_df = batch_calculate_indicators(df.copy())
print("Null counts in indicators:")
print(master_df.isnull().sum())

# test one stock
df_one = master_df[master_df['code'] == '600000'].reset_index(drop=True)
if not df_one.empty:
    pd.set_option('display.max_columns', None)
    print(df_one.tail(5)[['日期', '收盘', 'EMA5', 'MACD_DIF', 'Sqz_Ratio', 'RSI', 'BB_Width', 'Vol_MA20']])
    match, stats = check_strategy(df_one)
    print("Match:", match)
    print("Stats:", stats)
