
import sys
import os
import pandas as pd
from sqlalchemy import text

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from core.db import get_db_engine

def check_sync():
    engine = get_db_engine()
    if not engine:
        print("Error: Could not connect to database.")
        return

    print("Checking database sync status...")
    
    with engine.connect() as conn:
        # Check daily_k (Price data)
        res_k = conn.execute(text("SELECT MAX(date), COUNT(*) FROM daily_k")).fetchone()
        print(f"Daily K-line Table:")
        print(f"  Latest Date: {res_k[0]}")
        print(f"  Total Rows: {res_k[1]}")
        
        # Check for completeness of the latest date
        if res_k[0]:
            count_latest = conn.execute(text(f"SELECT COUNT(*) FROM daily_k WHERE date = '{res_k[0]}'")).fetchone()[0]
            print(f"  Stocks on {res_k[0]}: {count_latest}")

        # Check scan_history
        res_scan = conn.execute(text("SELECT MAX(date), COUNT(*) FROM scan_history")).fetchone()
        print(f"\nScan History Table:")
        print(f"  Latest Scan Date: {res_scan[0]}")
        print(f"  Total Rows: {res_scan[1]}")

        # Check money_flow_daily
        try:
            res_flow = conn.execute(text("SELECT MAX(date), COUNT(*) FROM money_flow_daily")).fetchone()
            print(f"\nMoney Flow Table:")
            print(f"  Latest Date: {res_flow[0]}")
            print(f"  Total Rows: {res_flow[1]}")
        except Exception:
            print("\nMoney Flow Table: Table might not exist or empty.")

if __name__ == "__main__":
    check_sync()
