import akshare as ak
import pandas as pd
import time
import random
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Dict, List, Any, Optional, Tuple, Callable
from .db import save_to_db, get_db_engine, save_stock_basic, get_stock_basic_map, validate_stock_code
from sqlalchemy import text
from .indicators import calculate_ema
from .logging_config import logger


# ═══════════════════════════════════════════════════════
# Feature 1: 数据源容灾智能切换 — resilient_fetch 工厂
# ═══════════════════════════════════════════════════════

def resilient_fetch(fetch_funcs: List[Callable], timeout: int = 8, label: str = "data"):
    """
    按优先级依次尝试多个数据源函数，任何一个成功即返回结果。
    所有调用强制 timeout 秒超时，防止 akshare 底层挂起。
    
    Args:
        fetch_funcs: 一组 callable，按优先级排列
        timeout: 每个源的最大等待秒数
        label: 日志标签
    Returns:
        第一个成功函数的返回值，或 None
    """
    for i, func in enumerate(fetch_funcs):
        source_name = getattr(func, '__name__', f'source_{i}')
        # 每个源内部尝试 2 次
        for attempt in range(2):
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(func)
                    result = future.result(timeout=timeout)
                    if result is not None and not (isinstance(result, pd.DataFrame) and result.empty):
                        return result
            except FuturesTimeoutError:
                logger.warning(f"[{label}] {source_name} timed out after {timeout}s (attempt {attempt+1})")
            except Exception as e:
                logger.debug(f"[{label}] {source_name} failed (attempt {attempt+1}): {str(e)[:80]}")
            
            if attempt == 0:
                time.sleep(1) # 重试间隔
                
    logger.warning(f"[{label}] All {len(fetch_funcs)} sources failed after multiple attempts.")
    return None

