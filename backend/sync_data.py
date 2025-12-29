import akshare as ak
import pandas as pd
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.db import get_db_engine, init_db, save_to_db

from sqlalchemy import text

def sync_single_stock(code, name, start_time, engine):
    """同步单只股票的函数，支持增量同步 (v5.1)"""
    try:
        # 1. 查询本地数据库中该股的最后日期
        last_date = None
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT MAX(date) FROM daily_k WHERE code='{code}'"))
            last_date = result.fetchone()[0]
        
        # 2. 判断同步起点
        today = datetime.now()
        # A股交易时间：周一至周五，周末顺延到周五
        target_sync_date = today
        if today.weekday() == 5: # 周六
            target_sync_date = today - timedelta(days=1)
        elif today.weekday() == 6: # 周日
            target_sync_date = today - timedelta(days=2)
            
        fetch_start = start_time
        if last_date:
            # 如果已经同步到最新交易日（或之后），则跳过
            if last_date >= target_sync_date.date():
                # print(f"✅ [{code}] Already up-to-date ({last_date})")
                return True, "skipped"
            
            # 从最后日期的次日开始拉取
            fetch_start = (last_date + timedelta(days=1)).strftime("%Y%m%d")
        
        # 3. 执行拉取
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=fetch_start, adjust="qfq")
        if not df.empty:
            save_to_db(df, code, engine)
            return True, "downloaded"
        
        return True, "no_new_data" # 可能今天还没收盘或停牌
    except Exception as e:
        return False, f"{code}: {str(e)}"

def sync_all_market(mkt_cap_min=5000000000, max_workers=10):
    """
    使用并发加速同步全市场核心个股的历史数据
    """
    engine = get_db_engine()
    if not engine:
        print("❌ 数据库连接失败，请检查 db_config.json")
        return

    print("🚀 正在获取市场快照...")
    try:
        snapshot = ak.stock_zh_a_spot_em()
        candidates = snapshot[snapshot['总市值'] > mkt_cap_min]
        total = len(candidates)
        print(f"📦 发现 {total} 只符合条件（市值 > {mkt_cap_min/1e8}亿）的股票")
    except Exception as e:
        print(f"❌ 获取快照失败: {e}")
        return

    init_db(engine)
    
    success = 0
    fail = 0
    start_time = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
    
    print(f"⚡ 开始并发同步 (线程数: {max_workers})...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(sync_single_stock, row['代码'], row['名称'], start_time, engine): row['代码'] 
            for _, row in candidates.iterrows()
        }
        
        for i, future in enumerate(as_completed(futures)):
            is_ok, msg = future.result()
            if is_ok:
                success += 1
            else:
                fail += 1
                if ":" in str(msg): print(f"\n❌ {msg}")
            
            if (i + 1) % 10 == 0 or (i + 1) == total:
                print(f"⏳ 进度: {i+1}/{total} | 成功: {success} | 失败: {fail}", end="\r")
            
            # 微量延迟防止瞬间并发过高触发 WAF
            if i % max_workers == 0: time.sleep(0.5)

    print(f"\n✅ 同步完成！ 总计: {total}, 成功: {success}, 失败: {fail}")

if __name__ == "__main__":
    sync_all_market()
