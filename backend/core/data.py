import akshare as ak
import pandas as pd
import time
import random
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from .db import save_to_db, get_db_engine, save_stock_basic, get_stock_basic_map
import os

# 记录原始代理环境变量以便按需恢复
ORIGINAL_PROXIES = {
    var: os.environ.get(var) 
    for var in ["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"]
}

def force_direct_connection():
    proxy_vars = ["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"]
    for var in proxy_vars:
        if var in os.environ:
            del os.environ[var]
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"

def restore_proxies():
    for var, val in ORIGINAL_PROXIES.items():
        if val:
            os.environ[var] = val
        elif var in os.environ:
            del os.environ[var]

# 模块导入时立即执行
force_direct_connection()

# --- 智能断路器 & 状态记忆 ---
# 记录哪些接口已被证实 SSL 拦截，直接跳过首选源进入降级源
PREFERRED_SOURCES = {} 
# 记录哪些失败已经打印过日志，避免 5000+ 股票同步时刷屏
FAILED_SOURCES_LOGGED = set()

def safe_ak_call(func_name, **kwargs):
    """Safely call an akshare function with retries, headers, and automatic source learning."""
    import akshare as ak
    import time
    
    # 每次调用强制确保清理残留代理（极其重要）
    force_direct_connection()
    
    # 指令降级映射表
    SINA_FALLBACK_MAP = {
        "stock_zh_a_hist": "stock_zh_a_daily",
        "stock_zh_a_spot_em": "stock_zh_a_spot",
        "stock_zh_index_spot_em": "stock_zh_index_daily",
        "stock_board_industry_name_em": "stock_board_industry_name_ths"
    }
    
    # --- 1. 检查断路器记忆 ---
    is_learned_fallback = PREFERRED_SOURCES.get(func_name) is True
    
    max_retries = 2
    for attempt in range(max_retries):
        current_func_name = func_name
        current_kwargs = kwargs.copy()
        
        # 如果已经学习到需要降级，或者这是重试且存在降级逻辑
        should_use_fallback = is_learned_fallback or (attempt > 0 and func_name in SINA_FALLBACK_MAP)
        
        if should_use_fallback and func_name in SINA_FALLBACK_MAP:
            current_func_name = SINA_FALLBACK_MAP[func_name]
            # --- 参数适配器 ---
            # 1. Sina 个股历史
            if current_func_name == "stock_zh_a_daily":
                code = current_kwargs.get("symbol", "")
                if code.isdigit():
                    if code.startswith("6"): current_kwargs["symbol"] = f"sh{code}"
                    else: current_kwargs["symbol"] = f"sz{code}"
                keys_to_remove = ["period", "adjust", "start_date", "end_date"]
                for k in keys_to_remove:
                    if k in current_kwargs: del current_kwargs[k]
                    
            # 2. Sina 指数快照
            elif current_func_name == "stock_zh_index_daily":
                # 指数快照通常是批量获取，但 Sina 这里是单个。我们暂时降级为上证综指作为代表
                current_kwargs["symbol"] = "sh000001"
                for k in list(current_kwargs.keys()):
                    if k != "symbol": del current_kwargs[k]
            
            # 3. Sina 全市场快照 (stock_zh_a_spot)
            elif current_func_name == "stock_zh_a_spot":
                # 不需要特殊参数
                pass
            
            if attempt > 0 or not is_learned_fallback:
                # 只有还没学习时才打印 fallback 尝试
                if not is_learned_fallback:
                    print(f"🔄 Attempting fallback: {func_name} -> {current_func_name} for {current_kwargs.get('symbol', 'batch')}")

        try:
            func = getattr(ak, current_func_name)
            
            # 使用线程池实现硬超时控制 (15秒)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func, **current_kwargs)
                try:
                    result = future.result(timeout=15)
                    
                    # --- 2. 学习成功路径 ---
                    # 如果降级源成功了，记录下来，下次直接走降级
                    if should_use_fallback and not is_learned_fallback and result is not None and not (isinstance(result, pd.DataFrame) and result.empty):
                        # 再次检查 key 是否已存在，避免并发打印
                        if func_name not in PREFERRED_SOURCES:
                            PREFERRED_SOURCES[func_name] = True
                            print(f"💡 Learned: Source '{func_name}' is blocked, sticking to fallback '{current_func_name}' for this session.")
                    
                    # --- 3. 结果格式化适配 (如果是降级源返回的) ---
                    if should_use_fallback and isinstance(result, pd.DataFrame):
                        # A. Sina 全市场快照适配
                        if current_func_name == "stock_zh_a_spot":
                            mapping = {
                                'code': '代码', 'name': '名称', 'trade': '最新价',
                                'changepercent': '涨跌幅', 'pricechange': '涨跌额',
                                'volume': '成交量', 'amount': '成交额',
                                'high': '最高', 'low': '最低', 'open': '今开',
                                'settlement': '昨收', 'mktcap': '总市值', 'nmc': '流通市值',
                                'turnoverratio': '换手率', 'per': '市盈率-动态', 'pb': '市净率'
                            }
                            # 重命名存在的列
                            existing_mapping = {k: v for k, v in mapping.items() if k in result.columns}
                            result = result.rename(columns=existing_mapping)
                            # Sina 总市值单位是万元，EM 是元，这里需要同步
                            if '总市值' in result.columns:
                                result['总市值'] = result['总市值'] * 10000
                            if '流通市值' in result.columns:
                                result['流通市值'] = result['流通市值'] * 10000
                        
                        # B. THS 行业板块适配
                        elif current_func_name == "stock_board_industry_name_ths":
                            mapping = {'name': '板块名称', 'code': '板块代码'}
                            result = result.rename(columns=mapping)
                            if 'pct' not in result.columns and '涨跌幅' not in result.columns:
                                result['涨跌幅'] = 0.0 # THS 接口可能不带涨跌幅，补充默认值以免崩掉
                    
                    return result
                except Exception as e:
                    # 如果是超时，抛出特定的错误以便后续处理
                    if "TimeoutError" in str(type(e)):
                        raise TimeoutError(f"Function {current_func_name} timed out after 15s")
                    raise e
            
        except Exception as e:
            err_msg = str(e)
            
            # --- 3. 智能日志抑制 ---
            # 情况 A: SSL 阻断/连接中断
            is_ssl_issue = any(x in err_msg for x in ["RemoteDisconnected", "Connection aborted", "Max retries exceeded", "SSL"])
            
            log_key = f"{current_func_name}_{is_ssl_issue}"
            if log_key not in FAILED_SOURCES_LOGGED:
                print(f"⚠️ safe_ak_call '{current_func_name}' failed: {err_msg}")
                FAILED_SOURCES_LOGGED.add(log_key)
            
            if attempt < max_retries - 1:
                if is_ssl_issue:
                    if func_name not in FAILED_SOURCES_LOGGED:
                        print("💡 SSL/Connection issue detected, recording fault and triggering fallback...")
                        FAILED_SOURCES_LOGGED.add(func_name)
                
                # 第二次尝试时尝试恢复代理，万一真的是必须代理
                restore_proxies()
                time.sleep(random.uniform(0.1, 0.4))
                continue
            
            # 彻底端掉
            if "RemoteDisconnected" in err_msg or "Connection aborted" in err_msg:
                if "final_ssl_fail" not in FAILED_SOURCES_LOGGED:
                    print("❌ 网络握手持续失败。建议安装 Homebrew Python 以获得真正的 SSL 支持。")
                    FAILED_SOURCES_LOGGED.add("final_ssl_fail")
            
            raise e

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
    """获取全市场实时快照 (v5.1 - 使用 safe_ak_call)"""
    try:
        df = safe_ak_call("stock_zh_a_spot_em")
        if df.empty: return df
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
        print(f"❌ Error fetching snapshot: {e}")
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
    """获取主要指数实时行情 (使用极速快照接口)"""
    cached = get_cached_data('index_data', 30) # 缩短缓存到30秒
    if cached: return cached

    indices_names = {
        "000001": "上证",
        "399006": "创业板",
        "000300": "沪深300",
        "000688": "科创50",
        "000852": "中证1000"
    }
    
    try:
        # 使用东财指数快照，极速 (如果 SSL 拦截，将通过 safe_ak_call 自动降级或报错)
        df = safe_ak_call("stock_zh_index_spot_em")
        if df is not None and not df.empty:
            res = {}
            # 情况 A: 东财快照成功
            if '代码' in df.columns:
                for _, row in df.iterrows():
                    code = row['代码']
                    if code in indices_names:
                        res[indices_names[code]] = {
                            'price': float(row['最新价']),
                            'pct': float(row.get('涨跌幅', 0))
                        }
            # 情况 B: Sina 历史快照降级 (safe_ak_call 内部重定向)
            elif 'close' in df.columns or '收盘' in df.columns:
                # 此时 df 通常是单个指数的历史记录 (如 sh000001)
                curr = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else curr
                close_val = float(curr.get('close', curr.get('收盘', 0)))
                prev_close = float(prev.get('close', prev.get('收盘', 0)))
                pct = ((close_val - prev_close) / prev_close * 100) if prev_close != 0 else 0
                res["上证"] = {'price': close_val, 'pct': pct}
            
            if res:
                set_cached_data('index_data', res)
                return res
    except Exception as e:
        print(f"⚠️ Index spot fetch failed: {e}")

    # Fallback to Sina (only if spot fails)
    res = {}
    sina_map = {"000001": "sh000001", "399006": "sz399006"}
    for name, code in [("上证", "000001"), ("创业板", "399006")]:
        try:
            sina_symbol = sina_map.get(code)
            if sina_symbol:
                df = safe_ak_call("stock_zh_index_daily", symbol=sina_symbol)
                if not df.empty:
                    curr = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else curr
                    pct = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
                    res[name] = {'price': float(curr['收盘']), 'pct': float(pct)}
        except: pass
    return res

