import sys
import os

# Append project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import get_db_engine, get_stock_basic_map
import pandas as pd
from sqlalchemy import text

engine = get_db_engine()
if not engine:
    print("Database engine not available.")
    exit(1)

try:
    with engine.connect() as conn:
        res = conn.execute(text("SELECT COUNT(*) FROM stock_basic")).fetchone()
        print("stock_basic count:", res[0])
        
        res_dk = conn.execute(text("SELECT COUNT(DISTINCT code) FROM daily_k")).fetchone()
        print("daily_k distinct code count:", res_dk[0])
        
        res_dk_total = conn.execute(text("SELECT COUNT(*) FROM daily_k")).fetchone()
        print("daily_k total records:", res_dk_total[0])
except Exception as e:
    print("Database query failed:", e)

basic_map = get_stock_basic_map()
print("get_stock_basic_map length:", len(basic_map))
if basic_map:
    print("Sample codes:", list(basic_map.keys())[:10])
