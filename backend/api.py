from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from typing import List, Optional, Dict, Any
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

from pydantic import BaseModel

# Import config and logging first
from core.config import config
from core.logging_config import logger

from core.db import (
    get_db_engine, init_db, load_db_config, save_scan_results,
    get_scan_history_by_date, get_scan_dates, get_available_dates,
    get_setting, save_setting, validate_stock_code
)
from core.data import get_market_snapshot, sync_stock, get_index_data, get_hot_sectors, get_sector_map, get_cached_data, set_cached_data
from core.indicators import calculate_indicators, get_weekly_indicators
from core.strategy import check_strategy, calculate_historical_win_rate

# Bark Key from config (not hardcoded)
BARK_KEY = config.BARK_KEY

socket.setdefaulttimeout(config.AKSHARE_TIMEOUT)  # 防止网络请求无限挂起

# 全局状态跟踪 - 添加线程锁保护
sync_progress_lock = threading.Lock()
sync_progress = {
    "is_running": False,
    "total": 0,
    "current": 0,
    "success": 0,
    "fail": 0,
    "start_time": None,
    "status_text": "等待中..."
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 禁用代理以避免网络连接错误
    config.setup_no_proxy()
    logger.info("Proxy disabled for network requests")

    logger.info("Initializing database...")
    init_db()

    logger.info("Starting Intraday Sentinel...")
    sentinel.start()  # Start the sentinel thread

    # 异步预热核心缓存
    from core.data import get_index_data, get_hot_sectors
    loop = asyncio.get_running_loop()
    logger.info("Pre-warming Index and Sector cache...")
    loop.run_in_executor(None, get_index_data)
    loop.run_in_executor(None, get_hot_sectors)
    # 异步预热地雷数据
    loop.run_in_executor(None, fetch_mine_sweeper_data)
    
    yield

app = FastAPI(title="Alpha Vision API", version="5.1.0", lifespan=lifespan)

# CORS Setup for React Frontend - using config
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],  # 限制HTTP方法
    allow_headers=["Content-Type", "Authorization"],  # 限制请求头
)

import asyncio

# --- Constants & Types ---
class PaperTradeCreate(BaseModel):
    code: str
    name: str
    price: float

# 地雷数据缓存 (30分钟有效)
_mine_sweeper_cache = {"data": None, "timestamp": 0}

def fetch_mine_sweeper_data() -> Dict[str, List[str]]:
    """Gathers risk data: earnings, unlocks, and reductions. (带30分钟缓存)"""
    global _mine_sweeper_cache

    # 检查缓存 (30分钟有效期)
    if _mine_sweeper_cache["data"] is not None:
        cache_age = time.time() - _mine_sweeper_cache["timestamp"]
        if cache_age < 1800:  # 30分钟
            logger.debug(f"Using cached mine sweeper data (age: {int(cache_age)}s)")
            return _mine_sweeper_cache["data"]

    today = datetime.now()
    today_str = today.strftime("%Y%m%d")
    data = {"earnings": [], "unlocks": [], "reductions": []}

    def fetch_with_timeout(func, timeout=10, default=None):
        """带超时的API调用"""
        import concurrent.futures
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func)
                return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning(f"API timeout after {timeout}s: {func.__name__ if hasattr(func, '__name__') else 'unknown'}")
            return default
        except Exception as e:
            logger.debug(f"API error: {str(e)[:50]}")
            return default

    try:
        # 1. Earnings (Next 7 days) - using report disclosure schedule
        current_year = today.year
        report_period = f"{current_year - 1}年报"

        df_earnings = fetch_with_timeout(
            lambda: ak.stock_report_disclosure(market="沪深京", period=report_period),
            timeout=10
        )
        if df_earnings is not None and not df_earnings.empty:
            df_earnings['首次预约'] = df_earnings['首次预约'].astype(str).str.replace('-', '')
            data["earnings"] = df_earnings[df_earnings['首次预约'] >= today_str]['股票代码'].tolist()[:500]  # 限制数量

        # 2. Unlocks (Next 30 days) - using detailed release schedule
        end_date = (today + timedelta(days=30)).strftime("%Y%m%d")

        df_unlocks = fetch_with_timeout(
            lambda: ak.stock_restricted_release_detail_em(start_date=today_str, end_date=end_date),
            timeout=10
        )
        if df_unlocks is not None and not df_unlocks.empty:
            data["unlocks"] = df_unlocks['股票代码'].tolist()[:500]  # 限制数量

        # 3. Reductions (Block trades - 大宗交易)
        df_reduce = fetch_with_timeout(
            lambda: ak.stock_dzjy_mrtj(),
            timeout=10
        )
        if df_reduce is not None and not df_reduce.empty:
            data["reductions"] = df_reduce['证券代码'].tolist()[:500]  # 限制数量

    except Exception as e:
        logger.warning(f"Mine Sweeper Error: {e}")

    # 更新缓存
    _mine_sweeper_cache["data"] = data
    _mine_sweeper_cache["timestamp"] = time.time()

    return data

