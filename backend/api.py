from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from typing import List, Optional
from datetime import datetime, timedelta
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import random
import socket
import threading
import asyncio
import akshare as ak
import requests
import math

def sanitize_float(val):
    """Sanitizes float values to be JSON compliant (converts NaN/Inf to 0)."""
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return 0.0
    return val

def sanitize_recursive(data):
    """Recursively sanitizes a dictionary or list for JSON compliance."""
    if isinstance(data, dict):
        return {k: sanitize_recursive(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_recursive(v) for v in data]
    return sanitize_float(data)

BARK_KEY = "zVQJgaLZ4qApBq2d84NRTU" # 已自动提取您的 Key

socket.setdefaulttimeout(30) # 防止网络请求无限挂起

from pydantic import BaseModel

from core.db import get_db_engine, init_db, load_db_config, save_scan_results, get_scan_history_by_date, get_scan_dates, get_setting, save_setting, delete_scan_history_by_date
from core.data import get_market_snapshot, sync_stock, get_index_data, get_hot_sectors, get_sector_map, get_cached_data, set_cached_data, get_northbound_flow
from core.indicators import calculate_indicators, get_weekly_indicators
from core.strategy import check_strategy, calculate_historical_win_rate
from core.news import EastMoneyCrawler, NewsDeduplicator
from core.db_news import init_news_tables
from core.theme_tracker import ThemeTracker

# 全局状态跟踪
sync_progress = {
    "is_running": False,
    "total": 0,
    "current": 0,
    "success": 0,
    "fail": 0,
    "start_time": None,
    "status_text": "等待中..."
}

app = FastAPI(title="Alpha Vision API", version="5.1.0")

# CORS Setup for React Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:8000", # For self-referencing if needed
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import asyncio

# --- Constants & Types ---
class PaperTradeCreate(BaseModel):
    code: str
    name: str
    price: float

def fetch_mine_sweeper_data():
    """Gathers risk data: earnings, unlocks, and reductions."""
    today = datetime.now().strftime("%Y%m%d")
    data = {"earnings": [], "unlocks": [], "reductions": []}
    
    try:
        import akshare as ak
        # 1. Earnings (Next 7 days)
        df_earnings = ak.stock_report_disclosure_around_cn(symbol="利好利空")
        if df_earnings is not None and not df_earnings.empty:
            data["earnings"] = df_earnings[df_earnings['公告日期'] >= today]['股票代码'].tolist()
            
        # 2. Unlocks (Next 30 days)
        df_unlocks = ak.stock_restricted_release_queue_em()
        if df_unlocks is not None and not df_unlocks.empty:
            data["unlocks"] = df_unlocks['代码'].tolist()
            
        # 3. Reductions (Major shareholders)
        df_reduce = ak.stock_dzjy_mrtj_em()
        if df_reduce is not None and not df_reduce.empty:
            data["reductions"] = df_reduce['证券代码'].tolist()
            
    except Exception as e:
        print(f"Mine Sweeper Error: {e}")
        
    return data

def send_intraday_notification(stock_list):
    """Sends a push notification via Bark for the 14:30 Sentinel."""
    if not stock_list: return
    
    names = [s.get('名称', s.get('name')) for s in stock_list]
    codes = [s.get('代码', s.get('code')) for s in stock_list]
    
    title = "Alpha Vision 哨兵提醒"
    body = f"【14:30 尾盘确认】\n发现 {len(names)} 只标的走势稳健：\n" + "、".join([f"{n}({c})" for n, c in zip(names, codes)])
    
    print(f"\n🔔 NOTIFICATION: {body}\n")

    if BARK_KEY and "YOUR_BARK_KEY" not in BARK_KEY:
        try:
            # Bark API: https://api.day.app/{key}/{title}/{body}
            url = f"https://api.day.app/{BARK_KEY}/{title}/{body}?icon=https://i.imgur.com/8p4jA4w.png"
            requests.get(url, timeout=5)
            print("✅ Bark push sent successfully.")
        except Exception as e:
            print(f"❌ Bark push failed: {e}")
    else:
        print("⚠️ Bark Key not configured. Skipping push.")
        
    return body

class IntradaySentinel:
    def __init__(self):
        self.last_top_5 = []
        self.thread = None
        self._stop = False
        self.trigger_time = "14:30"

    def start(self):
        # Load time from DB
        self.trigger_time = get_setting("sentinel_time", "14:20")
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            
            # Dynamic trigger time check
            if current_time == self.trigger_time:
                print(f"Sentinel Triggered at {self.trigger_time}: Automated check...")
                try:
                    # Run a full scan (local_only=True for speed in sentinel)
                    results = run_market_scan(local_only=False)
                    if results:
                        self.last_top_5 = results[:5]
                        send_intraday_notification(self.last_top_5)
                except Exception as e:
                    error_msg = f"[{datetime.now()}] Sentinel Scan Error: {e}\n"
                    print(error_msg)
                    with open("sync_error.log", "a") as f:
                        f.write(error_msg)
                    
                    # Robustness: If limit reached, try with higher turnover
                    if "待扫描股票过多" in str(e):
                        print("🔄 Sentinel: Attempting recovery with stricter turnover filter...")
                        try:
                            results = run_market_scan(local_only=False, turnover_min=5.0)
                            if results:
                                self.last_top_5 = results[:5]
                                send_intraday_notification(self.last_top_5)
                        except Exception as e2:
                            print(f"❌ Sentinel: Recovery failed: {e2}")
                
                time.sleep(60) # Skip this minute
            
            # Periodically refresh settings (every 10 mins)
            if now.minute % 10 == 0 and now.second < 30:
                self.trigger_time = get_setting("sentinel_time", "14:30")
                
            time.sleep(30)

sentinel = IntradaySentinel()

@app.on_event("startup")
async def startup_event():
    print("🏗️ Initializing database...")
    init_db()

    print("📰 Initializing news tables...")
    init_news_tables()

    print("🎯 Initializing theme tracker...")
    global theme_tracker
    engine = get_db_engine()
    if engine:
        theme_tracker = ThemeTracker(engine)

    print("🚀 Starting Intraday Sentinel...")
    # Ensure default time is in DB
    if get_setting("sentinel_time") is None:
        save_setting("sentinel_time", "14:30")
    sentinel.start() # Start the sentinel thread

    # 异步预热核心缓存
    from core.data import get_index_data, get_hot_sectors
    loop = asyncio.get_event_loop()
    print("🔥 Pre-warming Index and Sector cache...")
    loop.run_in_executor(None, get_index_data)
    loop.run_in_executor(None, get_hot_sectors)
    # sector_map 极慢且涉及大量接口调用，延迟到首次扫描时生成，不在启动时抢占带宽

# Initialize news crawler and deduplicator
crawler = EastMoneyCrawler()
deduplicator = NewsDeduplicator()
theme_tracker = None  # Will be initialized after DB is ready

@app.get("/api/health")
def health_check():
    return {"status": "ok", "time": datetime.now().isoformat()}

@app.get("/api/news/stock/{code}")
def get_stock_news(code: str):
    """获取个股新闻

    Args:
        code: 股票代码
    """
    try:
        # 爬取新闻
        news_items = crawler.fetch_stock_news(code)

        # 去重
        unique_items = deduplicator.deduplicate_by_tfidf(news_items)

        # 转换为字典
        result = [
            {
                "title": item.title,
                "source": item.source,
                "url": item.url,
                "publish_time": item.publish_time.isoformat()
            }
            for item in unique_items
        ]

        return {"data": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/news/refresh/{code}")
def refresh_stock_news(code: str):
    """手动刷新个股新闻（按需抓取）"""
    return get_stock_news(code)

@app.get("/api/news/themes")
def get_themes(limit: int = 10):
    """获取热门题材列表"""
    try:
        if theme_tracker is None:
            return {"data": []}

        themes = theme_tracker.get_top_themes(limit=limit)
        return {"data": themes}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/market/indices")
def get_indices():
    """获取主要指数行情"""
    print("📡 Request: GET /api/market/indices")
    return get_index_data()

@app.get("/api/market/sectors")
def get_sectors():
    """获取热门行业板块"""
    print("📡 Request: GET /api/market/sectors")
    return get_hot_sectors()

# --- Sync Logic ---
def background_sync_task():
    global sync_progress
    from sync_data import sync_single_stock
    from core.data import get_market_snapshot, get_sector_map
    from core.db import get_db_engine, init_db
    
    sync_progress["is_running"] = True
    sync_progress["start_time"] = datetime.now().isoformat()
    sync_progress["success"] = 0
    sync_progress["fail"] = 0
    sync_progress["current"] = 0
    sync_progress["status_text"] = "正在初始化板块映射..."

    try:
        # Step 1: Ensure sectors are persisted in DB
        sync_progress["status_text"] = "正在初始化板块映射..."
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
                df_codes = ak.stock_info_a_code_name()
                candidates = df_codes.rename(columns={'code':'code', 'name':'name'})
            except:
                with get_db_engine().connect() as conn:
                    candidates = pd.read_sql("SELECT DISTINCT code, name FROM daily_k", conn)

        if candidates.empty:
            print("❌ All methods to get stock list failed.")
            sync_progress["is_running"] = False
            return

        sync_progress["total"] = len(candidates)
        sync_progress["status_text"] = f"正在对比本地数据 (共 {len(candidates)} 只)..."
        engine = get_db_engine()
        init_db(engine)
        
        # --- 核心优化：批量查询本地已同步日期 ---
        print("🔍 Checking existing data in batch...")
        with engine.connect() as conn:
            query = text("SELECT code, MAX(date) as last_date FROM daily_k GROUP BY code")
            df_existing = pd.read_sql(query, engine)
            # 建立映射: code -> last_date
            existing_map = pd.Series(df_existing.last_date.values, index=df_existing.code).to_dict()
        
        # 计算同步基准日期 (今日或最近一个交易日)
        today = datetime.now()
        target_sync_date = today
        if today.weekday() == 5: target_sync_date = today - timedelta(days=1)
        elif today.weekday() == 6: target_sync_date = today - timedelta(days=2)
        target_sync_date = target_sync_date.date()
        
        # 预过滤：将不需要下载的票直接标记为成功
        actual_tasks = []
        for _, row in candidates.iterrows():
            code = row['code']
            last_date = existing_map.get(code)
            if last_date and last_date >= target_sync_date:
                sync_progress["success"] += 1
                sync_progress["current"] += 1
            else:
                actual_tasks.append(row)
        
        print(f"⚡ {len(candidates) - len(actual_tasks)} stocks skipped (up-to-date). {len(actual_tasks)} to sync.")
        
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        max_workers = 15 # 稍微提升并发
        
        sync_progress["status_text"] = f"正在同步核心标的 (待处理: {len(actual_tasks)})..."
        if actual_tasks:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(sync_single_stock, row['code'], row['name'], start_date, engine): row['code'] 
                    for row in actual_tasks
                }
                
                for future in as_completed(futures):
                    is_ok, status = future.result()
                    if is_ok:
                        sync_progress["success"] += 1
                    else:
                        sync_progress["fail"] += 1
                    sync_progress["current"] += 1
                    # 动态延迟：如果是真正下载了且任务还很多，稍作休息；如果是跳过或报错，不停留
                    if status == "downloaded" and sync_progress["current"] % 5 == 0:
                        time.sleep(0.5)
                
    except Exception as e:
        print(f"❌ Background sync error: {e}")
    finally:
        sync_progress["is_running"] = False

@app.post("/api/sync/daily")
def start_sync(background_tasks: BackgroundTasks):
    if sync_progress["is_running"]:
        return {"status": "already_running", "progress": sync_progress}
    background_tasks.add_task(background_sync_task)
    return {"status": "started"}


@app.get("/api/sync/status")
def get_sync_status():
    return sync_progress

def run_market_scan(
    threshold: float = 0.12,
    vol_multiplier: float = 1.5,
    rsi_min: int = 55,
    use_macd_filter: bool = True,
    use_bb_sqz: bool = True,
    sqz_lookback: int = 10,
    use_weekly: bool = True,
    market_range: str = "全市场(除科创)",
    turnover_min: float = 3.0,
    mkt_cap_min: float = 0.0,
    use_rs_filter: bool = True,
    local_only: bool = True
):
    """Internal core scanning logic"""
    try:
        snapshot_df = pd.DataFrame()
        engine = get_db_engine()
        
        # 1. 如果不是强制本地，尝试联网获取快照
        if not local_only:
            try:
                snapshot_df = get_market_snapshot()
            except:
                print("⚠️ Network snapshot failed.")

        # 2. 如果数据为空（联网失败 或 强制本地），启用本地数据库兜底
        if snapshot_df.empty:
            print(f"🔄 Switching to LOCAL DB mode (Local Only: {local_only})...")
            try:
                with engine.connect() as conn:
                    print("📡 Querying DB for max_date...")
                    max_date_res = conn.execute(text("SELECT MAX(date) FROM daily_k")).fetchone()
                    if max_date_res and max_date_res[0]:
                        max_date = max_date_res[0]
                        print(f"📅 Found max_date in DB: {max_date}")
                        query = text(f"""
                            SELECT d.code, b.name, d.close as price, d.open, d.high, d.low, d.vol, 
                                   2.0 as pct_chg, 10.0 as turnover, 10000000000.0 as mkt_cap 
                            FROM daily_k d
                            LEFT JOIN stock_basic b ON d.code = b.code
                            WHERE d.date = '{max_date}'
                        """)
                        snapshot_df = pd.read_sql(query, engine)
                        print(f"📊 Loaded {len(snapshot_df)} rows from DB fallback.")
                        # Fallback for name if join failed
                        if not snapshot_df.empty:
                            snapshot_df['name'] = snapshot_df['name'].fillna(snapshot_df['code'])
                    else:
                        print("❌ No data found in daily_k table.")
            except Exception as e:
                print(f"❌ Local fallback error: {e}")
                pass
            
        if snapshot_df.empty:
             detail_msg = "无法获取市场数据。"
             if local_only:
                 detail_msg += "【离线模式】已开启，但本地数据库尚未同步今日数据。请先执行【数据管理 -> 同步当日数据】。"
             else:
                 detail_msg += "联网请求超时且本地无缓存数据，请检查网络或刷新后再试。"
             raise HTTPException(status_code=503, detail=detail_msg)
        
        # 初始过滤 (核心优化：只分析当日上涨且满足换手率/市值要求的股票)
        total_snapshot = len(snapshot_df)
        
        # SOP: 剔除 ST、北交所 (8, 4, 920开头)、退市整理
        snapshot_df['code_str'] = snapshot_df['code'].astype(str)
        snapshot_df['name_str'] = snapshot_df['name'].astype(str)
        
        is_not_st = ~snapshot_df['name_str'].str.contains('ST|退', case=False)
        is_not_bj = ~snapshot_df['code_str'].str.startswith(('8', '4', '920'))
        
        candidates = snapshot_df[
            (snapshot_df['pct_chg'] > 0) & 
            is_not_st & is_not_bj &
            (snapshot_df['mkt_cap'] >= mkt_cap_min * 100000000) & # UI 传过来的是“亿”为单位
            (snapshot_df['turnover'] >= turnover_min)
        ].copy()
        
        print(f"📊 Snapshot: {total_snapshot} stocks")
        # 预存快照数据以便后续提取 PE 和 换手率
        snapshot_lookup = {row['code']: row for _, row in snapshot_df.iterrows()}
        print(f"🔍 After SOP Filter (No ST/BJ/Delist, +%, TO>{turnover_min}%, MC>{mkt_cap_min}亿): {len(candidates)} candidates")
        
        # 1. 处理科创板过滤
        if "包含科创板" not in market_range:
            candidates = candidates[~candidates['code'].astype(str).str.startswith('688')]
            
        # 2. 处理成分股精确过滤
        index_map = {
            "沪深300": "000300",
            "上证50": "000016",
            "中证500": "000905",
            "中证1000": "000852"
        }
        
        target_index = None
        for key, val in index_map.items():
            if key in market_range:
                target_index = val
                break
                
        if target_index:
            try:
                import akshare as ak
                cons_df = ak.index_stock_cons(symbol=target_index)
                if not cons_df.empty:
                    cons_codes = cons_df['品种代码'].tolist()
                    candidates = candidates[candidates['code'].isin(cons_codes)]
            except Exception as e:
                print(f"⚠️ {market_range} filter failed: {e}")

        # 3. 安全检查：如果待扫描数量依然过多，提示用户缩小范围
        max_allowed = 5000 if local_only else 2000
        if len(candidates) > max_allowed:
            mode_desc = "本地" if local_only else "在线"
            raise HTTPException(
                status_code=400, 
                detail=f"{mode_desc}模式待扫描股票过多 ({len(candidates)}只/上限{max_allowed}), 请缩小市场范围或调高筛选条件。"
            )
            
        results = []
        engine = get_db_engine()
        
        # 核心优化：预热指数历史缓存，避免并发时重复拉取
        from core.data import get_index_hist
        get_index_hist("000001")
        
        print(f"🚀 Starting scan for {len(candidates)} candidates...")
        start_time = time.time()
        
        # 并发扫描逻辑
        workers = 15 if local_only else 8
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_stock = {
                executor.submit(
                    single_stock_task, 
                row['code'], row['name'], row['price'], row['vol'], row['open'],
                threshold, vol_multiplier, rsi_min, use_macd_filter, use_bb_sqz, sqz_lookback, use_weekly, use_rs_filter,
                pe=row.get('pe', 0), turnover=row.get('turnover', 0),
                local_only=local_only, engine=engine
            ): row for _, row in candidates.iterrows()
            }
            
            fail_reasons = {}
            for future in as_completed(future_to_stock):
                try:
                    # 单个股票分析超时设为 30s，防止某一个接口挂起卡死全场
                    res = future.result(timeout=30)
                    if isinstance(res, dict) and 'Score' in res:
                        results.append(res)
                    elif isinstance(res, dict):
                        reason = res.get('reason', '未知原因')
                        fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
                except Exception as e:
                    continue
            
            if fail_reasons:
                print(f"📉 Rejection Summary: {fail_reasons}")
        
        print(f"✅ Scan completed in {time.time() - start_time:.2f}s. Found {len(results)} matches.")
        
        # 排序并取 Top 30
        results = sorted(results, key=lambda x: x['Score'], reverse=True)[:30]
        
        # 补充增强数据 (行业, 胜率)
        from core.data import get_sector_map
        sector_map = get_sector_map() # This now handles DB + Memory cache
        
        def fetch_single_industry(res_item):
            code = res_item['代码']
            industry = sector_map.get(code, "未知")
            if industry == "未知":
                try:
                    import akshare as ak
                    info_df = ak.stock_individual_info_em(symbol=code)
                    if not info_df.empty:
                        industry_val = info_df[info_df['item'] == '行业分类']['value'].values
                        if len(industry_val) > 0:
                            return code, industry_val[0]
                except:
                    pass
            return code, industry

        # 并发补充结果详情，避免 30 个股票串行查询导致的超时
        print(f"🏷️ Supplementing industry info for {len(results)} results in parallel...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_industry = {executor.submit(fetch_single_industry, res): res for res in results}
            industry_results = {}
            for future in as_completed(future_to_industry):
                try:
                    code, ind = future.result(timeout=10)
                    industry_results[code] = ind
                except:
                    continue
        
        for res in results:
            res['行业'] = industry_results.get(res['代码'], "未知")

        # --- 北向资金数据补充 (Northbound Money Flow) ---
        print(f"💰 Fetching northbound flow data for {len(results)} results...")
        northbound_map = {}

        def fetch_northbound_for_stock(res_item):
            code = res_item['代码']
            try:
                nb_data = get_northbound_flow(code=code, days=3)
                return code, nb_data
            except Exception as e:
                return code, {'net_flow': 0, 'trend': '---', 'recent_data': []}

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_nb = {executor.submit(fetch_northbound_for_stock, res): res for res in results}
            for future in as_completed(future_to_nb):
                try:
                    code, nb_data = future.result(timeout=5)
                    northbound_map[code] = nb_data
                except:
                    continue

        for res in results:
            code = res['代码']
            nb_data = northbound_map.get(code, {'net_flow': 0, 'trend': '---', 'recent_data': []})
            res['北向'] = f"{nb_data['trend']}" if nb_data['trend'] != '---' else '---'
            res['北向净流入'] = nb_data['net_flow']
            
        # --- SOP: 板块共振 (Sector Resonance) 计算 ---
        industry_counts = {}
        for res in results:
            ind = res.get('行业', '未知')
            industry_counts[ind] = industry_counts.get(ind, 0) + 1
            
        for res in results:
            ind = res.get('行业', '未知')
            if industry_counts.get(ind, 0) > 1 and ind != '未知':
                res['共振'] = "🔥 核心热点"
            else:
                res['共振'] = "独苗"
            
        # --- SOP: 地雷监测 (Mine Sweeper) ---
        mine_data = fetch_mine_sweeper_data()
        for res in results:
            code = res['代码']
            warnings = []
            if code in mine_data["earnings"]: warnings.append("📅 财报")
            if code in mine_data["unlocks"]: warnings.append("🔒 解禁")
            if code in mine_data["reductions"]: warnings.append("⚠️ 减持")
            res['warnings'] = warnings

        # Update Sentinel memory
        sentinel.last_top_5 = results[:5]

        # --- 持久化保存 ---
        # 扫描前清空今日旧数据，防止 Bug 修复前的“幽灵记录”残留在列表里
        current_date_str = datetime.now().strftime("%Y-%m-%d")
        delete_scan_history_by_date(current_date_str, engine)
        save_scan_results(results, engine)
        
        # --- JSON Compliance Sanitization ---
        return sanitize_recursive(results)
    except HTTPException as he:
        # 允许 HTTPException 直接通过，不再包装成 500
        raise he
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def single_stock_task(code, name, price, vol, open_price, threshold, vol_multiplier, rsi_min, use_macd_filter, use_bb_sqz, sqz_lookback, use_weekly, use_rs_filter=True, pe=0, turnover=0, local_only=False, engine=None):
    from core.db import load_from_db
    import akshare as ak
    
    target_date = datetime.now()
    start_date = (target_date - timedelta(days=250)).strftime("%Y%m%d")
    end_date_str = target_date.strftime("%Y-%m-%d")
    
    df = load_from_db(code, (target_date - timedelta(days=360)).strftime("%Y-%m-%d"), engine)
    
    # 逻辑调整：如果是 local_only，且数据库为空，则直接跳过
    if df.empty and local_only:
        return {"reason": "本地数据缺失 (Local-Only 模式已开启)"}

    if df.empty or df.iloc[-1]['日期'] < (target_date - timedelta(days=3)).strftime("%Y-%m-%d"):
        if local_only:
            # 即使数据旧，也尝试用现有的，如果没有则跳过
            if df.empty: return {"reason": "数据库无此代码数据"}
            print(f"⚠️ [{code}] Using stale local data (Local-Only)")
        else:
            try:
                print(f"📉 [{code}] Fetching fresh data...")
                df_new = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, adjust="qfq")
                if isinstance(df_new, pd.DataFrame) and not df_new.empty:
                    df = df_new
                    from core.db import save_to_db
                    save_to_db(df, code, engine) 
                    time.sleep(random.uniform(0.1, 0.3))
            except Exception as e:
                print(f"❌ [{code}] Hist fetch error: {e}")
                return {"reason": f"接口请求失败: {str(e)}"}
            
    if df.empty or len(df) < 120: 
        print(f"⚠️ [{code}] Insufficient data ({len(df)})")
        return None
    
    try:
        df = calculate_indicators(df, current_price=price, current_vol=vol, current_open=open_price)
        match, stats = check_strategy(
            df, 
            threshold=threshold, 
            vol_multiplier=vol_multiplier, 
            rsi_min=rsi_min, 
            use_macd_filter=use_macd_filter, 
            use_bb_sqz=use_bb_sqz, 
            sqz_lookback=sqz_lookback, 
            use_rs_filter=use_rs_filter
        )
        
        if match:
            print(f"✨ [{code}] Resonance Match!")
            if use_weekly:
                if not get_weekly_indicators(code, df=df, local_only=local_only): 
                    print(f"⏩ [{code}] Weekly trend failed")
                    return {"reason": "周线趋势未走好"}
            
            # 增加胜率和其他指标
            wr, sig_count = calculate_historical_win_rate(df)
            stats['历史胜率'] = f"{wr}%"
            stats['信号次数'] = sig_count
            stats['代码'] = code
            stats['名称'] = name
            stats['PE'] = sanitize_float(pe)
            stats['换手率'] = sanitize_float(turnover)
            
            # 计算量比 (今日成交量 / 前5日平均成交量)
            if len(df) >= 6:
                avg_vol_5 = df.iloc[-6:-1]['成交量'].mean()
                stats['量比'] = sanitize_float(round(vol / avg_vol_5, 2)) if avg_vol_5 > 0 else 0
            else:
                stats['量比'] = 0
                
            # 最后兜底：清理 stats 中可能存在的 NaN/Inf
            for k, v in stats.items():
                stats[k] = sanitize_float(v)
                
            return stats
        else:
            return stats # 返回包含失败原因的字典
    except Exception as e:
        print(f"❌ [{code}] Analysis error: {e}")
        return {"reason": f"分析异常: {str(e)}"}
    
    return {"reason": "未知错误"}

@app.get("/api/stock/{code}/kline")
async def get_stock_kline(code: str, local_only: bool = False):
    """获取个股 K 线数据供前端绘图"""
    from core.db import load_from_db
    import akshare as ak
    from datetime import datetime, timedelta
    
    engine = get_db_engine()
    target_date = datetime.now()
    # 获取近 300 天的数据，确保有足够的交易日来画出 100-200 根 K 线
    start_date_str = (target_date - timedelta(days=300)).strftime("%Y-%m-%d")
    
    df = load_from_db(code, start_date_str, engine)
    
    if df.empty and not local_only:
        try:
            print(f"📉 API: Fetching K-line for {code}...")
            start_fetch = (target_date - timedelta(days=300)).strftime("%Y%m%d")
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_fetch, adjust="qfq")
            if not df.empty:
                from core.db import save_to_db
                save_to_db(df, code, engine)
        except Exception as e:
            print(f"❌ K-line fetch error for {code}: {e}")
            
    if df.empty:
        return {"code": code, "data": []}
        
    # 计算绘图所需的指标
    from core.indicators import calculate_indicators
    # 扩展 periods 包含 120 和 250
    df = calculate_indicators(df, periods=[20, 120, 250])
    
    # 统一列名映射
    mapping = {
        '日期': 'time', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'value'
    }
    ak_mapping = {
        '日期': 'time', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'
    }
    
    col_map = mapping if '收盘' in df.columns else ak_mapping
    plot_df = df.rename(columns=col_map)
    plot_df['time'] = plot_df['time'].astype(str)
    
    # 只保留绘图需要的列并取最近 220 条 (多留一点为了指标计算的完整性)
    cols = ['time', 'open', 'high', 'low', 'close', 'value' if 'value' in plot_df.columns else 'volume', 'EMA20', 'EMA120', 'EMA250']
    records = plot_df[cols].tail(200).to_dict('records')
    
    return {
        "code": code,
        "name": df.iloc[0]['name'] if 'name' in df.columns else "未知",
        "data": records
    }

