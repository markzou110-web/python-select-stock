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
from .risk_constants import STALE_SNAPSHOT_WARN, FRESHNESS_WARN_THRESHOLD_MIN

# P1：记录最近一次成功的快照来源（在 _fetch_snapshot_* 内赋值），供 attrs.source 使用。
# 模块级而非函数内，便于各内嵌 _fetch 函数写入。
_last_snapshot_source: str = "unknown"


# ═══════════════════════════════════════════════════════
_resilient_executor = ThreadPoolExecutor(max_workers=10)

def resilient_fetch(fetch_funcs: List[Callable], timeout: int = 8, label: str = "data", min_rows: int = 0):
    """
    按优先级依次尝试多个数据源函数，任何一个成功即返回结果。
    所有调用强制 timeout 秒超时，防止 akshare 底层挂起。
    
    Args:
        fetch_funcs: 一组 callable，按优先级排列
        timeout: 每个源的最大等待秒数
        label: 日志标签
        min_rows: DataFrame 结果的最小行数阈值，低于此值视为残缺数据并继续降级。
                  防止数据源被限流后返回截断结果(如东财返回100行)造成"假成功"。
                  仅对 pd.DataFrame 结果生效，默认 0 表示不校验。
    Returns:
        第一个成功函数的返回值，或 None
    """
    for i, func in enumerate(fetch_funcs):
        source_name = getattr(func, '__name__', f'source_{i}')
        # 每个源内部尝试 2 次
        for attempt in range(2):
            try:
                future = _resilient_executor.submit(func)
                result = future.result(timeout=timeout)
                if result is not None and not (isinstance(result, pd.DataFrame) and result.empty):
                    # 行数完整性校验:防止被限流的数据源返回截断数据(如东财返回100行)
                    # 被误判为成功而阻断降级
                    if min_rows > 0 and isinstance(result, pd.DataFrame) and len(result) < min_rows:
                        logger.warning(
                            f"[{label}] {source_name} returned incomplete data: "
                            f"{len(result)} rows < {min_rows} required (attempt {attempt+1})"
                        )
                        if attempt == 0:
                            time.sleep(1)
                        continue  # 行数不足，视为失败，继续尝试下一源/重试
                    return result
            except FuturesTimeoutError:
                logger.warning(f"[{label}] {source_name} timed out after {timeout}s (attempt {attempt+1})")
            except Exception as e:
                logger.debug(f"[{label}] {source_name} failed (attempt {attempt+1}): {str(e)[:80]}")
            
            if attempt == 0:
                time.sleep(1) # 重试间隔
                
    logger.warning(f"[{label}] All {len(fetch_funcs)} sources failed after multiple attempts.")
    return None


def _fetch_limit_down_count() -> Optional[int]:
    """获取当日 A 股跌停家数（用于市场宽度降级）。

    走 akshare 东财跌停池接口，带 8s 超时容错。盘后/非交易日接口仍返回当日数据；
    盘中返回实时跌停家数。失败返回 None，调用方退回纯指数趋势判定（不阻断）。
    """
    def _fetch_ztpool():
        today = datetime.now().strftime('%Y%m%d')
        df = ak.stock_zt_pool_dtgc_em(date=today)
        if df is not None and not df.empty:
            return df
        return None
    try:
        df = resilient_fetch([_fetch_ztpool], timeout=8, label="limit_down")
        if df is not None and not df.empty:
            return int(len(df))
        return None
    except Exception as e:
        logger.debug(f"limit_down count fetch failed: {e}")
        return None


