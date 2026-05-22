import sys
import os
import time

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from core.logging_config import logger
from core.db import get_db_engine
from core.indicators import calculate_indicators
from core.strategy import evaluate_exit_signals
import pandas as pd
from sqlalchemy import text

print("Connecting to DB...")
engine = get_db_engine()

print("Fetching open positions...")
df_paper = pd.read_sql("SELECT * FROM paper_trading WHERE status = 'OPEN'", engine)
print(f"Loaded {len(df_paper)} open positions.")

from core.data import get_market_snapshot
print("Fetching snapshot...")
snapshot = get_market_snapshot()
snapshot_map = snapshot.set_index('code')['price'].to_dict()

print("Running indicator and signal evaluation loops...")
for idx, row in df_paper.iterrows():
    code = row['code']
    name = row['name']
    entry_price = float(row['entry_price'])
    high_since_entry = float(row.get('high_since_entry') or entry_price)
    
    curr_price = snapshot_map.get(code)
    if not curr_price:
        print(f"  Stock {name}({code}) has no price in snapshot. Skip.")
        continue
        
    query = text("""
        SELECT date as "日期", close as "收盘", open as "开盘", 
               high as "最高", low as "最低", vol as "成交量"
        FROM daily_k
        WHERE code = :code
        ORDER BY date DESC LIMIT 40
    """)
    with engine.connect() as conn:
        df_hist = pd.read_sql(query, conn, params={"code": code})
        df_hist = df_hist.sort_values("日期")
        
    if len(df_hist) < 20:
        print(f"  Stock {name}({code}) hist len {len(df_hist)} < 20. Skip.")
        continue
        
    try:
        t0 = time.time()
        # 1. 注入实时价并计算指标
        df_labeled = calculate_indicators(df_hist, current_price=curr_price)
        t1 = time.time()
        
        # 2. 评估卖出信号
        signals = evaluate_exit_signals(df_labeled, entry_price, high_since_entry)
        t2 = time.time()
        
        print(f"  Stock {name}({code}): Indicators calculated in {t1-t0:.4f}s, Exit evaluation in {t2-t1:.4f}s. Signals: {len(signals) if signals else 0}")
    except Exception as e:
        print(f"  ❌ Stock {name}({code}) FAILED with error: {e}")

print("🎉 Indicator/Signal debug complete!")