def fetch_stock_data_with_indicators(code: str):
    from core.db import load_from_db, save_to_db, get_db_engine
    from core.indicators import calculate_indicators
    import akshare as ak
    
    engine = get_db_engine()
    target_date = datetime.now()
    # Load last 1 year
    start_db = (target_date - timedelta(days=365)).strftime("%Y-%m-%d")
    df = load_from_db(code, start_db, engine)
    
    # Check if stale (older than 2 days)
    is_stale = True
    if not df.empty and '日期' in df.columns:
        last_date_str = str(df.iloc[-1]['日期'])
        try:
             last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
             if (target_date - last_date).days <= 2: 
                 is_stale = False
        except: pass
            
    if is_stale or df.empty:
        try:
             start_date = (target_date - timedelta(days=365)).strftime("%Y%m%d")
             df_new = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, adjust="qfq")
             if not df_new.empty:
                 df = df_new
                 save_to_db(df, code, engine)
        except Exception as e:
            print(f"Fetch error for {code}: {e}")
            
    if df.empty: return df
    
    # Calculate indicators
    df = calculate_indicators(df, periods=[5, 10, 20, 60])
    return df

@app.get("/api/stock/detail")
def get_stock_detail(code: str):
    """获取单只股票详情 (k线指标 + 资金面) 用于 AI Deep Dive"""
    try:
        # 1. 获取 K 线数据并计算指标
        df = fetch_stock_data_with_indicators(code)
        if df.empty:
            raise HTTPException(status_code=404, detail="未找到该股票的历史数据")
        
        # 2. 准备 K 线绘图数据 (只取最近 60 天给 Mini Chart)
        plot_df = df.tail(60).copy()
        plot_df['time'] = plot_df['日期'].astype(str)
        
        # 映射字段名给前端 lightweight-charts
        records = []
        for _, row in plot_df.iterrows():
            records.append({
                "time": row['time'],
                "open": float(row['开盘']),
                "high": float(row['最高']),
                "low": float(row['最低']),
                "close": float(row['收盘']),
                "value": float(row['成交量']),
                "EMA5": float(row.get('EMA5', 0)),
                "EMA20": float(row.get('EMA20', 0)),
                "EMA60": float(row.get('EMA60', 0)),
                "RSI": float(row.get('RSI', 0)),
                "MACD": float(row.get('MACD_HIST', 0))
            })
            
        return {
            "code": code,
            "data": records,
            "indicators": {
                "rsi": float(df.iloc[-1].get('RSI', 0)),
                "dif": float(df.iloc[-1].get('MACD_DIF', 0)),
                "dea": float(df.iloc[-1].get('MACD_DEA', 0)),
                "hist": float(df.iloc[-1].get('MACD_HIST', 0)),
                "ema5": float(df.iloc[-1].get('EMA5', 0)),
                "ema20": float(df.iloc[-1].get('EMA20', 0)),
                "ema60": float(df.iloc[-1].get('EMA60', 0))
            }
        }
    except Exception as e:
        print(f"Error fetching stock detail for {code}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/scan/history")