def get_market_regime() -> Dict[str, Any]:
    """
    获取大盘环境：结合上证指数 (000001) 和 创业板指 (399006)
    """
    indices_tx = {"sh000001": "上证", "sz399006": "创业"}
    realtime_index_alias = {"上证": "上证", "创业": "创业板"}
    realtime_indices = {}
    states = {}
    
    try:
        try:
            realtime_indices = get_index_data()
        except Exception as exc:
            logger.debug(f"Realtime index quote unavailable for regime wording: {exc}")

        for code, name in indices_tx.items():
            def _fetch_sina(c=code):
                # 新浪指数日线在海外IP仅需 0.1~0.3 秒，极其稳定且数据完整
                return ak.stock_zh_index_daily(symbol=c)
            def _fetch_tx(c=code):
                return ak.stock_zh_index_daily_tx(symbol=c)
            def _fetch_em(c=code.replace('sh','').replace('sz','')):
                now = datetime.now()
                return ak.index_zh_a_hist(symbol=c, period="daily",
                    start_date=(now - timedelta(days=60)).strftime('%Y%m%d'),
                    end_date=now.strftime('%Y%m%d'))
            
            # 优先使用极其快速稳定的新浪源
            df = resilient_fetch([_fetch_sina, _fetch_tx, _fetch_em], timeout=15, label=f"regime_{name}")
            if df is None or df.empty:
                continue
            
            # 统一列名：新浪源和腾讯源列名是 close，东财源是 收盘
            close_col = 'close' if 'close' in df.columns else '收盘'
            df_regime = pd.DataFrame({'收盘': df[close_col].values})
            df_regime = calculate_ema(df_regime, 20)

            latest = df_regime.iloc[-1]
            close = float(latest['收盘'])
            ema20 = float(latest['EMA20'])
            # 当日涨跌幅：相对前一日收盘。用于让推送文案对单日急跌有感知（不改 regime 判定本身）。
            chg_pct = None
            if len(df_regime) >= 2:
                prev_close = float(df_regime.iloc[-2]['收盘'])
                if prev_close > 0:
                    chg_pct = round((close - prev_close) / prev_close * 100, 2)

            # 盘中 Bark 文案必须使用实时指数涨跌。历史日线源在收盘前常只返回
            # 上一交易日，导致 14:50 仍显示昨天跌幅；EMA20 趋势仍沿用日线序列。
            realtime_quote = realtime_indices.get(realtime_index_alias.get(name, name), {})
            if realtime_quote:
                close = float(realtime_quote.get("price") or close)
                chg_pct = round(float(realtime_quote.get("pct")), 2) if realtime_quote.get("pct") is not None else chg_pct

            states[name] = {
                "close": round(close, 2),
                "ema20": round(ema20, 2),
                "trend": "BULL" if close > ema20 else "BEAR",
                "chg_pct": chg_pct,
            }
        
        if "上证" not in states or "创业" not in states:
            return {"status": "UNKNOWN", "desc": "数据获取失败", "indices": states, "updated_at": "", "limit_down_count": None}

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

        # 市场宽度降级：指数趋势是慢变量，对"指数被权重股托住但个股大面积跌停"
        # 的结构性行情失明。读取实时跌停家数，达到阈值时强制降级（只降不升），
        # 联动收紧扫描阈值/下调仓位上限/触发减仓。失败返回 None 不阻断（退回纯指数判定）。
        limit_down_count = _fetch_limit_down_count()
        if isinstance(limit_down_count, int):
            from core.risk_constants import BREADTH_DOWNGRADE_DEFENSIVE, BREADTH_DOWNGRADE_CRITICAL
            if limit_down_count >= BREADTH_DOWNGRADE_CRITICAL and status != "CRITICAL":
                status = "CRITICAL"
                desc = f"宽度降级·空仓防守：跌停 {limit_down_count} 家（指数失真，个股恐慌扩散）"
            elif limit_down_count >= BREADTH_DOWNGRADE_DEFENSIVE and status == "OFFENSIVE":
                status = "DEFENSIVE"
                desc = f"宽度降级·减仓观望：跌停 {limit_down_count} 家（指数失真，市场宽度恶化）"

        return {
            "status": status,
            "desc": desc,
            "indices": states,
            "limit_down_count": limit_down_count,
            "updated_at": datetime.now().strftime('%H:%M:%S')
        }
    except Exception as e:
        logger.error(f"Error getting market regime: {e}")
        return {"status": "UNKNOWN", "desc": "数据获取失败", "indices": {}, "limit_down_count": None, "updated_at": ""}

# --- 统一根据配置禁用代理 ---
from core.config import config
config.setup_no_proxy()

# --- Bounded TTL Cache with auto-eviction ---
import threading

