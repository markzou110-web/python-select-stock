from core.db import get_db_engine
from sqlalchemy import text
import sys

def reset_daily_k():
    engine = get_db_engine()
    if not engine:
        print("Failed to get db engine.")
        sys.exit(1)
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM daily_k;"))
            conn.commit()
            print("Successfully cleared all data from daily_k table.")
            # Clear scan history too so it starts fresh
            conn.execute(text("DELETE FROM scan_history;"))
            conn.commit()
            print("Successfully cleared scan_history.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    reset_daily_k()