def get_market_regime() -> Dict[str, Any]:
    """
    获取大盘环境：结合上证指数 (000001) 和 创业板指 (399006)
    """
    indices_tx = {"sh000001": "上证", "sz399006": "创业"}
    states = {}
    
    try:
        for code, name in indices_tx.items():
            def _fetch_tx(c=code):
                return ak.stock_zh_index_daily_tx(symbol=c)
            def _fetch_em(c=code.replace('sh','').replace('sz','')):
                now = datetime.now()
                return ak.index_zh_a_hist(symbol=c, period="daily",
                    start_date=(now - timedelta(days=60)).strftime('%Y%m%d'),
                    end_date=now.strftime('%Y%m%d'))
            
            df = resilient_fetch([_fetch_tx, _fetch_em], timeout=15, label=f"regime_{name}")
            if df is None or df.empty:
                continue
            
            # 统一列名：腾讯源列名是 close，东财源是 收盘
            close_col = 'close' if 'close' in df.columns else '收盘'
            df_regime = pd.DataFrame({'收盘': df[close_col].values})
            df_regime = calculate_ema(df_regime, 20)
            
            latest = df_regime.iloc[-1]
            close = float(latest['收盘'])
            ema20 = float(latest['EMA20'])
            
            states[name] = {
                "close": round(close, 2),
                "ema20": round(ema20, 2),
                "trend": "BULL" if close > ema20 else "BEAR"
            }
        
        if "上证" not in states or "创业" not in states:
            return {"status": "UNKNOWN", "desc": "数据获取失败", "indices": states, "updated_at": ""}
            
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
    """获取全市场实时快照 (v6.0 - 引入 resilient_fetch 多源容灾弹性重构)"""
    # 增加 60 秒的高速缓存，防止双策略或并发扫描时频繁高负荷请求
    cached = get_cached_data('market_snapshot', 60)
    if cached is not None:
        logger.info("Using cached market snapshot data.")
        return cached

    def _fetch_snapshot_em():
        # 增加随机延迟以避开频率限制
        time.sleep(random.uniform(0.1, 0.5))
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            raise ValueError("Eastmoney returned empty snapshot")
            
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

    def _fetch_snapshot_sina():
        # 增加随机延迟
        time.sleep(random.uniform(0.1, 0.5))
        df = ak.stock_zh_a_spot()
        if df is None or df.empty:
            raise ValueError("Sina returned empty snapshot")
        
        # 统一列名映射
        df = df.rename(columns={
            '代码': 'code',
            '名称': 'name',
            '最新价': 'price',
            '今开': 'open',
            '涨跌幅': 'pct_chg',
            '成交量': 'vol'
        })
        
        # 提取 6 位纯数字代码
        df['code'] = df['code'].str.extract(r'(\d{6})')
        
        # 新浪成交量单位是股，东财是手。统一转换为手 (1手 = 100股)
        if 'vol' in df.columns:
            df['vol'] = pd.to_numeric(df['vol'], errors='coerce') / 100.0
            
        # 补全东财快照特有的字段
        df['turnover'] = None
        df['mkt_cap'] = None
        df['pe'] = None
        
        # 只保留标准快照列
        cols = ['code', 'name', 'price', 'open', 'pct_chg', 'vol', 'turnover', 'mkt_cap', 'pe']
        df = df[cols]
        return df

    def _fetch_snapshot_tencent():
        """使用腾讯行情API，并发分批抓取全市场快照"""
        basic_map = get_stock_basic_map()
        codes = list(basic_map.keys())
        if not codes:
            raise ValueError("Tencent fetch aborted: Local database stock_basic table has no codes.")
        
        logger.info(f"Tencent snapshot fetch triggered for {len(codes)} stocks.")
        
        def format_tencent_code(code: str) -> str:
            if code.startswith('6') or code.startswith('900'):
                return f'sh{code}'
            elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
                return f'sz{code}'
            elif code.startswith('8') or code.startswith('4') or code.startswith('920'):
                return f'bj{code}'
            return code

        def fetch_tencent_chunk(chunk_codes):
            symbols = [format_tencent_code(c) for c in chunk_codes]
            url = f"http://qt.gtimg.cn/q={','.join(symbols)}"
            try:
                r = requests.get(url, timeout=8)
                if r.status_code != 200:
                    return []
                r.encoding = 'gbk'
                lines = r.text.strip().split('\n')
                results = []
                for line in lines:
                    if not line or '"' not in line:
                        continue
                    try:
                        content = line.split('"')[1]
                        parts = content.split('~')
                        if len(parts) < 46:
                            continue
                        
                        # 提取核心字段
                        code = parts[2]
                        name = parts[1]
                        price = float(parts[3]) if parts[3] else None
                        open_val = float(parts[5]) if parts[5] else None
                        pct_chg = float(parts[32]) if parts[32] else 0.0
                        vol = float(parts[6]) if parts[6] else 0.0 # 已经是手
                        turnover = float(parts[38]) if parts[38] else None
                        mkt_cap = float(parts[45]) * 100000000.0 if parts[45] else None # 换算为元
                        pe = float(parts[39]) if parts[39] else None
                        
                        results.append({
                            'code': code,
                            'name': name,
                            'price': price,
                            'open': open_val,
                            'pct_chg': pct_chg,
                            'vol': vol,
                            'turnover': turnover,
                            'mkt_cap': mkt_cap,
                            'pe': pe
                        })
                    except Exception:
                        continue
                return results
            except Exception as e:
                logger.debug(f"Tencent chunk fetch failed: {e}")
                return []

        chunk_size = 200
        chunks = [codes[i:i + chunk_size] for i in range(0, len(codes), chunk_size)]
        
        all_results = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_chunk = {executor.submit(fetch_tencent_chunk, chunk): chunk for chunk in chunks}
            for future in as_completed(future_to_chunk):
                try:
                    res = future.result()
                    if res:
                        all_results.extend(res)
                except Exception:
                    continue
                    
        if not all_results:
            raise ValueError("Tencent fetch returned empty result")
            
        df = pd.DataFrame(all_results)
        logger.info(f"Tencent snapshot fetched successfully: {len(df)} rows.")
        return df

    # 通过 resilient_fetch 容灾调用，给新浪源/腾讯源充足的超时空间（35秒）
    df = resilient_fetch([_fetch_snapshot_em, _fetch_snapshot_sina, _fetch_snapshot_tencent], timeout=35, label="market_snapshot")
    
    if df is not None and not df.empty:
        # 存入缓存
        set_cached_data('market_snapshot', df)
        return df
    
    logger.error("All real-time snapshot sources failed.")
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
    """获取主要指数实时行情 (容灾多源 + 缓存)"""
    cached = get_cached_data('index_data', 60)
    if cached:
        return cached

    indices = {
        "上证": "sh000001",
        "创业板": "sz399006",
        "沪深300": "sh000300",
        "科创50": "sh000688",
        "中证1000": "sh000852"
    }

    # 提前获取东财全指数快照作为备选 (EM 备选源)
    em_snapshot = pd.DataFrame()
    try:
        em_snapshot = ak.stock_zh_index_spot_em()
    except Exception as e:
        logger.debug(f"Failed to fetch EM index snapshot: {e}")

    def fetch_one(name: str, code: str) -> Tuple[str, Optional[Dict[str, float]]]:
        # 提取数字代码用于 EM 匹配
        numeric_code = "".join(filter(str.isdigit, code))

        def _tx():
            time.sleep(random.uniform(0.1, 0.3))
            df = ak.stock_zh_index_daily_tx(symbol=code)
            if df is not None and not df.empty:
                curr, prev = df.iloc[-1], df.iloc[-2] if len(df) > 1 else df.iloc[-1]
                pct = (curr['close'] - prev['close']) / prev['close'] * 100
                return {'price': float(curr['close']), 'pct': round(pct, 2)}
            return None
        
        def _em():
            if em_snapshot.empty: return None
            match = em_snapshot[em_snapshot['代码'] == numeric_code]
            if not match.empty:
                row = match.iloc[0]
                return {'price': float(row['最新价']), 'pct': float(row['涨跌幅'])}
            return None
        
        result = resilient_fetch([_tx, _em], timeout=15, label=f"index_{name}")
        return name, result

    res = {}
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(fetch_one, n, c) for n, c in indices.items()]
            for future in futures:
                try:
                    name, data = future.result(timeout=15)
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

    if res:
        set_cached_data('index_data', res)
    return res