_CACHE_MAX_SIZE = 128
CACHE: Dict[str, Tuple[Any, float]] = {}
_cache_lock = threading.Lock()


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
    with _cache_lock:
        if key in CACHE:
            data, timestamp = CACHE[key]
            if time.time() - timestamp < ttl_seconds:
                return data
            del CACHE[key]
        return None


def set_cached_data(key: str, data: Any) -> None:
    """Store data in cache with current timestamp. Evicts expired entries when full."""
    with _cache_lock:
        if len(CACHE) >= _CACHE_MAX_SIZE:
            _evict_expired()
        if len(CACHE) >= _CACHE_MAX_SIZE:
            oldest_key = min(CACHE, key=lambda k: CACHE[k][1])
            del CACHE[oldest_key]
        CACHE[key] = (data, time.time())


def get_stale_cache(key: str) -> Optional[Any]:
    """Get cached data regardless of TTL (for fallback use when fresh fetch fails)."""
    with _cache_lock:
        if key in CACHE:
            return CACHE[key][0]
        return None


def snapshot_data_date(snapshot: Any) -> Optional[str]:
    """Return the explicit quote date carried by a market snapshot."""
    attrs = getattr(snapshot, "attrs", {}) or {}
    value = attrs.get("data_date")
    if value:
        return str(value)[:10]
    fetched_at = attrs.get("fetched_at")
    if isinstance(fetched_at, datetime):
        return fetched_at.strftime("%Y-%m-%d")
    return None


def snapshot_matches_date(snapshot: Any, expected_date: str) -> bool:
    """Reject cross-day quote reuse when a caller requires one trading date."""
    if snapshot is None or (hasattr(snapshot, "empty") and snapshot.empty):
        return False
    return snapshot_data_date(snapshot) == str(expected_date)[:10]


def _expected_snapshot_date(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    try:
        from core.trading_calendar import is_a_share_trading_day
        if is_a_share_trading_day(now):
            return now.strftime("%Y-%m-%d")
        dates = get_tool_trade_date_hist()
        parsed = pd.to_datetime(dates.get("trade_date"), errors="coerce").dropna()
        parsed = parsed[parsed <= now]
        if not parsed.empty:
            return parsed.max().strftime("%Y-%m-%d")
    except Exception as exc:
        logger.debug(f"Expected snapshot date fallback failed: {exc}")
    return now.strftime("%Y-%m-%d")


def _infer_snapshot_data_date(snapshot: pd.DataFrame, fetched_at: datetime) -> str:
    if "quote_time" in snapshot.columns:
        values = snapshot["quote_time"].dropna().astype(str).str.extract(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})")
        values = values.dropna()
        if not values.empty:
            dates = values.agg("-".join, axis=1)
            if not dates.empty:
                return str(dates.mode().iloc[0])
    return _expected_snapshot_date(fetched_at)

