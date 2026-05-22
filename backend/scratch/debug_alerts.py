import sys
import os
from datetime import datetime
import time

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from core.logging_config import logger
from core.db import get_db_engine
import pandas as pd
from sqlalchemy import text

print("Step 1: Connecting to Database...")
engine = get_db_engine()
print("Database engine initialized.")

print("Step 2: Fetching open positions...")
start = time.time()
try:
    df_paper = pd.read_sql("SELECT * FROM paper_trading WHERE status = 'OPEN'", engine)
    print(f"Fetched {len(df_paper)} open positions in {time.time() - start:.2f}s")
except Exception as e:
    print(f"Error fetching open positions: {e}")
    sys.exit(1)

if df_paper.empty:
    print("No open positions found. Exiting.")
    sys.exit(0)

print("Step 3: Fetching market snapshot...")
from core.data import get_market_snapshot
start = time.time()
try:
    snapshot = get_market_snapshot()
    print(f"Fetched snapshot with {len(snapshot)} rows in {time.time() - start:.2f}s")
except Exception as e:
    print(f"Error fetching snapshot: {e}")
    sys.exit(1)

snapshot_map = snapshot.set_index('code')['price'].to_dict()

print("Step 4: Querying historical K-line for each position...")
for idx, row in df_paper.iterrows():
    code = row['code']
    name = row['name']
    curr_price = snapshot_map.get(code)
    print(f"  [{idx+1}/{len(df_paper)}] Stock {name}({code}), current price: {curr_price}")
    if not curr_price:
        print(f"    Skipping: no current price in snapshot")
        continue
    
    query = text("""
        SELECT date as "日期", close as "收盘", open as "开盘", 
               high as "最高", low as "最低", vol as "成交量"
        FROM daily_k
        WHERE code = :code
        ORDER BY date DESC LIMIT 40
    """)
    try:
        q_start = time.time()
        with engine.connect() as conn:
            df_hist = pd.read_sql(query, conn, params={"code": code})
        print(f"    Queried {len(df_hist)} rows in {time.time() - q_start:.4f}s")
    except Exception as e:
        print(f"    Error querying historical data: {e}")

print("🎉 Debug script completed successfully!")