def send_intraday_notification(stock_list: List[Dict[str, Any]]) -> Optional[str]:
    """
    Sends a push notification via Bark for the 14:30 Sentinel.

    Args:
        stock_list: List of stock dictionaries with '名称'/'name' and '代码'/'code' keys

    Returns:
        Message body if sent, None otherwise
    """
    if not stock_list:
        return None

    names = [s.get('名称', s.get('name', '')) for s in stock_list]
    codes = [s.get('代码', s.get('code', '')) for s in stock_list]

    title = "Alpha Vision 哨兵提醒"
    body = f"【14:30 尾盘确认】\n发现 {len(names)} 只标的走势稳健：\n" + "、".join([f"{n}({c})" for n, c in zip(names, codes)])

    logger.info(f"Notification: {body}")

    if config.is_bark_configured():
        try:
            url = config.BARK_URL_TEMPLATE.format(key=BARK_KEY, title=title, body=body)
            requests.get(url, timeout=5)
            logger.info("Bark push sent successfully.")
        except Exception as e:
            logger.error(f"Bark push failed: {e}")
    else:
        logger.debug("Bark Key not configured. Skipping push.")

    return body

class IntradaySentinel:
    def __init__(self):
        self.last_top_5 = []
        self.thread = None
        self._stop = False
        self.trigger_time = "14:20"

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
                logger.info(f"Sentinel Triggered at {self.trigger_time}: Automated check...")
                try:
                    # Run a full scan (local_only=True for speed in sentinel)
                    results = run_market_scan(local_only=True)
                    if results:
                        self.last_top_5 = results[:5]
                        send_intraday_notification(self.last_top_5)
                except Exception as e:
                    logger.error(f"Sentinel Scan Error: {e}")
                
                time.sleep(60) # Skip this minute
            
            # Periodically refresh settings (every 10 mins)
            if now.minute % 10 == 0 and now.second < 30:
                self.trigger_time = get_setting("sentinel_time", "14:20")
                
            time.sleep(30)

sentinel = IntradaySentinel()


@app.get("/api/health")
def health_check():
    return {"status": "ok", "time": datetime.now().isoformat()}

@app.get("/api/market/indices")
def get_indices():
    """获取主要指数行情"""
    logger.debug("Request: GET /api/market/indices")
    return get_index_data()


@app.get("/api/market/sectors")
def get_sectors():
    """获取热门行业板块"""
    logger.debug("Request: GET /api/market/sectors")
    return get_hot_sectors()

