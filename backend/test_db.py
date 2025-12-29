from core.db import save_scan_results, get_db_engine, init_db
import pandas as pd
from datetime import datetime

def test_save():
    init_db()
    engine = get_db_engine()
    test_results = [{
        "代码": "600519",
        "名称": "贵州茅台",
        "现价": 1700.0,
        "涨幅%": 1.2,
        "Score": 85.5,
        "RSI": 60.1,
        "DIF": 0.5,
        "BB": 0.05,
        "粘合度": 0.02,
        "行业": "白酒",
        "历史胜率": "70%",
        "信号次数": 5,
        "北向": "流入",
        "共振": "🔥 核心热点"
    }]
    print("Saving test results...")
    save_scan_results(test_results, engine)
    print("Done. Checking count...")
    with engine.connect() as conn:
        from sqlalchemy import text
        res = conn.execute(text("SELECT count(*) FROM scan_history")).scalar()
        print(f"Count: {res}")

if __name__ == "__main__":
    test_save()
