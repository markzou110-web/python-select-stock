import akshare as ak
import pandas as pd
import time
from datetime import datetime, timedelta
from core.db import get_db_engine, init_db, save_to_db

def sync_all_market(mkt_cap_min=5000000000):
    """
    同步全市场核心个股的历史数据到 PostgreSQL
    建议在非交易时间运行，或者每天运行一次
    """
    engine = get_db_engine()
    if not engine:
        print("❌ 数据库连接失败，请检查 db_config.json")
        return

    print("🚀 正在获取市场快照...")
    try:
        snapshot = ak.stock_zh_a_spot_em()
        # 初始过滤：市值 > 50亿
        candidates = snapshot[snapshot['总市值'] > mkt_cap_min]
        print(f"📦 发现 {len(candidates)} 只符合条件（市值 > {mkt_cap_min/1e8}亿）的股票")
    except Exception as e:
        print(f"❌ 获取快照失败: {e}")
        return

    init_db(engine)
    
    success = 0
    fail = 0
    start_time = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
    
    for i, (_, row) in enumerate(candidates.iterrows()):
        code = row['代码']
        name = row['名称']
        
        print(f"[{i+1}/{len(candidates)}] 正在同步 {code} ({name})...", end="\r")
        
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_time, adjust="qfq")
            if not df.empty:
                save_to_db(df, code, engine)
                success += 1
            else:
                fail += 1
            # 频率控制，防止被封
            time.sleep(0.2)
        except Exception as e:
            print(f"\n❌ {code} 同步失败: {e}")
            fail += 1
            time.sleep(2) # 遇到错误多歇会儿

    print(f"\n✅ 同步完成！ 成功: {success}, 失败: {fail}")

if __name__ == "__main__":
    sync_all_market()