def get_hot_sectors():
    """获取热门行业板块指数 (缓存 10 分钟)"""
    cached = get_cached_data('hot_sectors', 600)
    if cached: return cached

    # 减少重试次数，尝试极速接口
    try:
        # 尝试使用股票热度接口作为行业热度的快照
        df = safe_ak_call("stock_board_industry_name_em")
        if df is not None and not df.empty:
            # 兼容不同接口的列名
            pct_col = '涨跌幅' if '涨跌幅' in df.columns else (df.columns[2] if len(df.columns) > 2 else None)
            name_col = '板块名称' if '板块名称' in df.columns else ('name' if 'name' in df.columns else df.columns[0])
            
            df_sorted = df.sort_values(pct_col, ascending=False).head(5) if pct_col else df.head(5)
            hot_sectors = []
            for _, row in df_sorted.iterrows():
                hot_sectors.append({
                    'name': str(row[name_col]),
                    'pct': float(row[pct_col]) if pct_col and pct_col in row else 0.0,
                    'lead': str(row.get('领涨股票', ''))
                })
            set_cached_data('hot_sectors', hot_sectors)
            return hot_sectors
    except Exception as e:
        print(f"⚠️ Hot sectors fetch failed: {e}")
            
    # 如果极速接口失败，尝试从本地缓存或返回空
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
        df_board = safe_ak_call("stock_board_industry_name_em")
        if df_board.empty: return {}
        
        # 优化：只拉取前 50 个核心板块作为背景缓存
        all_boards = df_board['板块名称'].head(50).tolist()
        
        # 2. 并发抓取成分股
        def fetch_sector_with_retry(sector_name, retries=3):
            for i in range(retries):
                try:
                    time.sleep(random.uniform(0.5, 1.0))
                    df_curr = safe_ak_call("stock_board_industry_cons_em", symbol=sector_name)
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