async def get_history_results(date: str):
    """获取指定日期的历史选股结果"""
    return get_scan_history_by_date(date)

@app.get("/api/scan/dates")
async def get_history_dates():
    """获取历史扫描日期列表"""
    return get_scan_dates()

@app.get("/api/scan")
def scan_market(
    threshold: float = 0.12,
    vol_multiplier: float = 1.5,
    rsi_min: int = 55,
    use_macd_filter: bool = True,
    use_bb_sqz: bool = True,
    sqz_lookback: int = 10,
    use_weekly: bool = True,
    market_range: str = "全市场(除科创)",
    turnover_min: float = 3.0,
    mkt_cap_min: float = 0.0,
    use_rs_filter: bool = True,
    local_only: bool = True
):
    """API Endpoint for market scan"""
    return run_market_scan(
        threshold, vol_multiplier, rsi_min, use_macd_filter, 
        use_bb_sqz, sqz_lookback, use_weekly, market_range, 
        turnover_min, mkt_cap_min, use_rs_filter, local_only
    )

@app.get("/api/settings")
def get_settings_api():
    return {
        "sentinel_time": get_setting("sentinel_time", "14:30"),
        "bark_key": BARK_KEY
    }

@app.post("/api/settings")
def save_settings_api(data: dict):
    if "sentinel_time" in data:
        save_setting("sentinel_time", data["sentinel_time"])
        sentinel.trigger_time = data["sentinel_time"] # Update live
    return {"status": "success"}

