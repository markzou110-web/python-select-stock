
import sys
import os
import pandas as pd
from sqlalchemy import text

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from core.db import get_db_engine
from core.money_flow import sync_stock_money_flow

def debug_sync():
    engine = get_db_engine()
    if not engine:
        print("Error: Could not connect to database.")
        return

    code = "600519"
    print(f"DEBUG: Syncing money flow for {code}...")
    
    # Check current status in DB
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(date) FROM money_flow_daily WHERE code = :code"), {"code": code}).fetchone()
        print(f"Current MAX date in DB for {code}: {res[0]}")

    success = sync_stock_money_flow(code, engine)
    print(f"Sync result: {success}")

    # Check status again
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(date) FROM money_flow_daily WHERE code = :code"), {"code": code}).fetchone()
        print(f"New MAX date in DB for {code}: {res[0]}")

if __name__ == "__main__":
    debug_sync()