def get_market_snapshot(force_refresh: bool = False) -> pd.DataFrame:
    """获取全市场实时快照 (v6.0 - 引入 resilient_fetch 多源容灾弹性重构)"""
    # P0：在 get_cached_data 调用前先持有 stale 引用。因为 get_cached_data 命中过期条目时
    # 会 del CACHE[key]，导致后续 get_stale_cache 取不到全失败兜底用的旧快照。
    # 提前持有引用可避免这个时序问题，且对新启动（无缓存）场景无副作用。
    expected_date = _expected_snapshot_date()
    stale_fallback = get_stale_cache('market_snapshot')
    if stale_fallback is not None and not snapshot_matches_date(stale_fallback, expected_date):
        logger.warning(
            "Ignoring cross-day stale market snapshot: expected=%s actual=%s",
            expected_date,
            snapshot_data_date(stale_fallback) or "unknown",
        )
        stale_fallback = None
    # 增加 60 秒的高速缓存，防止双策略或并发扫描时频繁高负荷请求
    cached = None if force_refresh else get_cached_data('market_snapshot', 60)
    if cached is not None and snapshot_matches_date(cached, expected_date):
        logger.info("Using cached market snapshot data.")
        return cached

    def _fetch_snapshot_em():
        global _last_snapshot_source
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
            '最高': 'high',
            '最低': 'low',
            '涨跌幅': 'pct_chg',
            '成交量': 'vol',
            '换手率': 'turnover',
            '总市值': 'mkt_cap',
            '市盈率-动态': 'pe'
        })
        _last_snapshot_source = "akshare东财"
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
            '最高': 'high',
            '最低': 'low',
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
        cols = ['code', 'name', 'price', 'open', 'high', 'low', 'pct_chg', 'vol', 'turnover', 'mkt_cap', 'pe']
        df = df[cols]
        return df

    def _fetch_snapshot_tencent():
        """使用腾讯行情API，并发分批抓取全市场快照"""
        global _last_snapshot_source
        basic_map = get_stock_basic_map()
        codes = list(basic_map.keys())
        if not codes:
            raise ValueError("Tencent fetch aborted: Local database stock_basic table has no codes.")
        
        logger.info(f"Tencent snapshot fetch triggered for {len(codes)} stocks.")
        
        def fetch_tencent_chunk(chunk_codes):
            try:
                from .direct_sources import tencent_quote

                quotes = tencent_quote(chunk_codes)
                results = []
                for code, quote in quotes.items():
                    mcap_yi = quote.get("mcap_yi")
                    float_mcap_yi = quote.get("float_mcap_yi")
                    # get_stock_basic_map() 的正式契约是 {code: industry}；测试和
                    # 少量旧调用也可能传入字典值，兼容两者但不改变返回结构。
                    basic = basic_map.get(code)
                    industry = basic.get("industry") if isinstance(basic, dict) else basic
                    results.append({
                        'code': code,
                        'name': quote.get("name", ""),
                        'industry': industry,
                        'quote_time': quote.get("quote_time") or "",
                        'price': quote.get("price") or None,
                        'open': quote.get("open") or None,
                        'high': quote.get("high") or quote.get("price") or None,
                        'low': quote.get("low") or quote.get("price") or None,
                        'pct_chg': quote.get("change_pct") or 0.0,
                        'vol': quote.get("vol") or 0.0,
                        'amount': (quote.get("amount_wan") or 0.0) * 10000.0,
                        'turnover': quote.get("turnover_pct") or None,
                        'mkt_cap': mcap_yi * 100000000.0 if mcap_yi else None,
                        'float_mkt_cap': float_mcap_yi * 100000000.0 if float_mcap_yi else None,
                        'pe': quote.get("pe_ttm") or None,
                        'pb': quote.get("pb") or None,
                        'limit_up': quote.get("limit_up") or None,
                        'limit_down': quote.get("limit_down") or None,
                        'vol_ratio': quote.get("vol_ratio") or None,
                        'amplitude': quote.get("amplitude_pct") or None,
                        'last_close': quote.get("last_close") or None,
                    })
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
        _last_snapshot_source = "腾讯"
        return df

    def _fetch_em_direct():
        """P2：直连东财 push2 端点（绕过 akshare wrapper 层的封禁）。
        复用 direct_sources.em_get 节流器；单次分页(pz=6000)返回全市场 11 列。"""
        global _last_snapshot_source
        from .direct_sources import snapshot_from_eastmoney
        df = snapshot_from_eastmoney()
        if df is None or df.empty:
            raise ValueError("Eastmoney direct returned empty snapshot")
        _last_snapshot_source = "东财直连"
        return df

    def _fetch_snapshot_sina_direct():
        """P2：直连新浪 hq.sinajs.cn（绕过 akshare 的 py_mini_racer V8 段错误）。
        用标准库 re 解析，无需 demjson3 依赖。第 3 独立提供商，终极兜底。"""
        global _last_snapshot_source
        from .direct_sources import snapshot_from_sina
        basic_map = get_stock_basic_map()
        codes = list(basic_map.keys())
        if not codes:
            raise ValueError("Sina direct aborted: stock_basic table has no codes.")
        df = snapshot_from_sina(codes)
        if df is None or df.empty:
            raise ValueError("Sina direct returned empty snapshot")
        _last_snapshot_source = "新浪直连"
        return df

    # 通过 resilient_fetch 容灾调用，给腾讯源充足的超时空间（35秒）
    # 注意：已移除 _fetch_snapshot_sina。新浪源 stock_zh_a_spot() 内部会实例化
    # py_mini_racer.MiniRacer()（V8 引擎），在 macOS arm64 + 多线程环境下触发
    # native crash (address_pool_manager Check failed: !pool->IsInitialized())，
    # 该段错误无法被 try/except 捕获，会杀掉整个 uvicorn 进程。
    # 该段错误根因是 akshare wrapper 内部的 py_mini_racer，不是新浪接口本身。
    # P2 新增的 _fetch_snapshot_sina_direct 用 requests 直连 hq.sinajs.cn，绕开 V8，
    # 已在 scratch/debug_sina.py 验证 arm64 安全。
    # 数据源优先级:腾讯/新浪直连(实测稳定)优先,东财(易被限流截断)降为末位兜底。
    # 2026-06 修复:东财被限流时返回截断的 100 行(应 ~5500),且 resilient_fetch
    # 旧逻辑只判 df.empty 导致"假成功"阻断降级。现腾讯/新浪前置 + min_rows 校验。
    df = resilient_fetch(
        [_fetch_snapshot_tencent, _fetch_snapshot_sina_direct, _fetch_snapshot_em, _fetch_em_direct],
        timeout=35, label="market_snapshot", min_rows=4000,
    )

    if df is not None and not df.empty:
        # 双重防御：确保 high 和 low 列始终存在，若不存在以最新价/今开填充
        if 'high' not in df.columns:
            df['high'] = df['price'] if 'price' in df.columns else df['open']
        if 'low' not in df.columns:
            df['low'] = df['price'] if 'price' in df.columns else df['open']

        # P1：在 DataFrame 上挂行情元数据，供 Bark 推送生成"⏱️ 行情 HH:MM · 源"标注。
        # 沿用 scanner.py:1246 已有的 df.attrs['data_date'] 范式（pandas 2.2.2 支持）。
        fetched_at = datetime.now()
        df.attrs = {
            'fetched_at': fetched_at,
            'data_date': _infer_snapshot_data_date(df, fetched_at),
            'source': _last_snapshot_source,
        }

        # 存入缓存
        set_cached_data('market_snapshot', df)
        return df

    # Celery workers do not share memory. Reuse only a complete same-day snapshot
    # persisted by another scan before accepting an older in-process stale cache.
    try:
        from core.db import load_recent_point_in_time_snapshot
        persisted = load_recent_point_in_time_snapshot(max_age_minutes=FRESHNESS_WARN_THRESHOLD_MIN)
        if persisted is not None and snapshot_matches_date(persisted, expected_date):
            logger.warning("All real-time sources failed. Using recent persisted cross-worker snapshot.")
            set_cached_data('market_snapshot', persisted)
            return persisted
    except Exception as exc:
        logger.warning(f"Persisted market snapshot fallback unavailable: {exc}")

    if stale_fallback is not None and not stale_fallback.empty:
        logger.warning("All real-time sources failed. Falling back to stale snapshot (may lag minutes).")
        stale_attrs = getattr(stale_fallback, 'attrs', {}) or {}
        stale_fallback.attrs = {
            'fetched_at': stale_attrs.get('fetched_at'),
            'data_date': stale_attrs.get('data_date') or expected_date,
            'source': STALE_SNAPSHOT_WARN,
        }
        return stale_fallback

    logger.error("All real-time snapshot sources failed and no stale cache available.")
    return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# P1：行情新鲜度辅助函数（供 Bark 推送生成"⏱️ 行情 HH:MM · 源"标注）