# --- Sync Logic ---
def background_sync_task():
    """Background task to sync stock data."""
    global sync_progress

    with sync_progress_lock:
        sync_progress["is_running"] = True
        sync_progress["start_time"] = datetime.now().isoformat()
        sync_progress["success"] = 0
        sync_progress["fail"] = 0
        sync_progress["current"] = 0
        sync_progress["status_text"] = "正在初始化板块映射..."

    try:
        # 使用多数据源同步系统
        from core.multi_source_sync import MultiSourceSync
        from core.db import get_db_engine, init_db
        from sqlalchemy import text

        engine = get_db_engine()
        if not engine:
            logger.error("Database connection failed")
            return

        init_db(engine)

        with sync_progress_lock:
            sync_progress["status_text"] = "正在初始化多数据源同步..."

        # 初始化多数据源同步器
        syncer = MultiSourceSync()

        # 打印数据源状态
        logger.info("Checking data source status...")
        report = syncer.manager.get_status_report()
        available_count = sum(1 for info in report.values() if info['status'] == 'available')
        logger.info(f"Available data sources: {available_count}/{len(report)}")

        # 获取需要同步的股票代码（从本地数据库获取）
        with engine.connect() as conn:
            # 获取所有股票代码
            all_codes_result = conn.execute(text("SELECT DISTINCT code FROM daily_k ORDER BY code")).fetchall()
            all_codes = [row[0] for row in all_codes_result]

        if not all_codes:
            logger.error("No stocks found in database")
            with sync_progress_lock:
                sync_progress["is_running"] = False
            return

        with sync_progress_lock:
            sync_progress["total"] = len(all_codes)
            sync_progress["status_text"] = f"正在同步 {len(all_codes)} 只股票..."

        logger.info(f"Starting sync for {len(all_codes)} stocks using multi-source...")

        # 执行批量同步（使用腾讯等稳定数据源）
        results = syncer.sync_batch(
            all_codes,
            delay_range=(1.2, 2.5),
            progress_callback=lambda current, total, success, failed: update_sync_progress(
                current, total, success, failed, len(all_codes)
            )
        )

        logger.info(f"Sync completed: {results}")

    except Exception as e:
        logger.error(f"Background sync error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        with sync_progress_lock:
            sync_progress["is_running"] = False


def update_sync_progress(current: int, total: int, success: int, failed: int, overall_total: int):
    """更新同步进度回调"""
    with sync_progress_lock:
        sync_progress["current"] = current
        sync_progress["success"] = success
        sync_progress["fail"] = failed
        sync_progress["total"] = overall_total
        sync_progress["status_text"] = f"正在同步... ({current}/{total})"

@app.post("/api/sync/daily")
def start_sync(background_tasks: BackgroundTasks):
    """Start daily data synchronization."""
    with sync_progress_lock:
        if sync_progress["is_running"]:
            return {"status": "already_running", "progress": sync_progress}

    background_tasks.add_task(background_sync_task)
    return {"status": "started"}


@app.get("/api/sync/status")
def get_sync_status() -> Dict[str, Any]:
    """Get current synchronization progress."""
    with sync_progress_lock:
        return sync_progress.copy()

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
    local_only: bool = True,
    data_date: Optional[str] = None
):
    """
    Internal core scanning logic

    Args:
        data_date: 指定使用的数据日期 (YYYY-MM-DD 格式)，为 None 时自动选择最新日期
    """
    try:
        snapshot_df = pd.DataFrame()
        engine = get_db_engine()

        # 1. 如果不是强制本地，尝试联网获取快照
        if not local_only and data_date is None:
            try:
                snapshot_df = get_market_snapshot()
            except:
                logger.debug("Network snapshot failed.")

        # 2. 如果数据为空（联网失败 或 强制本地），启用本地数据库兜底
        if snapshot_df.empty:
            logger.info(f"Switching to LOCAL DB mode (Local Only: {local_only}, Data Date: {data_date or 'Auto'})...")
            try:
                with engine.connect() as conn:
                    # 如果指定了日期，使用指定日期；否则查找有足够数据的最近日期
                    if data_date:
                        # 验证日期格式和存在性
                        date_check = conn.execute(
                            text("SELECT date, COUNT(DISTINCT code) as stock_count FROM daily_k WHERE date = :date GROUP BY date"),
                            {"date": data_date}
                        ).fetchone()
                        if not date_check:
                            raise HTTPException(
                                status_code=400,
                                detail=f"指定日期 {data_date} 没有数据或格式不正确。请使用 YYYY-MM-DD 格式。"
                            )
                        max_date = data_date
                        stock_count = date_check[1]
                        logger.info(f"Using specified date: {max_date} ({stock_count} stocks)")
                    else:
                        # 查找有足够数据的最近日期（至少 1000 只股票）
                        logger.debug("Querying DB for best available date...")
                        best_date_query = text("""
                            SELECT date, COUNT(DISTINCT code) as stock_count
                            FROM daily_k
                            GROUP BY date
                            HAVING COUNT(DISTINCT code) >= 1000
                            ORDER BY date DESC
                            LIMIT 1
                        """)
                        best_date_res = conn.execute(best_date_query).fetchone()
                        if best_date_res and best_date_res[0]:
                            max_date = best_date_res[0]
                            stock_count = best_date_res[1]
                            logger.info(f"Found best date in DB: {max_date} ({stock_count} stocks)")
                        else:
                            raise HTTPException(status_code=503, detail="数据库中没有足够的数据进行扫描")

                    # 使用参数化查询防止 SQL 注入
                    query = text("""
                        SELECT d.code, b.name, d.close as price, d.open, d.high, d.low, d.vol,
                               2.0 as pct_chg, 10.0 as turnover, 10000000000.0 as mkt_cap
                        FROM daily_k d
                        LEFT JOIN stock_basic b ON d.code = b.code
                        WHERE d.date = :max_date
                    """)
                    snapshot_df = pd.read_sql(query, engine, params={"max_date": max_date})
                    logger.info(f"Loaded {len(snapshot_df)} rows from DB fallback.")
                    # 保存数据日期信息用于返回
                    if hasattr(snapshot_df, 'attrs'):
                        snapshot_df.attrs['data_date'] = max_date
                    # Fallback for name if join failed
                    if not snapshot_df.empty:
                        snapshot_df['name'] = snapshot_df['name'].fillna(snapshot_df['code'])
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Local fallback error: {e}")
                raise HTTPException(status_code=500, detail=f"加载数据失败: {str(e)}")
            
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
            (snapshot_df['mkt_cap'] >= mkt_cap_min * 100000000) &  # UI 传过来的是"亿"为单位
            (snapshot_df['turnover'] >= turnover_min)
        ].copy()

        logger.info(f"Snapshot: {total_snapshot} stocks")
        logger.info(f"After SOP Filter (No ST/BJ/Delist, +%, TO>{turnover_min}%, MC>{mkt_cap_min}亿): {len(candidates)} candidates")
        
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
                logger.warning(f"{market_range} filter failed: {e}")

        # 3. 安全检查：如果待扫描数量依然过多，提示用户缩小范围
        # 已移除数量限制 - 用户可根据需要扫描任意数量的股票
        # max_allowed = 4000 if local_only else 1200
        # if len(candidates) > max_allowed:
        #     mode_desc = "本地" if local_only else "在线"
        #     raise HTTPException(
        #         status_code=400,
        #         detail=f"{mode_desc}模式待扫描股票过多 ({len(candidates)}只/上限{max_allowed}), 请缩小市场范围或调高筛选条件。"
        #     )
        logger.info(f"准备扫描 {len(candidates)} 只股票...")
            
        results = []
        engine = get_db_engine()
        
        # 核心优化：预拉取指数历史并过滤，避免在线程内重复查询和过滤
        from core.data import get_index_hist
        bench_df = get_index_hist("000001")
        bench_slice = None
        if not bench_df.empty:
            # 预先过滤出需要的日期范围
            hist_end = datetime.now() if not data_date else datetime.strptime(data_date, "%Y-%m-%d")
            hist_start = hist_end - timedelta(days=365)
            mask = (bench_df['日期'] >= hist_start.strftime("%Y-%m-%d")) & (bench_df['日期'] <= hist_end.strftime("%Y-%m-%d"))
            bench_slice = bench_df.loc[mask, ['日期', '收盘']].copy()
            logger.info(f"Pre-filtered benchmark data: {len(bench_slice)} points.")
        
        # 核心优化：批量拉取所有候选标的的 250 天历史数据，避免在线程内重复查询数据库
        logger.info(f"Pre-loading historical data for {len(candidates)} candidates in batch...")
        start_time = time.time()
        # If data_date is specified, use it as end_date, otherwise use today
        end_date_hist = datetime.now().strftime("%Y-%m-%d") if not data_date else data_date
        start_date_hist = (datetime.strptime(end_date_hist, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
        candidate_codes = candidates['code'].tolist()
        hist_map = {}

        try:
            # 分批拉取防止 SQL 语句过长 - 使用参数化查询
            chunk_size = 800
            for i in range(0, len(candidate_codes), chunk_size):
                chunk = candidate_codes[i:i + chunk_size]
                # 使用参数化查询防止 SQL 注入
                placeholders = ", ".join([f":code_{j}" for j in range(len(chunk))])
                params = {f"code_{j}": c for j, c in enumerate(chunk)}
                params["start_date"] = start_date_hist
                params["end_date"] = end_date_hist

                query = text(f"""
                    SELECT code, date as "日期", open as "开盘", high as "最高",
                           low as "最低", close as "收盘", vol as "成交量"
                    FROM daily_k
                    WHERE code IN ({placeholders}) AND date >= :start_date AND date <= :end_date
                    ORDER BY date ASC
                """)
                with engine.connect() as conn:
                    chunk_df = pd.read_sql(query, conn, params=params)
                    if not chunk_df.empty:
                        # 统一日期格式为字符串，确保比较和后续计算速度
                        if pd.api.types.is_datetime64_any_dtype(chunk_df['日期']):
                            chunk_df['日期'] = chunk_df['日期'].dt.strftime('%Y-%m-%d')
                        elif chunk_df['日期'].dtype == 'object':
                            # Ensure it's string explicitly if needed
                            chunk_df['日期'] = chunk_df['日期'].astype(str).str[:10]
                        
                        # 按代码分组并存入映射
                        for code, group in chunk_df.groupby('code'):
                            hist_map[code] = group
            logger.info(f"Pre-loaded history for {len(hist_map)} stocks.")
        except Exception as e:
            logger.warning(f"Batch loading failed: {e}. Falling back to individual queries.")

        # 并发扫描逻辑 - 调整并发数以平衡 CPU 负载
        workers = 12 if local_only else 8
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_stock = {
                executor.submit(
                    single_stock_task,
                row['code'], row['name'], row['price'], row['vol'], row['open'],
                threshold, vol_multiplier, rsi_min, use_macd_filter, use_bb_sqz, sqz_lookback, use_weekly, use_rs_filter,
                local_only=local_only, engine=engine, preloaded_df=hist_map.get(row['code']), target_date=data_date,
                bench_df=bench_slice
            ): row for _, row in candidates.iterrows()
            }
            
            fail_reasons = {}
            none_count = 0
            for future in as_completed(future_to_stock):
                try:
                    # 单个股票分析超时设为 30s，防止某一个接口挂起卡死全场
                    res = future.result(timeout=30)
                    if isinstance(res, dict) and 'Score' in res:
                        results.append(res)
                    elif isinstance(res, dict):
                        reason = res.get('reason', '未知原因')
                        fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
                    elif res is None:
                        none_count += 1
                except Exception as e:
                    fail_reasons[f"异常: {str(e)[:30]}"] = fail_reasons.get(f"异常: {str(e)[:30]}", 0) + 1

            logger.info(f"Scan Stats: Matches={len(results)}, Rejections={sum(fail_reasons.values())}, Silent=None({none_count})")
            if fail_reasons:
                logger.info(f"Rejection Summary: {fail_reasons}")

        logger.info(f"Scan completed in {time.time() - start_time:.2f}s. Found {len(results)} matches.")
        
        # 排序并取 Top 30
        results = sorted(results, key=lambda x: x['Score'], reverse=True)[:30]
        
        # 补充增强数据 (行业, 胜率) - 核心优化：只对最终入选的 30 只股票计算胜率
        logger.info(f"Calculating historical win rate and supplements for top {len(results)} matches...")
        from core.strategy import calculate_historical_win_rate
        for res in results:
            code = res['代码']
            # Find the history in hist_map
            df_hist = hist_map.get(code)
            if df_hist is not None and not df_hist.empty:
                # 重新应用指标计算以确保完整（或者我们可以重用分析时的 df，但由于并发，这里重新算更简单）
                from core.indicators import calculate_indicators
                # Note: We don't have price/vol/open here directly, but indicators should already be in df_hist if we were careful
                # Let's assume we need to calculate them if they are missing or just recalculate for safety
                df_labeled = calculate_indicators(df_hist, bench_df=bench_slice)
                wr, sig_count = calculate_historical_win_rate(df_labeled)
                res['历史胜率'] = f"{wr}%"
                res['信号次数'] = sig_count

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
        logger.info(f"Supplementing industry info for {len(results)} results in parallel...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_industry = {executor.submit(fetch_single_industry, res): res for res in results}
            industry_results = {}
            for future in as_completed(future_to_industry):
                try:
                    code, ind = future.result(timeout=10)
                    industry_results[code] = ind
                except Exception:
                    continue
        
        for res in results:
            res['行业'] = industry_results.get(res['代码'], "未知")
            
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
        save_scan_results(results, engine)
        
        return results
    except HTTPException as he:
        # 允许 HTTPException 直接通过，不再包装成 500
        raise he
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def single_stock_task(code, name, price, vol, open_price, threshold, vol_multiplier, rsi_min, use_macd_filter, use_bb_sqz, sqz_lookback, use_weekly, use_rs_filter=True, local_only=False, engine=None, preloaded_df=None, target_date=None, bench_df=None):
    from core.db import load_from_db
    import akshare as ak

    # Use provided target_date or default to now
    if target_date is None or target_date == "":
        target_date = datetime.now()
    elif isinstance(target_date, str):
        target_date = datetime.strptime(target_date, "%Y-%m-%d")

    start_date = (target_date - timedelta(days=250)).strftime("%Y%m%d")
    end_date_str = target_date.strftime("%Y-%m-%d")
    
    # 优先使用预加载的数据
    if preloaded_df is not None and not preloaded_df.empty:
        df = preloaded_df
    else:
        df = load_from_db(code, (target_date - timedelta(days=360)).strftime("%Y-%m-%d"), engine)
        if not df.empty and pd.api.types.is_datetime64_any_dtype(df['日期']):
            df['日期'] = df['日期'].dt.strftime('%Y-%m-%d')
    
    # 逻辑调整：如果是 local_only，且数据库为空，则直接跳过
    if df.empty and local_only:
        return {"reason": "本地数据缺失 (Local-Only 模式已开启)"}

    stale_threshold = (target_date - timedelta(days=3)).strftime("%Y-%m-%d")
    if df.empty or df.iloc[-1]['日期'] < stale_threshold:
        if local_only:
            # 即使数据旧，也尝试用现有的，如果没有则跳过
            if df.empty: return {"reason": "数据库无此代码数据"}
            logger.warning(f"[{code}] Using stale local data (Local-Only)")
        else:
            try:
                logger.info(f"[{code}] Fetching fresh data...")
                df_new = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, adjust="qfq")
                if isinstance(df_new, pd.DataFrame) and not df_new.empty:
                    df = df_new
                    from core.db import save_to_db
                    save_to_db(df, code, engine) 
                    time.sleep(random.uniform(0.1, 0.3))
            except Exception as e:
                logger.error(f"[{code}] Hist fetch error: {e}")
                return {"reason": f"接口请求失败: {str(e)}"}
            
    if df.empty or len(df) < 120: 
        logger.debug(f"[{code}] Insufficient data ({len(df)})")
        return None
    
    try:
        from core.indicators import calculate_indicators
        df = calculate_indicators(df, current_price=price, current_vol=vol, current_open=open_price, bench_df=bench_df)
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
            logger.info(f"[{code}] Resonance Match!")
            if use_weekly:
                from core.indicators import get_weekly_indicators
                if not get_weekly_indicators(code, df=df, local_only=local_only): 
                    logger.info(f"[{code}] Weekly trend failed")
                    return {"reason": "周线趋势未走好"}
            
            # 胜率计算已移至外层 Top 30 逻辑中，避免在此高并发环节进行昂贵计算
            stats['代码'] = code
            stats['名称'] = name
            return stats
        else:
            return stats # 返回包含失败原因的字典
    except Exception as e:
        logger.error(f"[{code}] Analysis error: {e}")
        return {"reason": f"分析异常: {str(e)}"}
    
    return {"reason": "未知错误"}

@app.get("/api/stock/{code}/kline")
async def get_stock_kline(code: str, local_only: bool = False):
    """
    获取个股 K 线数据供前端绘图

    Args:
        code: Stock code (validated)
        local_only: If True, only use local data
    """
    # 验证股票代码格式
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code format")

    from core.db import load_from_db

    engine = get_db_engine()
    target_date = datetime.now()
    # 获取近 300 天的数据，确保有足够的交易日来画出 100-200 根 K 线
    start_date_str = (target_date - timedelta(days=300)).strftime("%Y-%m-%d")

    df = load_from_db(code, start_date_str, engine)

    if df.empty and not local_only:
        try:
            logger.debug(f"API: Fetching K-line for {code}...")
            start_fetch = (target_date - timedelta(days=300)).strftime("%Y%m%d")
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_fetch, adjust="qfq")
            if not df.empty:
                from core.db import save_to_db
                save_to_db(df, code, engine)
        except Exception as e:
            logger.warning(f"K-line fetch error for {code}: {e}")

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
            logger.error(f"Fetch error for {code}: {e}")
            
    if df.empty: return df
    
    # Calculate indicators
    df = calculate_indicators(df, periods=[5, 10, 20, 60])
    return df

@app.get("/api/stock/detail")
def get_stock_detail(code: str):
    """
    获取单只股票详情 (k线指标 + 资金面) 用于 AI Deep Dive

    Args:
        code: Stock code
    """
    # 验证股票代码格式
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code format")

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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching stock detail for {code}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/scan/history")
async def get_history_results(date: str):
    """获取指定日期的历史选股结果"""
    return get_scan_history_by_date(date)

@app.get("/api/scan/dates")
async def get_history_dates():
    """获取历史扫描日期列表"""
    return get_scan_dates()


@app.get("/api/scan/available-dates")
async def get_available_dates_api() -> Dict[str, Any]:
    """获取可用于选股的数据日期列表"""
    dates = get_available_dates()
    return {"dates": dates}


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
    local_only: bool = True,
    data_date: Optional[str] = None
):
    """
    API Endpoint for market scan

    Args:
        data_date: 指定使用的数据日期 (YYYY-MM-DD 格式)，为 None 时自动选择最新日期
    """
    return run_market_scan(
        threshold, vol_multiplier, rsi_min, use_macd_filter,
        use_bb_sqz, sqz_lookback, use_weekly, market_range,
        turnover_min, mkt_cap_min, use_rs_filter, local_only, data_date
    )

