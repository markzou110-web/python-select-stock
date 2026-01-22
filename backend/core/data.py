import akshare as ak
import pandas as pd
import time
import random
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from .db import save_to_db, get_db_engine, save_stock_basic, get_stock_basic_map

# --- Simple Time-based Cache ---
CACHE = {}

def get_cached_data(key, ttl_seconds):
    if key in CACHE:
        data, timestamp = CACHE[key]
        if time.time() - timestamp < ttl_seconds:
            return data
    return None

def set_cached_data(key, data):
    CACHE[key] = (data, time.time())

def get_market_snapshot():
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
                print(f"⚠️ Snapshot fetch failed (attempt {attempt+1}), retrying in {wait_time}s... Error: {e}")
                time.sleep(wait_time)
                continue
            print(f"❌ Error fetching snapshot after {max_retries} attempts: {e}")
            return pd.DataFrame()

def sync_stock(code, name, engine=None):
    """同步单只股票的缺失数据"""
    from sqlalchemy import text
    if engine is None:
        engine = get_db_engine()
    if not engine: return False
    try:
        # 获取最新日期
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT MAX(date) FROM daily_k WHERE code='{code}'"))
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
        with open("sync_error.log", "a") as f:
            f.write(f"[{datetime.now()}] sync_stock Error ({code}): {str(e)}\n")
        return False

def get_index_data():
    """获取主要指数实时行情 (并发拉取 + 缓存)"""
    cached = get_cached_data('index_data', 60)
    if cached: return cached

    indices = {
        "上证": "000001", 
        "创业板": "399006", 
        "沪深300": "000300", 
        "科创50": "000688", 
        "中证1000": "000852"
    }
    
    def fetch_one_with_retry(name, code, retries=3):
        for i in range(retries):
            try:
                # 策略：首推快速超时 (5s)，失败后再用长超时 (10s)
                to = 5 if i == 0 else 10
                # 给底层 akshare 增加环境超时，如果底层不支持，外层 ThreadPoolExecutor 会切断
                df = ak.index_zh_a_hist(symbol=code, period="daily", 
                                       start_date=(datetime.now() - timedelta(days=10)).strftime("%Y%m%d"))
                if not df.empty:
                    curr = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else curr
                    pct = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
                    return name, {'price': curr['收盘'], 'pct': pct}
            except Exception as e:
                if i < retries - 1:
                    time.sleep(random.uniform(0.5, 1.5)) # 避峰重试
                else:
                    print(f"❌ Index fetch totally failed for {name} after {retries} attempts: {e}")
        return name, None

    res = {}
    # 降低并发度，减少 EastMoney 连通重置风险
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(fetch_one_with_retry, name, code) for name, code in indices.items()]
        for future in futures:
            try:
                # 总执行过程超时设长，允许内部重试耗时
                name, data = future.result(timeout=40)
                if data: res[name] = data
            except:
                pass
    
    if res: set_cached_data('index_data', res)
    return res

def get_hot_sectors():
    """获取热门行业板块指数 (缓存 10 分钟)"""
    cached = get_cached_data('hot_sectors', 600)
    if cached: return cached

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
        except:
            if i < 2: time.sleep(1)
            
    return []

def get_sector_map():
    """获取全市场个股行业映射 (重量级操作，优先读取数据库)"""
    # 1. 内存缓存
    cached = get_cached_data('sector_map', 86400)
    if cached: return cached

    # 2. 数据库缓存 (可靠性保障)
    db_map = get_stock_basic_map()
    if db_map:
        set_cached_data('sector_map', db_map)
        return db_map

    print("🏗️ Building sector map from API and persisting to DB...")
    sector_map = {}
    try:
        # 1. 获取所有行业板块名称
        df_board = ak.stock_board_industry_name_em()
        if df_board.empty: return {}
        
        # 优化：只拉取前 50 个核心板块作为背景缓存
        all_boards = df_board['板块名称'].head(50).tolist()
        
        # 2. 并发抓取成分股
        def fetch_sector_with_retry(sector_name, retries=3):
            for i in range(retries):
                try:
                    time.sleep(random.uniform(0.5, 1.0))
                    df_curr = ak.stock_board_industry_cons_em(symbol=sector_name)
                    if not df_curr.empty:
                        return sector_name, df_curr[['代码', '名称']].copy()
                except:
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
                except:
                    continue
        
        # 3. 持久化到数据库
        if all_basic_data:
            df_basic = pd.DataFrame(all_basic_data)
            save_stock_basic(df_basic)
            
        if sector_map: 
            set_cached_data('sector_map', sector_map)
            print(f"✅ Full sector map built and persisted: {len(sector_map)} stocks mapped.")
        return sector_map
    except Exception as e:
        print(f"❌ Critical error in get_sector_map: {e}")
        return {}

