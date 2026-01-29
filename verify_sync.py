import sys
import os
import json
import pandas as pd
from sqlalchemy import text
from datetime import datetime

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from core.db import get_db_engine

def check_sync():
    engine = get_db_engine()
    if not engine:
        print("❌ 无法连接到数据库")
        return

    print(f"🕒 当前系统时间: {datetime.now()}")
    print("-" * 40)

    try:
        with engine.connect() as conn:
            # 1. K线数据 (daily_k)
            k_res = conn.execute(text("SELECT MAX(date), COUNT(*) FROM daily_k")).fetchone()
            print(f"📈 K线数据 (daily_k):")
            print(f"   - 最新日期: {k_res[0]}")
            print(f"   - 总记录数: {k_res[1]}")

            # 2. 资金流数据 (money_flow_daily)
            mf_res = conn.execute(text("SELECT MAX(date), COUNT(*) FROM money_flow_daily")).fetchone()
            print(f"💰 资金流数据 (money_flow_daily):")
            print(f"   - 最新日期: {mf_res[0]}")
            print(f"   - 总记录数: {mf_res[1]}")

            # 3. 新闻数据 (news_raw)
            news_res = conn.execute(text("SELECT MAX(publish_time), COUNT(*) FROM news_raw")).fetchone()
            print(f"📰 新闻数据 (news_raw):")
            print(f"   - 最新发布时间: {news_res[0]}")
            print(f"   - 总记录数: {news_res[1]}")

            # 4. 扫描历史 (scan_history)
            scan_res = conn.execute(text("SELECT MAX(date), COUNT(*) FROM scan_history")).fetchone()
            print(f"🔍 扫描历史 (scan_history):")
            print(f"   - 最新扫描日期: {scan_res[0]}")
            print(f"   - 总记录数: {scan_res[1]}")

            # 5. 股票基础信息 (stock_basic)
            sb_res = conn.execute(text("SELECT COUNT(*) FROM stock_basic")).fetchone()
            print(f"📋 股票基础信息 (stock_basic):")
            print(f"   - 总记录数: {sb_res[0]}")

            # 6. 特色板块 (themes)
            themes_res = conn.execute(text("SELECT COUNT(*) FROM themes")).fetchone()
            print(f"🧩 特色板块 (themes):")
            print(f"   - 总记录数: {themes_res[0]}")

            print("-" * 40)
            
            # 检查是否有正在同步或最近报错的日志
            if os.path.exists("sync_error.log"):
                print("⚠️ 发现同步错误日志 (sync_error.log) 最后 5 行:")
                with open("sync_error.log", "r") as f:
                    lines = f.readlines()
                    for line in lines[-5:]:
                        print(f"   {line.strip()}")
            else:
                print("✅ 未发现同步错误日志")

    except Exception as e:
        print(f"❌ 查询出错: {e}")

if __name__ == "__main__":
    check_sync()