# 消除 5 处内联 set_index().to_dict() 重复（复用优先），并统一暴露 attrs 元数据。
# ─────────────────────────────────────────────────────────────────────────────

def snapshot_lookup(snapshot, code: str, column: str = 'price'):
    """统一"从快照查某只股票某字段"的入口。

    替换散落在 sentinel/tasks/watchlist/paper_trade 的 set_index().to_dict() 模式。
    Args:
        snapshot: get_market_snapshot() 返回的 DataFrame
        code: 6 位股票代码
        column: 字段名（默认 'price'）
    Returns:
        (value, fetched_at, source)：value 为 None 表示快照中无此票；
        fetched_at/source 来自 df.attrs（可能为 None，如旧缓存或测试 mock）。
    """
    if snapshot is None or (hasattr(snapshot, 'empty') and snapshot.empty):
        return None, None, None
    attrs = getattr(snapshot, 'attrs', {}) or {}
    fetched_at = attrs.get('fetched_at')
    source = attrs.get('source')
    if column not in getattr(snapshot, 'columns', []):
        return None, fetched_at, source
    match = snapshot[snapshot['code'] == code]
    if match.empty:
        return None, fetched_at, source
    try:
        val = match.iloc[0][column]
        # NaN 视为缺失
        if val != val:  # noqa: PLR0124  (NaN != NaN)
            val = None
    except Exception:
        val = None
    return val, fetched_at, source