@app.post("/api/paper/add")
def add_paper_trade(trade: PaperTradeCreate):
    engine = get_db_engine()
    if not engine: return {"status": "error"}
    try:
        with engine.connect() as conn:
            conn.execute(text('''
                INSERT INTO paper_trading (code, name, entry_price, entry_date, current_price, status)
                VALUES (:code, :name, :price, :date, :price, 'OPEN')
                ON CONFLICT (code, entry_date) DO NOTHING
            '''), {
                "code": trade.code,
                "name": trade.name,
                "price": trade.price,
                "date": datetime.now().strftime("%Y-%m-%d")
            })
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        print(f"Error adding paper trade: {e}")
        return {"status": "error", "detail": str(e)}

@app.get("/api/paper/list")
def list_paper_trades():
    engine = get_db_engine()
    if not engine: return []
    try:
        df = pd.read_sql("SELECT * FROM paper_trading ORDER BY entry_date DESC", engine)
        return df.to_dict('records')
    except:
        return []

@app.delete("/api/paper/remove/{id}")
def remove_paper_trade(id: int):
    engine = get_db_engine()
    if not engine: return {"status": "error"}
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM paper_trading WHERE id = :id"), {"id": id})
            conn.commit()
        return {"status": "success"}
    except:
        return {"status": "error"}