def get_index_hist(symbol):
    """获取主要指数历史用于 RS 计算 (优先新浪 fallback)"""
    cache_key = f'index_hist_{symbol}'
    cached = get_cached_data(cache_key, 3600)
    if cached is not None: return cached

    sina_map = {
        "000001": "sh000001",
        "399006": "sz399006",
        "000300": "sh000300",
        "000688": "sh000688",
        "000852": "sh000852"
    }

    try:
        # 1. 优先尝试 Sina 接口
        sina_symbol = sina_map.get(symbol)
        if sina_symbol:
            df = safe_ak_call("stock_zh_index_daily", symbol=sina_symbol)
            if not df.empty:
                df = df.rename(columns={
                    'date': '日期', 'open': '开盘', 'high': '最高', 'low': '最低', 
                    'close': '收盘', 'volume': '成交量'
                })
                # 确保日期列是字符串 YYYY-MM-DD
                if '日期' in df.columns:
                    df['日期'] = pd.to_datetime(df['日期']).dt.strftime('%Y-%m-%d')
                set_cached_data(cache_key, df)
                return df

        # 2. Fallback to EastMoney
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        df = safe_ak_call("index_zh_a_hist", symbol=symbol, period="daily", start_date=start_date)
        if not df.empty:
             set_cached_data(cache_key, df)
        return df
    except Exception as e:
        print(f"❌ Error fetching index hist for {symbol}: {e}")
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
