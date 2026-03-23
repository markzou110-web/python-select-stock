import sys
import os
import pandas as pd
from sqlalchemy import text

# Add backend to path
sys.path.insert(0, '/Users/liangzou/Desktop/AI_Tools/python-select-stock/backend')

from core.db import get_db_engine

def check_db():
    engine = get_db_engine()
    if not engine:
        print("Error: Could not get DB engine.")
        return

    try:
        with engine.connect() as conn:
            # 1. Check stock_basic count
            res = conn.execute(text("SELECT COUNT(*) FROM stock_basic")).fetchone()
            print(f"Total records in stock_basic: {res[0]}")

            if res[0] > 0:
                # 2. Check sample records
                res = conn.execute(text("SELECT * FROM stock_basic LIMIT 5")).fetchall()
                print("\nSample records in stock_basic:")
                for row in res:
                    print(row)

            # 3. Check for specific stock 000617
            res = conn.execute(text("SELECT * FROM stock_basic WHERE code = '000617'")).fetchone()
            print(f"\nRecord for 000617: {res}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_db()
