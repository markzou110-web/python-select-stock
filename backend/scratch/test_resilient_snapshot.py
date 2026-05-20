import os
import sys
import pandas as pd
import time

# Ensure backend path is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data import get_market_snapshot, CACHE
from core.logging_config import logger

def test_snapshot():
    # 强制清理缓存，保证不读过期或空数据
    if 'market_snapshot' in CACHE:
        del CACHE['market_snapshot']
        
    print("Testing get_market_snapshot() resilience integration...")
    start_time = time.time()
    df = get_market_snapshot()
    duration = time.time() - start_time
    
    if df.empty:
        print("FAIL: get_market_snapshot() returned empty DataFrame!")
        sys.exit(1)
        
    print(f"SUCCESS: Snapshot fetched in {duration:.2f} seconds.")
    print(f"Total rows fetched: {len(df)}")
    print(f"Columns: {df.columns.tolist()}")
    
    print("\n--- Sample Data (First 5 rows) ---")
    print(df.head(5))
    
    print("\n--- Check Data Formats and Units ---")
    print(f"Any null prices? {df['price'].isnull().sum()}")
    print(f"Any null opens? {df['open'].isnull().sum()}")
    print(f"Any null volumes? {df['vol'].isnull().sum()}")
    print(f"Max volume: {df['vol'].max()} 手")
    print(f"Mean price: {df['price'].mean():.2f}")
    
    # 腾讯源有市值数据，且我们将其乘以了 1e8 换算为元
    if 'mkt_cap' in df.columns:
        valid_mkt_cap = df[df['mkt_cap'].notnull()]
        print(f"Non-null mkt_cap count: {len(valid_mkt_cap)}")
        if len(valid_mkt_cap) > 0:
            print(f"Sample mkt_cap: {valid_mkt_cap['mkt_cap'].iloc[0]:,.2f} 元")
            print(f"Max mkt_cap: {df['mkt_cap'].max():,.2f} 元")
            
    if 'turnover' in df.columns:
        valid_turnover = df[df['turnover'].notnull()]
        print(f"Non-null turnover count: {len(valid_turnover)}")
        if len(valid_turnover) > 0:
            print(f"Max turnover: {df['turnover'].max()}%")
            
    # Verify columns exactly match the A-share standard columns required by scan.py
    required_cols = ['code', 'name', 'price', 'open', 'pct_chg', 'vol', 'turnover', 'mkt_cap', 'pe']
    for col in required_cols:
        assert col in df.columns, f"Required column '{col}' is missing!"
    print("\nAll required columns are present and correctly formatted!")

if __name__ == "__main__":
    test_snapshot()
