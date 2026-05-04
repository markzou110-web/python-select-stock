import akshare as ak
import pandas as pd
import time
import random
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Dict, List, Any, Optional, Tuple
from .db import save_to_db, get_db_engine, save_stock_basic, get_stock_basic_map, validate_stock_code
from sqlalchemy import text
from .indicators import calculate_ema
from .logging_config import logger

def get_market_regime() -> Dict[str, Any]:
    """
    获取大盘环境：结合上证指数 (000001) 和 创业板指 (399006)
    """
    indices = {"000001": "上证", "399006": "创业"}
    states = {}
    
    try:
        now = datetime.now()
        start_date = (now - timedelta(days=60)).strftime('%Y%m%d')
        end_date = now.strftime('%Y%m%d')
        
        for code, name in indices.items():
            # 使用更可靠的 index_zh_a_hist
            df = ak.index_zh_a_hist(symbol=code, period="daily", 
                                   start_date=(now - timedelta(days=60)).strftime('%Y%m%d'),
                                   end_date=now.strftime('%Y%m%d'))
            
            if df.empty: continue
            
            # index_zh_a_hist 返回的列名为: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
            # 无需重命名，直接计算 EMA20
            df = calculate_ema(df, 20)
            
            latest = df.iloc[-1]
            close = float(latest['收盘'])
            ema20 = float(latest['EMA20'])
            
            states[name] = {
                "close": round(close, 2),
                "ema20": round(ema20, 2),
                "trend": "BULL" if close > ema20 else "BEAR"
            }
            
        # 综合评判
        if states["上证"]["trend"] == "BULL" and states["创业"]["trend"] == "BULL":
            status = "OFFENSIVE"
            desc = "进攻模式：双指数均站上 20 日线"
        elif states["上证"]["trend"] == "BEAR" and states["创业"]["trend"] == "BEAR":
            status = "CRITICAL"
            desc = "空仓防守：双指数均跌破 20 日线"
        else:
            status = "DEFENSIVE"
            desc = "减仓观望：市场进入震荡/分化期"
            
        return {
            "status": status,
            "desc": desc,
            "indices": states,
            "updated_at": datetime.now().strftime('%H:%M:%S')
        }
    except Exception as e:
        logger.error(f"Error getting market regime: {e}")
        return {"status": "UNKNOWN", "desc": "数据获取失败", "indices": {}, "updated_at": ""}

# --- 禁用代理以避免连接问题 ---
# 禁用 requests 和 urllib 的代理
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

# --- Bounded TTL Cache with auto-eviction ---
_CACHE_MAX_SIZE = 128
CACHE: Dict[str, Tuple[Any, float]] = {}


def _evict_expired() -> None:
    """Remove all expired entries from cache."""
    now = time.time()
    expired = [k for k, (_, ts) in CACHE.items() if now - ts > 86400]
    for k in expired:
        del CACHE[k]


def get_cached_data(key: str, ttl_seconds: int) -> Optional[Any]:
    """
    Get data from cache if still valid.

    Args:
        key: Cache key
        ttl_seconds: Time-to-live in seconds

    Returns:
        Cached data or None if expired/not found
    """
    if key in CACHE:
        data, timestamp = CACHE[key]
        if time.time() - timestamp < ttl_seconds:
            return data
        del CACHE[key]
    return None


def set_cached_data(key: str, data: Any) -> None:
    """Store data in cache with current timestamp. Evicts expired entries when full."""
    if len(CACHE) >= _CACHE_MAX_SIZE:
        _evict_expired()
    if len(CACHE) >= _CACHE_MAX_SIZE:
        oldest_key = min(CACHE, key=lambda k: CACHE[k][1])
        del CACHE[oldest_key]
    CACHE[key] = (data, time.time())