def format_freshness(snapshot=None, threshold_min: float = None, *, attrs: dict = None) -> str:
    """生成 Bark 推送用的行情新鲜度行，遵循现有 emoji 规范。

    Args:
        snapshot: 已抓取的快照 DataFrame（读其 .attrs）；或传 None 配合 attrs。
        attrs: 直接传入 attrs dict（优先级高于 snapshot）。用于推送点的"价格"与
               "时间戳"必须同源——调用方用已有快照的价格时，应传同一快照的 attrs，
               而非重新调 get_market_snapshot()（否则时间戳会来自新快照，与旧价格矛盾）。
    Returns: 形如 "⏱️ 行情 14:32 (滞后2分) · akshare东财"
             过期超阈值或 stale 兜底时前缀 ⚠️，如 "⚠️ ⏱️ 行情 14:30 · 过期快照(可能滞后)"
    """
    if threshold_min is None:
        threshold_min = FRESHNESS_WARN_THRESHOLD_MIN
    a = attrs if attrs is not None else (getattr(snapshot, 'attrs', {}) or {})
    fetched_at = a.get('fetched_at')
    src = a.get('source') or "未知源"
    if fetched_at is None:
        return f"⏱️ 行情时间未知 · {src}"
    now = datetime.now()
    lag_min = (now - fetched_at).total_seconds() / 60.0
    time_str = fetched_at.strftime("%H:%M")
    is_stale = src == STALE_SNAPSHOT_WARN or lag_min > threshold_min
    prefix = "⚠️ " if is_stale else ""
    lag_text = "实时" if lag_min < 1 else f"滞后{int(lag_min)}分"
    return f"{prefix}⏱️ 行情 {time_str} ({lag_text}) · {src}"