@app.get("/api/scan/export")
def export_scan_results(
    date: str = Query(None, description="导出日期 (YYYY-MM-DD), 不指定则导出最近一次"),
    format: str = Query("csv", description="导出格式: csv 或 excel")
):
    """导出扫描结果为 CSV 或 Excel 文件

    Returns:
        FileResponse: 下载文件
    """
    from fastapi.responses import FileResponse
    from pathlib import Path

    engine = get_db_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="数据库连接失败")

    try:
        # 确定导出日期
        if not date:
            dates = get_scan_dates(engine)
            if not dates:
                raise HTTPException(status_code=404, detail="暂无扫描结果")
            date = dates[0]

        # 获取扫描结果
        results = get_scan_history_by_date(date, engine)
        if not results:
            raise HTTPException(status_code=404, detail=f"未找到 {date} 的扫描结果")

        # 转换为 DataFrame
        df = pd.DataFrame(results)

        # 选择并重命名关键列
        column_mapping = {
            '代码': '代码',
            '名称': '名称',
            '现价': '现价',
            '涨幅%': '涨跌幅(%)',
            '量比': '量比',
            '换手率': '换手率(%)',
            'PE': '市盈率',
            'RSI': 'RSI',
            'DIF': 'MACD_DIF',
            'BB': '布林带宽度',
            '粘合度': '均线粘合度',
            'Score': '评分',
            '行业': '行业',
            '历史胜率': '历史胜率(%)',
            '信号次数': '信号次数',
            '北向': '北向资金流向',
            '北向净流入': '北向净流入(亿)',
            '共振': '板块共振'
        }

        # 只保留存在的列
        export_columns = [col for col in column_mapping.keys() if col in df.columns]
        df_export = df[export_columns].copy()

        # 重命名为中文列名
        df_export.columns = [column_mapping[col] for col in export_columns]

        # 格式化数值列
        numeric_columns = ['现价', '涨跌幅(%)', '量比', '换手率(%)', '市盈率', 'RSI', 'MACD_DIF',
                          '布林带宽度', '均线粘合度', '评分', '北向净流入(亿)']
        for col in numeric_columns:
            if col in df_export.columns:
                df_export[col] = pd.to_numeric(df_export[col], errors='coerce').round(2)

        # 生成文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir = Path("/tmp")

        if format.lower() == "excel":
            filename = f"scan_results_{date}_{timestamp}.xlsx"
            filepath = temp_dir / filename
            df_export.to_excel(filepath, index=False, engine='openpyxl')
        else:  # csv
            filename = f"scan_results_{date}_{timestamp}.csv"
            filepath = temp_dir / filename
            df_export.to_csv(filepath, index=False, encoding='utf-8-sig')

        return FileResponse(
            path=str(filepath),
            filename=filename,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if format.lower() == "excel" else 'text/csv'
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")

@app.get("/api/test/push")
def test_push_notification():
    """测试 Bark 推送功能"""
    mock_data = [
        {"code": "600519", "name": "测试茅台", "price": 1800.0},
        {"code": "300750", "name": "测试时代", "price": 450.0}
    ]
    try:
        msg = send_intraday_notification(mock_data)
        return {"status": "success", "message": f"Push sent: {msg}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