def get_hot_sectors() -> List[Dict[str, Any]]:
    """获取热门行业板块指数 (容灾多源 + 缓存 10 分钟)"""
    cached = get_cached_data('hot_sectors', 600)
    if cached:
        return cached

    def _sina():
        df = ak.stock_sector_spot()
        if df is not None and not df.empty:
            df_sorted = df.sort_values('涨跌幅', ascending=False).head(5)
            return [{'name': str(r['板块']), 'pct': float(r['涨跌幅']), 'lead': str(r['股票名称'])} for _, r in df_sorted.iterrows()]
        return None

    def _em():
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty:
            df_sorted = df.sort_values('涨跌幅', ascending=False).head(5)
            return [{'name': str(r['板块名称']), 'pct': float(r['涨跌幅']), 'lead': str(r['领涨股票'])} for _, r in df_sorted.iterrows()]
        return None

    result = resilient_fetch([_sina, _em], timeout=8, label="hot_sectors")
    if result:
        set_cached_data('hot_sectors', result)
        return result

    # 兜底过期缓存
    if 'hot_sectors' in CACHE:
        return CACHE['hot_sectors'][0]
    return []

def get_sector_trends() -> Dict[str, Dict[str, Any]]:
    """获取全行业板块涨跌趋势，用于 SOP 大盘-板块-个股联动（缓存 10 分钟）
    
    Returns:
        Dict: { '半导体': {'pct': 3.2, 'trend': 'LEAD', 'lead_stock': 'xxx'}, ... }
        trend 分类: LEAD(领涨≥2%), FOLLOW(跟涨0~2%), FLAT(横盘-1~0%), DOWN(下跌<-1%)
    """
    cached = get_cached_data('sector_trends', 600)
    if cached:
        return cached

    def _classify(pct: float) -> str:
        if pct >= 2.0: return 'LEAD'
        if pct >= 0: return 'FOLLOW'
        if pct >= -1.0: return 'FLAT'
        return 'DOWN'

    def _sina():
        df = ak.stock_sector_spot()
        if df is not None and not df.empty:
            trends = {}
            for _, row in df.iterrows():
                pct = float(row['涨跌幅'])
                trends[str(row['板块'])] = {
                    'pct': round(pct, 2),
                    'trend': _classify(pct),
                    'lead_stock': str(row.get('股票名称', ''))
                }
            return trends
        return None

    def _em():
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty:
            trends = {}
            for _, row in df.iterrows():
                pct = float(row['涨跌幅'])
                trends[str(row['板块名称'])] = {
                    'pct': round(pct, 2),
                    'trend': _classify(pct),
                    'lead_stock': str(row.get('领涨股票', ''))
                }
            return trends
        return None

    result = resilient_fetch([_sina, _em], timeout=10, label="sector_trends")
    if result:
        set_cached_data('sector_trends', result)
        logger.info(f"Sector trends loaded: {len(result)} sectors, "
                    f"LEAD={sum(1 for v in result.values() if v['trend']=='LEAD')}, "
                    f"DOWN={sum(1 for v in result.values() if v['trend']=='DOWN')}")
        return result
    
    # 兜底过期缓存
    if 'sector_trends' in CACHE:
        return CACHE['sector_trends'][0]
    return {}

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