def is_snapshot_stale(snapshot=None, threshold_min: float = None, *, attrs: dict = None) -> bool:
    """Return True when a market snapshot is unsuitable for live-only Bark pushes."""
    if threshold_min is None:
        threshold_min = FRESHNESS_WARN_THRESHOLD_MIN
    a = attrs if attrs is not None else (getattr(snapshot, 'attrs', {}) or {})
    if a.get('source') == STALE_SNAPSHOT_WARN:
        return True
    fetched_at = a.get('fetched_at')
    if fetched_at is None:
        return False
    return (datetime.now() - fetched_at).total_seconds() / 60.0 > threshold_min


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

    res = {}

    def _valid_index_quote(name: str, price: float) -> bool:
        min_price = {
            "上证": 1000,
            "创业板": 500,
            "沪深300": 1000,
            "科创50": 500,
            "中证1000": 1000,
        }.get(name, 100)
        if price < min_price:
            logger.warning(f"[index_data] Reject suspicious {name} quote: {price}")
            return False
        return True

    # 1. 优先尝试腾讯极速合并实时源 (海外IP畅通无阻，毫秒级返回，彻底避免多线程并发超时)
    try:
        symbols = list(indices.values())
        url = f"http://qt.gtimg.cn/q={','.join(symbols)}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            r.encoding = 'gbk'
            lines = r.text.strip().split('\n')
            code_to_name = {v: k for k, v in indices.items()}
            for line in lines:
                if not line or '=' not in line or '"' not in line:
                    continue
                line_prefix = line.split('=')[0].replace('v_', '').strip()
                if line_prefix in code_to_name:
                    content = line.split('"')[1]
                    parts = content.split('~')
                    if len(parts) >= 33:
                        name = code_to_name[line_prefix]
                        price = float(parts[3]) if parts[3] else 0.0
                        if not _valid_index_quote(name, price):
                            continue
                        res[name] = {
                            'price': price,
                            'pct': float(parts[32]) if parts[32] else 0.0
                        }
            if len(res) == len(indices):
                set_cached_data('index_data', res)
                return res
            elif res:
                logger.warning(f"[index_data] Partial fetch success from Tencent batch: {list(res.keys())}")
    except Exception as e:
        logger.debug(f"Tencent index batch fetch failed: {e}")

    # 2. 备选源：提前获取东财全指数快照 (EM 备选源)
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
                price = float(row['最新价'])
                if not _valid_index_quote(name, price):
                    return None
                return {'price': price, 'pct': float(row['涨跌幅'])}
            return None
        
        result = resilient_fetch([_tx, _em], timeout=15, label=f"index_{name}")
        return name, result

    # 针对未成功获取的指数，启动并发兜底
    missing_indices = {k: v for k, v in indices.items() if k not in res}
    if missing_indices:
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(fetch_one, n, c) for n, c in missing_indices.items()]
                for future in futures:
                    try:
                        name, data = future.result(timeout=15)
                        if data:
                            res[name] = data
                    except Exception:
                        pass
        except Exception:
            pass

    # 3. 兜底：用上次成功获取的缓存数据补全缺失项
    stale_index = get_stale_cache('index_data')
    if stale_index:
        for name in indices:
            if name not in res and name in stale_index:
                res[name] = stale_index[name]

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
    stale = get_stale_cache('hot_sectors')
    if stale:
        return stale
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
    stale = get_stale_cache('sector_trends')
    if stale:
        return stale
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
                    # 仅在重试时退避，首次请求不 sleep，避免冷启动时 ~86 个板块全部先空等。
                    if i > 0:
                        time.sleep(random.uniform(0.3, 0.6))
                    df_curr = ak.stock_board_industry_cons_em(symbol=sector_name)
                    if not df_curr.empty:
                        return sector_name, df_curr[['代码', '名称']].copy()
                except Exception:
                    pass
            return None, None

        all_basic_data = []
        # 板块间无频率限制，提升并发度以加速冷启动（原 max_workers=5 → 12）。
        with ThreadPoolExecutor(max_workers=12) as executor:
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

