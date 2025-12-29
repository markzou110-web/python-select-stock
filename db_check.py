import sys
import os
import pandas as pd
from sqlalchemy import text

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from backend.core.db import get_db_engine

def check_db():
    engine = get_db_engine()
    if not engine:
        print("❌ 无法连接到数据库，请检查 backend/db_config.json")
        return

    try:
        with engine.connect() as conn:
            # 1. 总行数
            total_rows = conn.execute(text("SELECT COUNT(*) FROM daily_k")).scalar()
            
            # 2. 覆盖的股票数量
            total_stocks = conn.execute(text("SELECT COUNT(DISTINCT code) FROM daily_k")).scalar()
            
            # 3. 日期范围
            date_range = conn.execute(text("SELECT MIN(date), MAX(date) FROM daily_k")).fetchone()
            
            # 4. 平均每只股票的天数
            avg_days = total_rows / total_stocks if total_stocks > 0 else 0
            
            print(f"📊 数据库概览:")
            print(f"   - 总记录数: {total_rows}")
            print(f"   - 覆盖股票数: {total_stocks}")
            print(f"   - 日期范围: {date_range[0]} 至 {date_range[1]}")
            print(f"   - 平均每只股票天数: {avg_days:.1f}")
            
            if total_stocks > 0:
                print("\n📉 最近同步的 5 只股票示例:")
                sample = conn.execute(text("SELECT code, COUNT(*) as days, MAX(date) as last_date FROM daily_k GROUP BY code LIMIT 5")).fetchall()
                for row in sample:
                    print(f"   - {row[0]}: {row[1]} 天 (最后日期: {row[2]})")
                    
    except Exception as e:
        print(f"❌ 查询出错: {e}")

if __name__ == "__main__":
    check_db()
