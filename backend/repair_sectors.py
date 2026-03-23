import sys
import os
import pandas as pd
from sqlalchemy import text

# Add backend to path
sys.path.insert(0, '/Users/liangzou/Desktop/AI_Tools/python-select-stock/backend')

# Disable proxies
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

from core.data import get_sector_map
from core.db import get_db_engine, save_stock_basic
from core.logging_config import logger

def repair_sectors():
    print("--- Starting Sector Repair ---")
    
    # 1. Manually trigger get_sector_map (which now fetches all boards and saves to DB)
    print("Building sector map from industry boards... This may take a minute.")
    sector_map = get_sector_map()
    
    if not sector_map:
        print("Error: Could not build sector map.")
        return

    print(f"Successfully mapped {len(sector_map)} stocks to industries.")
    
    # 2. Check the database again
    engine = get_db_engine()
    if engine:
        try:
            with engine.connect() as conn:
                res = conn.execute(text("SELECT COUNT(*) FROM stock_basic WHERE industry != '未知'")).fetchone()
                print(f"Stocks with valid industries now in DB: {res[0]}")
                
                # Sample
                res = conn.execute(text("SELECT * FROM stock_basic WHERE code = '000617'")).fetchone()
                print(f"New record for 000617: {res}")
        except Exception as e:
            print(f"Database check error: {e}")

if __name__ == "__main__":
    repair_sectors()