def _fetch_index_hist_sina(code: str) -> pd.DataFrame:
    """使用新浪纯 HTTP 接口获取指数最近 100 天的日 K 线历史 (100% 避开代理 SSL 问题)"""
    if code == "000001":
        symbol = "sh000001"
    elif code == "000300":  # 沪深300 指数（上交所发布，需 sh 前缀）
        symbol = "sh000300"
    elif code == "399006":
        symbol = "sz399006"
    elif code.startswith('6') or code.startswith('900'):
        symbol = f'sh{code}'
    else:
        symbol = f'sz{code}'
        
    url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=100"
    try:
        r = requests.get(url, timeout=5, proxies={"http": None, "https": None})
        if r.status_code != 200:
            raise ValueError(f"Sina HTTP returned {r.status_code}")
            
        data = r.json()
        if not data or not isinstance(data, list):
            raise ValueError("Invalid JSON response from Sina")
            
        results = []
        for item in data:
            results.append({
                '日期': item['day'].split()[0], # 格式化 YYYY-MM-DD
                '开盘': float(item['open']),
                '收盘': float(item['close']),
                '最高': float(item['high']),
                '最低': float(item['low']),
                '成交量': float(item['volume'])
            })
            
        df = pd.DataFrame(results)
        df = df.sort_values('日期').reset_index(drop=True)
        return df
    except Exception as e:
        logger.warning(f"Sina HTTP fetch index hist for {code} failed: {e}")
        raise

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
    unavailable_cache_key = f'{cache_key}_unavailable'
    if get_cached_data(unavailable_cache_key, 60) is not None:
        return pd.DataFrame()

    def _min_valid_index_close(index_code: str) -> float:
        return {
            "000001": 1000,  # 上证指数
            "000300": 1000,  # 沪深300
            "399006": 500,   # 创业板指
        }.get(index_code, 100)

    # 1. 第一级：优先采用极速且避开所有代理阻碍的新浪纯 HTTP 接口 (最实时，2026年数据完整)
    try:
        df = _fetch_index_hist_sina(code)
        if not df.empty:
            set_cached_data(cache_key, df)
            logger.info(f"Successfully fetched index hist for {code} via Sina HTTP (rows: {len(df)})")
            return df
    except Exception as e:
        logger.warning(f"Failed to fetch index hist via Sina HTTP, trying fallback: {e}")

    # 2. 第二级：极速无网络本地数据库兜底
    # 修复 BUG-F: 原来用 000001(平安银行) 个股作为指数 fallback，但个股不是指数，
    # 会导致 benchmark_return/alpha 完全失真。改为只尝试从 daily_k 取真正的指数数据，
    # 取不到就返回 None（让 alpha 诚实显示为 "-"，而非用错误数据误导）。
    try:
        from core.db import get_db_engine
        from sqlalchemy import text
        engine = get_db_engine()
        if engine:
            # 指数使用带市场前缀的独立代码，彻底避开 000001（平安银行）
            # 与 000001（上证指数）的六位代码冲突。
            local_index_code = {
                "000001": "sh000001",
                "000300": "sh000300",
                "399006": "sz399006",
            }.get(code)
            if local_index_code:
                query = text("""
                    SELECT date as "日期", close as "收盘", open as "开盘",
                           high as "最高", low as "最低", vol as "成交量"
                    FROM daily_k
                    WHERE code = :code
                    ORDER BY date DESC LIMIT 500
                """)
                with engine.connect() as conn:
                    df_local = pd.read_sql(query, conn, params={"code": local_index_code})
                    df_local = df_local.sort_values("日期").reset_index(drop=True)
                    if not df_local.empty:
                        # 关键修复：指数 6 位代码会与个股代码冲突。
                        # 例如 000001 在指数语境是上证，在个股语境是平安银行。
                        # 用合理点位下限识别污染数据，绝不把个股当指数。
                        latest_close = float(df_local["收盘"].iloc[-1])
                        min_close = _min_valid_index_close(code)
                        if latest_close < min_close:
                            logger.warning(
                                f"Local index fallback for {local_index_code} is suspicious "
                                f"(close={latest_close:.2f}, expected >= {min_close:.0f}). "
                                "Refusing to use it as benchmark; returning empty."
                            )
                        else:
                            logger.info(
                                f"Loaded index {local_index_code} from local database "
                                f"as fallback (rows: {len(df_local)})"
                            )
                            return df_local
            logger.warning(
                f"Index {code} has no independent local data; trying final fallback "
                "(stock-substitute disabled)"
            )
    except Exception as e:
        logger.warning(f"Fallback loading 000001 from local database failed: {e}")

    # 3. 第三级：兜底采用原 akshare 接口，但在独立的子线程中强力加入 5 秒超时保护限制，绝不卡死
    try:
        import threading
        
        res_container = []
        def target_func():
            try:
                df_ak = ak.index_zh_a_hist(symbol=code, period="daily")
                res_container.append(df_ak)
            except Exception as ex:
                logger.warning(f"ak.index_zh_a_hist exception: {ex}")
                
        t = threading.Thread(target=target_func)
        t.daemon = True
        t.start()
        t.join(timeout=5.0)
        
        if res_container:
            df = res_container[0]
            if not df.empty:
                set_cached_data(cache_key, df)
                return df
        logger.warning(f"akshare fallback for {code} timed out or failed after 5s.")
    except Exception as e:
        logger.debug(f"Error fetching index hist for {code}: {e}")

    # K-line calculation can request the same benchmark more than once. Cache a
    # failed lookup briefly so one provider outage does not multiply 5s waits.
    set_cached_data(unavailable_cache_key, True)
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
