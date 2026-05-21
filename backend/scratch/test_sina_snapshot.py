import akshare as ak
import pandas as pd
import os

# 确保无代理
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''

print("Fetching Eastmoney snapshot...")
try:
    df_em = ak.stock_zh_a_spot_em()
    print("EM snapshot columns:", df_em.columns.tolist())
    print("EM snapshot head:\n", df_em.head(2))
except Exception as e:
    print("EM snapshot failed:", e)

print("\nFetching Sina snapshot...")
try:
    df_sina = ak.stock_zh_a_spot()
    print("Sina snapshot columns:", df_sina.columns.tolist())
    print("Sina snapshot head:\n", df_sina.head(2))
except Exception as e:
    print("Sina snapshot failed:", e)
