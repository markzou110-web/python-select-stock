
import sys
import os
import pandas as pd
from datetime import datetime
from sqlalchemy import text

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from core.db import get_db_engine
from sync_data import sync_single_stock
from core.money_flow import sync_stock_money_flow

def test_full_sync_logic():
    engine = get_db_engine()
    if not engine:
        print("Error: Could not connect to database.")
        return

    code = "600519" # 贵州茅台
    name = "贵州茅台"
    start_date = "20260101"
    
    print(f"Testing full sync (K-line + Money Flow) for {code}...")
    
    # 1. Sync K-line
    k_ok, status = sync_single_stock(code, name, start_date, engine)
    print(f"  K-line Sync: {k_ok} ({status})")
    
    # 2. Sync Money Flow
    mf_ok = sync_stock_money_flow(code, engine)
    print(f"  Money Flow Sync: {mf_ok}")
    
    if k_ok and mf_ok:
        print("\nVerification successful! Both sync modules returned True.")
        
        # Check DB for recent records
        with engine.connect() as conn:
            max_k = conn.execute(text("SELECT MAX(date) FROM daily_k WHERE code = :code"), {"code": code}).fetchone()[0]
            max_mf = conn.execute(text("SELECT MAX(date) FROM money_flow_daily WHERE code = :code"), {"code": code}).fetchone()[0]
            print(f"  DB - latest K-line: {max_k}")
            print(f"  DB - latest Money Flow: {max_mf}")
    else:
        print("\nVerification failed. One or more sync modules failed.")

if __name__ == "__main__":
    test_full_sync_logic()
