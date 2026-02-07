import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import text
import time

from sync_data import sync_single_stock
from core.money_flow import sync_stock_money_flow
from core.data import get_market_snapshot, get_sector_map
from core.db import get_db_engine, init_db
from core.state import sync_progress

def full_sync_task(row, engine, start_date):
    """
    Synchronizes both K-line and Money Flow for a single stock.
    """
    code = row['code']
    name = row['name']
    k_ok = True
    mf_ok = True
    
    # 1. Sync K-line
    if row.get('need_k', True):
        k_ok, status = sync_single_stock(code, name, start_date, engine)
    else:
        status = "skipped"
        
    # 2. Sync Money Flow
    if row.get('need_mf', True):
        mf_ok = sync_stock_money_flow(code, engine)
        
    return (k_ok and mf_ok), status

def background_sync_task():
    """
    Orchestrates the background synchronization for the entire market.
    """
    sync_progress["is_running"] = True
    sync_progress["start_time"] = datetime.now().isoformat()
    sync_progress["success"] = 0
    sync_progress["fail"] = 0
    sync_progress["current"] = 0
    sync_progress["status_text"] = "正在初始化板块映射..."

    try:
        # Step 1: Ensure sectors are persisted in DB
        get_sector_map()
        candidates = pd.DataFrame()
        try:
            snapshot = get_market_snapshot()
            if not snapshot.empty:
                candidates = snapshot[snapshot['mkt_cap'] > 2000000000]
        except:
            print("⚠️ Snapshot failed in sync task.")

        if candidates.empty:
            print("🔄 Snapshot unavailable, falling back to lightweight stock list...")
            try:
                from core.data import safe_ak_call
                df_codes = safe_ak_call("stock_info_a_code_name")
                candidates = df_codes.rename(columns={'code':'code', 'name':'name'})
            except:
                with get_db_engine().connect() as conn:
                    candidates = pd.read_sql("SELECT code, name FROM stock_basic", conn)

        if candidates.empty:
            print("❌ All methods to get stock list failed.")
            sync_progress["is_running"] = False
            return

        sync_progress["total"] = len(candidates)
        sync_progress["status_text"] = f"正在对比本地数据 (共 {len(candidates)} 只)..."
        engine = get_db_engine()
        init_db(engine)
        
        # --- Batch check existing data ---
        print("🔍 Checking existing data in batch...")
        with engine.connect() as conn:
            query = text("SELECT code, MAX(date) as last_date FROM daily_k GROUP BY code")
            df_existing = pd.read_sql(query, engine)
            existing_map = pd.Series(df_existing.last_date.values, index=df_existing.code).to_dict()
        
        # Calculate sync benchmark date
        today = datetime.now()
        target_sync_date = today
        if today.weekday() == 5: target_sync_date = today - timedelta(days=1)
        elif today.weekday() == 6: target_sync_date = today - timedelta(days=2)
        target_sync_date = target_sync_date.date()

        # Batch check money flow data
        print("🔍 Checking existing money flow data...")
        with engine.connect() as conn:
            query_mf = text("SELECT code, MAX(date) as last_date FROM money_flow_daily GROUP BY code")
            df_mf_existing = pd.read_sql(query_mf, engine)
            mf_existing_map = pd.Series(df_mf_existing.last_date.values, index=df_mf_existing.code).to_dict()

        # Pre-filter: which stocks need update?
        actual_tasks = []
        for _, row in candidates.iterrows():
            code = row['code']
            last_date_k = existing_map.get(code)
            last_date_mf = mf_existing_map.get(code)
            
            need_k = not last_date_k or last_date_k < target_sync_date
            need_mf = not last_date_mf or last_date_mf < target_sync_date
            
            if need_k or need_mf:
                row_copy = row.copy()
                row_copy['need_k'] = need_k
                row_copy['need_mf'] = need_mf
                actual_tasks.append(row_copy)
            else:
                sync_progress["success"] += 1
                sync_progress["current"] += 1
        
        print(f"⚡ {len(candidates) - len(actual_tasks)} stocks skipped. {len(actual_tasks)} to sync.")
        
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        max_workers = 15
        
        sync_progress["status_text"] = f"正在同步核心数据 (待处理: {len(actual_tasks)})..."
        
        if actual_tasks:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(full_sync_task, row, engine, start_date): row['code'] 
                    for row in actual_tasks
                }
                
                for future in as_completed(futures):
                    is_ok, status = future.result()
                    if is_ok:
                        sync_progress["success"] += 1
                    else:
                        sync_progress["fail"] += 1
                    sync_progress["current"] += 1
                    
                    if status == "downloaded" and sync_progress["current"] % 5 == 0:
                        time.sleep(0.5)
                
    except Exception as e:
        print(f"❌ Background sync error: {e}")
    finally:
        sync_progress["is_running"] = False