def get_index_hist(code):
    """获取指数历史用于基准计算 (缓存 24 小时)"""
    cache_key = f'index_hist_{code}'
    cached = get_cached_data(cache_key, 86400)
    if cached is not None: return cached

    try:
        df = ak.index_zh_a_hist(symbol=code, period="daily")
        if not df.empty:
            set_cached_data(cache_key, df)
        return df
    except:
        return pd.DataFrame()

def get_northbound_flow(code=None, days=3):
    """获取北向资金流向数据

    Args:
        code: 股票代码，如果为None则返回全市场北向资金流向
        days: 查询天数 (默认3天)

    Returns:
        dict: {
            'net_flow': 净流入金额 (亿元),
            'trend': '流入' or '流出',
            'recent_data': 最近几天的数据列表
        }
    """
    cache_key = f'northbound_{code if code else "market"}_{days}'
    cached = get_cached_data(cache_key, 1800)  # 缓存30分钟
    if cached: return cached

    try:
        # 获取个股北向资金数据
        if code:
            # 个股北向资金历史数据
            for attempt in range(2):
                try:
                    time.sleep(random.uniform(0.3, 0.8))
                    df = ak.stock_hsgt_individual_em(symbol=code)
                    # 个股北向资金使用“今日增持资金”(元)
                    if df is not None and not df.empty and '今日增持资金' in df.columns:
                        # 取最近 days 天的数据
                        df_recent = df.head(days)

                        # 计算累计净流入 (转换为万元)
                        total_flow = df_recent['今日增持资金'].fillna(0).sum() / 10000.0

                        # 判断趋势
                        trend = '流入' if total_flow > 0 else '流出' if total_flow < 0 else '持平'

                        result = {
                            'net_flow': abs(round(total_flow, 2)),
                            'trend': trend,
                            'recent_data': df_recent.to_dict('records')[:3]
                        }
                        set_cached_data(cache_key, result)
                        return result
                    else:
                        # Stock not in northbound program or no data available
                        break
                except Exception as e:
                    if attempt < 1:
                        time.sleep(1)
                        continue
                    # Check if it's the known akshare bug for stocks not in northbound program
                    if "'NoneType' object is not subscriptable" in str(e):
                        # Stock not in northbound program - silent skip
                        break
                    print(f"⚠️ Northbound data fetch failed for {code}: {e}")
                    break

            # 返回默认值
            return {
                'net_flow': 0,
                'trend': '---',
                'recent_data': []
            }
        else:
            # 全市场北向资金流向 (沪深股通)
            for attempt in range(2):
                try:
                    time.sleep(random.uniform(0.3, 0.8))
                    df = ak.stock_hsgt_hist_em(symbol="北向资金")
                    if df is not None and not df.empty and '当日成交净买额' in df.columns:
                        df_recent = df.head(days)

                        # 计算累计净流入 (亿元)
                        total_flow = df_recent['当日成交净买额'].sum()

                        trend = '流入' if total_flow > 0 else '流出' if total_flow < 0 else '持平'

                        result = {
                            'net_flow': abs(round(total_flow, 2)),
                            'trend': trend,
                            'recent_data': df_recent.to_dict('records')[:3]
                        }
                        set_cached_data(cache_key, result)
                        return result
                except Exception as e:
                    if attempt < 1:
                        time.sleep(1)
                        continue
                    print(f"⚠️ Market northbound flow fetch failed: {e}")
                    break

            return {
                'net_flow': 0,
                'trend': '---',
                'recent_data': []
            }
    except Exception as e:
        print(f"❌ Error in get_northbound_flow: {e}")
        return {
            'net_flow': 0,
            'trend': '---',
            'recent_data': []
        }
