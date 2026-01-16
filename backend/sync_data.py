import akshare as ak
import pandas as pd
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.db import get_db_engine, init_db, save_to_db, get_stock_basic_map
from core.money_flow import sync_stock_money_flow, ensure_money_flow_available

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


def sync_stock_with_money_flow(code, name, engine=None):
    """同步单只股票的 K 线 + 资金流数据"""
    # 1. 同步 K 线数据（现有逻辑）
    start_time = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
    success, msg = sync_single_stock(code, name, start_time, engine if engine else get_db_engine())

    if not success or msg == "skipped":
        # 如果 K 线同步失败或已跳过，根据情况决定是否继续
        if msg == "skipped":
            # K 线已是最新，继续同步资金流
            pass
        else:
            return False

    # 2. 同步资金流数据（新增）
    return sync_stock_money_flow(code, engine)


def sync_all_money_flow(workers=5, force_full=False):
    """批量同步所有股票的资金流数据

    Args:
        workers: 并发线程数
        force_full: 是否强制全量同步
    """
    stock_map = get_stock_basic_map()
    if not stock_map:
        print("❌ 无法获取股票列表")
        return

    stock_list = [{'code': k, 'name': v} for k, v in stock_map.items()}
    print(f"🔄 开始同步 {len(stock_list)} 只股票的资金流数据...")

    engine = get_db_engine()
    if not engine:
        print("❌ 数据库连接失败")
        return

    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_code = {
            executor.submit(sync_stock_money_flow, s['code'], engine): s['code']
            for s in stock_list
        }

        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                result = future.result(timeout=30)
                if result:
                    completed += 1
                    if completed % 10 == 0:
                        print(f"✅ 进度: {completed}/{len(stock_list)}")
                else:
                    failed += 1
                    print(f"⚠️ {code} 资金流同步失败")
            except Exception as e:
                failed += 1
                print(f"❌ {code} 资金流同步异常: {e}")

    print(f"\n📊 同步完成: 成功 {completed}, 失败 {failed}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="同步市场数据")
    parser.add_argument("--money-flow", action="store_true", help="同步资金流数据")
    parser.add_argument("--workers", type=int, default=5, help="并发线程数")
    args = parser.parse_args()

    if args.money_flow:
        sync_all_money_flow(workers=args.workers)
    else:
        sync_all_market()

