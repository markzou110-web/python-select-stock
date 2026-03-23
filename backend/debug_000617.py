import sys
import os
import pandas as pd
import akshare as ak
from datetime import datetime, timedelta

# Add backend to path
sys.path.insert(0, '/Users/liangzou/Desktop/AI_Tools/python-select-stock/backend')

# Disable proxies
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

from core.indicators import calculate_indicators, calculate_pine_indicators
from core.strategy import check_pine_strategy

def debug_stock(code):
    print(f"--- Debugging {code} ---")
    
    # 1. Fetch data
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=(datetime.now() - timedelta(days=100)).strftime("%Y%m%d"), adjust="qfq")
        if df.empty:
            print(f"Error: No data for {code}")
            return
    except Exception as e:
        print(f"Error fetching data: {e}")
        return

    # 2. Rename columns to match what indicators expect
    # akshare returns: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
    
    # 3. Calculate indicators
    df['code'] = code
    df['name'] = 'Unknown'
    df = calculate_indicators(df, enable_pine_indicators=True)
    
    # 4. Check strategy
    match, result = check_pine_strategy(df)
    
    print(f"Match: {match}")
    if not match:
        print(f"Reason: {result.get('reason')}")
    else:
        print("Result details:")
        for k, v in result.items():
            print(f"  {k}: {v}")
            
    # 5. Inspect specific indicator values for the last 5 days
    print("\nIndicator values (Last 5 days):")
    cols = ['日期', '收盘', 'RF_Filter', 'RF_Upward', 'QQE_Long', '成交量', 'Vol_MA20']
    available_cols = [c for c in cols if c in df.columns]
    print(df[available_cols].tail())
    
    # Check Price vs Filter
    last = df.iloc[-1]
    print(f"\nLast Price: {last['收盘']}, RF_Filter: {round(last['RF_Filter'], 3)}")
    print(f"Price > Filter: {last['收盘'] > last['RF_Filter']}")

if __name__ == "__main__":
    debug_stock("000617")