def get_tool_trade_date_hist() -> pd.DataFrame:
    """
    用不触发 V8/py_mini_racer 崩溃的容灾方式获取 A 股交易日历列表
    """
    # 1. 优先使用数据库中已有的历史日期作为交易日历，极速且 100% 安全
    try:
        from core.db import get_db_engine
        engine = get_db_engine()
        if engine:
            with engine.connect() as conn:
                res = conn.execute(text("SELECT DISTINCT date FROM daily_k ORDER BY date ASC")).fetchall()
                if res:
                    dates = [row[0] for row in res]
                    dates = [pd.to_datetime(d).strftime("%Y-%m-%d") for d in dates]
                    return pd.DataFrame({"trade_date": dates})
    except Exception as e:
        logger.warning(f"Failed to fetch trade dates from local db: {e}")

    # 2. 次优先：获取上证指数 (000001) 的历史日期列表，使用 Eastmoney 接口而不使用 mini_racer/V8 引擎
    try:
        df_index = ak.index_zh_a_hist(symbol="000001", period="daily")
        if not df_index.empty and "日期" in df_index.columns:
            dates = pd.to_datetime(df_index["日期"]).dt.strftime("%Y-%m-%d").tolist()
            return pd.DataFrame({"trade_date": dates})
    except Exception as e:
        logger.warning(f"Failed to fetch trade dates via SSE Index daily hist: {e}")

    # 3. 最后兜底 (注意: 这在多线程下可能因 V8 isolate 冲突导致崩溃，作为最末端手段并加入异常保护)
    try:
        df_sina = ak.tool_trade_date_hist_sina()
        if not df_sina.empty:
            dates = pd.to_datetime(df_sina["trade_date"]).dt.strftime("%Y-%m-%d").tolist()
            return pd.DataFrame({"trade_date": dates})
    except Exception as e:
        logger.error(f"Ultimate fallback tool_trade_date_hist_sina also failed: {e}")
        
    # 4. 如果真的都没有，返回最近365天的日期（剔除周末）
    dates = []
    curr = datetime.now() - timedelta(days=365)
    end = datetime.now() + timedelta(days=1)
    while curr < end:
        if curr.weekday() < 5:
            dates.append(curr.strftime("%Y-%m-%d"))
        curr += timedelta(days=1)
    return pd.DataFrame({"trade_date": dates})

def get_sentiment_history(days: int = 10) -> List[Dict[str, Any]]:
    """
    获取过去 N 个交易日的市场情绪历史（涨跌停家数）
    """
    cache_key = f'sentiment_history_{days}'
    cached = get_cached_data(cache_key, 3600)  # 缓存 1 小时
    if cached is not None:
        return cached

    try:
        # 获取交易日历
        trade_dates_df = get_tool_trade_date_hist()
        trade_dates_df['trade_date'] = pd.to_datetime(trade_dates_df['trade_date'])
        
        # 获取最近的交易日
        today = datetime.now()
        past_dates = trade_dates_df[trade_dates_df['trade_date'] <= today]['trade_date'].tolist()
        target_dates = [d.strftime("%Y%m%d") for d in past_dates[-days:]]
        
        results = []
        
        def fetch_day_sentiment(date_str):
            for attempt in range(3):
                try:
                    # 增加随机延迟，避免并发撞击
                    time.sleep(random.uniform(0.2, 0.8) * (attempt + 1))
                    
                    # 涨停 (EM 接口)
                    zt_df = ak.stock_zt_pool_em(date=date_str)
                    up_count = len(zt_df) if zt_df is not None and not zt_df.empty else 0
                    
                    # 跌停 (EM 接口)
                    dt_df = ak.stock_dt_pool_em(date=date_str)
                    down_count = len(dt_df) if dt_df is not None and not dt_df.empty else 0
                    
                    return {
                        "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
                        "up": up_count,
                        "down": down_count
                    }
                except Exception as e:
                    if attempt < 2:
                        logger.debug(f"Retry {attempt+1} for sentiment date {date_str} due to: {e}")
                        continue
                    logger.debug(f"Final failure fetching sentiment for {date_str}: {e}")
                    return None

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_date = {executor.submit(fetch_day_sentiment, d): d for d in target_dates}
            for future in as_completed(future_to_date):
                res = future.result()
                if res:
                    results.append(res)
        
        # 按日期排序
        results.sort(key=lambda x: x['date'])
        
        if results:
            set_cached_data(cache_key, results)
            
        return results
    except Exception as e:
        logger.error(f"Error in get_sentiment_history: {e}")
        return []