def get_market_snapshot() -> pd.DataFrame:
    """获取全市场实时快照 (v5.1 - 强化防封与缓存)"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # 增加微小随机延迟以打散并发请求
            time.sleep(random.uniform(0.1, 0.5))
            df = ak.stock_zh_a_spot_em()
            # 重命名常用列以便处理
            df = df.rename(columns={
                '代码': 'code',
                '名称': 'name',
                '最新价': 'price',
                '今开': 'open',
                '涨跌幅': 'pct_chg',
                '成交量': 'vol',
                '换手率': 'turnover',
                '总市值': 'mkt_cap',
                '市盈率-动态': 'pe'
            })
            return df
        except Exception as e:
            if attempt < max_retries - 1:
                # 增强退避等待
                wait_time = (attempt + 1) * 4
                logger.warning(f"Snapshot fetch failed (attempt {attempt+1}), retrying in {wait_time}s... Error: {e}")
                time.sleep(wait_time)
                continue
            logger.error(f"Error fetching snapshot after {max_retries} attempts: {e}")
            return pd.DataFrame()

def sync_stock(code: str, name: str, engine=None) -> bool:
    """
    同步单只股票的缺失数据

    Args:
        code: Stock code
        name: Stock name
        engine: Database engine (optional)

    Returns:
        True if successful, False otherwise
    """
    from sqlalchemy import text

    # Validate stock code
    if not validate_stock_code(code):
        logger.error(f"Invalid stock code: {code}")
        return False

    if engine is None:
        engine = get_db_engine()
    if not engine:
        return False

    try:
        # 获取最新日期 - 使用参数化查询
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT MAX(date) FROM daily_k WHERE code = :code"),
                {"code": code}
            )
            last_date = result.fetchone()[0]

        if last_date:
            fetch_start = (last_date + timedelta(days=1)).strftime("%Y%m%d")
        else:
            fetch_start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

        today_str = datetime.now().strftime("%Y%m%d")
        if last_date and last_date.strftime("%Y%m%d") >= today_str:
             return True

        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=fetch_start, adjust="qfq")
        if not df.empty:
            save_to_db(df, code, engine=engine)
        return True
    except Exception as e:
        logger.error(f"sync_stock Error ({code}): {e}")
        return False

def get_index_data() -> Dict[str, Dict[str, float]]:
    """获取主要指数实时行情 (并发拉取 + 缓存)"""
    cached = get_cached_data('index_data', 60)
    if cached:
        return cached

    indices = {
        "上证": "000001",
        "创业板": "399006",
        "沪深300": "000300",
        "科创50": "000688",
        "中证1000": "000852"
    }

    def fetch_one_with_retry(name: str, code: str, retries: int = 2) -> Tuple[str, Optional[Dict[str, float]]]:
        for i in range(retries):
            try:
                # 增加随机延迟避免并发请求被限流
                time.sleep(random.uniform(0.3, 0.8))
                df = ak.index_zh_a_hist(symbol=code, period="daily",
                                       start_date=(datetime.now() - timedelta(days=10)).strftime("%Y%m%d"))
                if not df.empty:
                    curr = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else curr
                    pct = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
                    return name, {'price': float(curr['收盘']), 'pct': round(pct, 2)}
            except Exception as e:
                if i < retries - 1:
                    time.sleep(random.uniform(0.5, 1.5))
                else:
                    logger.debug(f"Index fetch failed for {name}: {str(e)[:50]}")
        return name, None

    res = {}
    # 降低并发度，减少 EastMoney 连通重置风险
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(fetch_one_with_retry, name, code) for name, code in indices.items()]
            for future in futures:
                try:
                    name, data = future.result(timeout=20)
                    if data:
                        res[name] = data
                except Exception:
                    pass
    except Exception:
        pass

    # 兜底：用上次成功获取的缓存数据补全缺失项
    if 'index_data' in CACHE:
        old_data = CACHE['index_data'][0]
        for name in indices:
            if name not in res and name in old_data:
                res[name] = old_data[name]
                logger.debug(f"Index {name} using stale cache")

    if res:
        set_cached_data('index_data', res)
    return res

def get_hot_sectors() -> List[Dict[str, Any]]:
    """获取热门行业板块指数 (缓存 10 分钟)"""
    cached = get_cached_data('hot_sectors', 600)
    if cached:
        return cached

    for i in range(3):
        try:
            df = ak.stock_board_industry_name_em()
            if not df.empty:
                df_sorted = df.sort_values('涨跌幅', ascending=False).head(5)
                hot_sectors = []
                for _, row in df_sorted.iterrows():
                    hot_sectors.append({
                        'name': row['板块名称'],
                        'pct': row['涨跌幅'],
                        'lead': row['领涨股票']
                    })
                set_cached_data('hot_sectors', hot_sectors)
                return hot_sectors
        except Exception as e:
            if i < 2:
                time.sleep(1)
            logger.debug(f"Hot sectors fetch attempt {i+1} failed: {e}")

    # 如果获取失败但有过期缓存，返回过期缓存
    if 'hot_sectors' in CACHE:
        logger.debug("Using expired cache for hot sectors")
        return CACHE['hot_sectors'][0]

    return []

def get_sector_map() -> Dict[str, str]:
    """获取全市场个股行业映射 (重量级操作，优先读取数据库)"""
    # 1. 内存缓存
    cached = get_cached_data('sector_map', 86400)
    if cached:
        return cached

    # 2. 数据库缓存 (可靠性保障)
    db_map = get_stock_basic_map()
    # 增加校验：如果绝大部分是'未知'，说明需要重新爬取行业分布
    unknown_count = list(db_map.values()).count('未知')
    if db_map and (len(db_map) == 0 or unknown_count / len(db_map) < 0.5):
        set_cached_data('sector_map', db_map)
        return db_map

    logger.info("Building sector map from API and persisting to DB...")
    sector_map = {}
    try:
        # 1. 获取所有行业板块名称
        df_board = ak.stock_board_industry_name_em()
        if df_board.empty:
            # 策略：如果东财接口失效，尝试使用 Tushare 兜底
            return _get_sector_map_from_tushare()

        # 获取所有行业板块 (不仅是前 50 个)
        all_boards = df_board['板块名称'].tolist()
        logger.info(f"Found {len(all_boards)} industry boards to map.")

        # 2. 并发抓取成分股
        def fetch_sector_with_retry(sector_name: str, retries: int = 3) -> Tuple[Optional[str], Optional[pd.DataFrame]]:
            for i in range(retries):
                try:
                    time.sleep(random.uniform(0.5, 1.0))
                    df_curr = ak.stock_board_industry_cons_em(symbol=sector_name)
                    if not df_curr.empty:
                        return sector_name, df_curr[['代码', '名称']].copy()
                except Exception:
                    pass
            return None, None

        all_basic_data = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_sector = {executor.submit(fetch_sector_with_retry, name): name for name in all_boards}

            for future in as_completed(future_to_sector):
                try:
                    s_name, df_codes = future.result(timeout=15)
                    if df_codes is not None:
                        for _, row in df_codes.iterrows():
                            code, name = row['代码'], row['名称']
                            sector_map[code] = s_name
                            all_basic_data.append({'code': code, 'name': name, 'industry': s_name})
                except Exception:
                    continue

        # 3. 持久化到数据库
        if all_basic_data:
            df_basic = pd.DataFrame(all_basic_data)
            save_stock_basic(df_basic)

        if sector_map:
            set_cached_data('sector_map', sector_map)
            logger.info(f"Full sector map built and persisted: {len(sector_map)} stocks mapped.")
        return sector_map
    except Exception as e:
        logger.error(f"Critical error in get_sector_map: {e}")
        # 发生严重错误时，尝试使用 Tushare 兜底
        return _get_sector_map_from_tushare()

def _get_sector_map_from_tushare() -> Dict[str, str]:
    """使用 Tushare 作为行业映射的备选方案"""
    try:
        from core.multi_source_sync import TushareDataSource
        ts_source = TushareDataSource()
        if not ts_source.pro:
            return {}
            
        logger.info("Fetching sector map from Tushare backup...")
        df_ts = ts_source.get_stock_list()
        if df_ts is not None and not df_ts.empty:
            # 基础同步器中返回的是 ['代码', '名称', '所属行业']，存储前需统一重命名
            df_ts = df_ts.rename(columns={'代码': 'code', '名称': 'name', '所属行业': 'industry'})
            
            # 存入数据库以持久化
            from core.db import save_stock_basic, get_db_engine
            save_stock_basic(df_ts, get_db_engine())
            
            # 返回映射字典
            return pd.Series(df_ts['industry'].values, index=df_ts['code']).to_dict()
    except Exception as e:
        logger.warning(f"Tushare fallback failed: {e}")
    return {}

def get_index_hist(code: str) -> pd.DataFrame:
    """
    获取指数历史用于基准计算 (缓存 24 小时)

    Args:
        code: Index code

    Returns:
        DataFrame with historical index data
    """
    cache_key = f'index_hist_{code}'
    cached = get_cached_data(cache_key, 86400)
    if cached is not None:
        return cached

    try:
        df = ak.index_zh_a_hist(symbol=code, period="daily")
        if not df.empty:
            set_cached_data(cache_key, df)
        return df
    except Exception as e:
        logger.debug(f"Error fetching index hist for {code}: {e}")
        return pd.DataFrame()