@app.get("/api/settings")
def get_settings_api() -> Dict[str, Any]:
    """获取系统设置（不暴露敏感信息）"""
    return config.get_bark_safe_status()

@app.post("/api/settings")
def save_settings_api(data: dict):
    if "sentinel_time" in data:
        save_setting("sentinel_time", data["sentinel_time"])
        sentinel.trigger_time = data["sentinel_time"] # Update live
    return {"status": "success"}

@app.post("/api/paper/add")
def add_paper_trade(trade: PaperTradeCreate) -> Dict[str, Any]:
    """Add a paper trade entry"""
    # 验证股票代码格式
    if not validate_stock_code(trade.code):
        return {"status": "error", "detail": "Invalid stock code format"}

    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
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
        logger.error(f"Error adding paper trade: {e}")
        return {"status": "error", "detail": "Internal server error"}

@app.get("/api/paper/list")
def list_paper_trades() -> List[Dict[str, Any]]:
    """List all paper trades"""
    engine = get_db_engine()
    if not engine:
        return []
    try:
        df = pd.read_sql("SELECT * FROM paper_trading ORDER BY entry_date DESC", engine)
        return df.to_dict('records')
    except Exception as e:
        logger.error(f"Error listing paper trades: {e}")
        return []


@app.delete("/api/paper/remove/{id}")
def remove_paper_trade(id: int) -> Dict[str, str]:
    """Remove a paper trade by ID"""
    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM paper_trading WHERE id = :id"), {"id": id})
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error removing paper trade: {e}")
        return {"status": "error"}


@app.get("/api/test/push")
def test_push_notification() -> Dict[str, Any]:
    """测试 Bark 推送功能"""
    mock_data = [
        {"code": "600519", "name": "测试茅台", "price": 1800.0},
        {"code": "300750", "name": "测试时代", "price": 450.0}
    ]
    try:
        msg = send_intraday_notification(mock_data)
        return {"status": "success", "message": f"Push sent: {msg}"}
    except Exception as e:
        logger.error(f"Test push error: {e}")
        return {"status": "error", "message": "Internal server error"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
