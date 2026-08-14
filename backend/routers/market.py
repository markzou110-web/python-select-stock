from fastapi import APIRouter
from typing import Dict, Any, List
from datetime import datetime, timedelta
import time
import akshare as ak
import pandas as pd
from sqlalchemy import text

from core.logging_config import logger
from core.data import (
    get_index_data,
    get_hot_sectors,
    get_market_snapshot,
    get_sector_map,
    get_sector_trends,
    get_cached_data,
    get_stale_cache,
    set_cached_data,
    snapshot_data_date,
    snapshot_matches_date,
)
from core.db import get_db_engine, get_scan_dates, get_scan_history_by_date
from core.sector_strength import build_sector_strength, build_sector_leaders, build_sector_history_context, classify_sector_role
from core.sector_push_analysis import build_hot_sector_push_gap_analysis
from core.research_radar import build_candidate_research_radar

router = APIRouter(prefix="/api", tags=["market"])

_mine_sweeper_cache = {"data": None, "timestamp": 0}


def _get_local_market_snapshot(engine) -> pd.DataFrame:
    """Build a fast snapshot from the latest two locally stored trading days."""
    if engine is None:
        return pd.DataFrame()
    try:
        df = pd.read_sql(text("""
            WITH recent_dates AS (
                SELECT DISTINCT date
                FROM daily_k
                ORDER BY date DESC
                LIMIT 2
            )
            SELECT d.code, d.date, d.close, d.high, d.low, d.vol, s.name
            FROM daily_k d
            LEFT JOIN stock_basic s ON s.code = d.code
            WHERE d.date IN (SELECT date FROM recent_dates)
            ORDER BY d.code, d.date
        """), engine)
    except Exception as exc:
        logger.warning(f"Local market snapshot unavailable: {exc}")
        return pd.DataFrame()
    if df.empty:
        return df

    df["code"] = df["code"].astype(str).str.zfill(6)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values(["code", "date"])
    df["pct_chg"] = df.groupby("code")["close"].pct_change() * 100
    latest = df.groupby("code", as_index=False).tail(1).copy()
    latest["pct_chg"] = latest["pct_chg"].fillna(0).round(2)
    latest["price"] = latest["close"]
    latest["turnover"] = 0
    snapshot = latest[["code", "name", "price", "high", "low", "pct_chg", "vol", "turnover"]]
    snapshot.attrs = {
        "data_date": str(latest["date"].max())[:10],
        "fetched_at": datetime.now(),
        "source": "local_daily_k",
    }
    return snapshot


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
            data["earnings"] = df_earnings[df_earnings['首次预约'] >= today_str]['股票代码'].tolist()[:500]

        # 2. Unlocks (Next 30 days) - using detailed release schedule
        end_date = (today + timedelta(days=30)).strftime("%Y%m%d")

        df_unlocks = fetch_with_timeout(
            lambda: ak.stock_restricted_release_detail_em(start_date=today_str, end_date=end_date),
            timeout=10
        )
        if df_unlocks is not None and not df_unlocks.empty:
            data["unlocks"] = df_unlocks['股票代码'].tolist()[:500]

        # 3. Reductions — 已移除（口径错误）
        # 原用 ak.stock_dzjy_mrtj()（大宗交易每日统计）当"减持"数据源，但：
        #   a) 大宗交易 ≠ 减持（机构调仓/引入战投/约定购回都走大宗，买方常有6个月限售）；
        #   b) 实测该接口返回 2022-01-05 的陈旧数据（4年前），601138 因此被误判 D 级。
        # 真正的减持信号应来自高管减持公告或限售股减持计划，而非大宗交易统计。
        # 保留 data["reductions"] 为空列表以维持接口契约（调用方仍可读，但恒为空）。

    except Exception as e:
        logger.warning(f"Mine Sweeper Error: {e}")

    # 更新缓存
    _mine_sweeper_cache["data"] = data
    _mine_sweeper_cache["timestamp"] = time.time()

    return data


@router.get("/health")
def health_check():
    return {"status": "ok", "time": datetime.now().isoformat()}


@router.get("/market/indices")
def get_indices():
    """获取主要指数行情"""
    logger.debug("Request: GET /api/market/indices")
    return get_index_data()


@router.get("/market/sectors")
def get_sectors():
    """获取热门行业板块"""
    logger.debug("Request: GET /api/market/sectors")
    return get_hot_sectors()


@router.get("/market/sector-strength")
def get_sector_strength(limit: int = 20, force: bool = False):
    """获取实时板块强度榜：动量、扩散率、阶段、前排个股。"""
    try:
        safe_limit = max(1, min(limit, 100))
        cache_key = f"sector_strength:{safe_limit}"
        expected_date = datetime.now().strftime("%Y-%m-%d")
        if not force:
            cached = get_cached_data(cache_key, 300)
            if cached and cached.get("data_date") == expected_date:
                return {**cached, "cache_hit": True}
            stale = get_stale_cache(cache_key)
            if stale and stale.get("data_date") == expected_date:
                return {**stale, "cache_hit": True, "cache_stale": True}

        engine = get_db_engine()
        if force:
            snapshot = get_market_snapshot(force_refresh=True)
            sector_trends = get_sector_trends()
        else:
            snapshot = get_market_snapshot()
            if not snapshot_matches_date(snapshot, expected_date):
                snapshot = _get_local_market_snapshot(engine)
            sector_trends = get_cached_data("sector_trends", 600) or get_stale_cache("sector_trends") or {}
        data_date = snapshot_data_date(snapshot)
        sector_map = get_sector_map()
        history_context = build_sector_history_context(engine, sector_map)
        strength = build_sector_strength(snapshot, sector_map, sector_trends, history_context)
        if not strength:
            return {"items": [], "updated_at": datetime.now().isoformat()}

        lead_map: Dict[str, List[Dict[str, Any]]] = build_sector_leaders(
            engine, snapshot, sector_map, strength
        )

        # 兜底：多日历史缺失时退回当日涨幅排序（保留原契约 code/name/price/pct/role）
        if snapshot is not None and not snapshot.empty:
            df = snapshot.copy()
            df['code'] = df['code'].astype(str).str.zfill(6)
            df['industry'] = df['code'].map(sector_map).fillna('未知')
            df['pct_chg'] = pd.to_numeric(df['pct_chg'], errors='coerce').fillna(0)
            if 'price' in df.columns:
                df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0)
            for industry, group in df[df['industry'] != '未知'].groupby('industry'):
                if lead_map.get(industry):
                    continue
                sector_avg = float(strength.get(industry, {}).get('sector_avg_pct', group['pct_chg'].mean()) or 0)
                leaders = group.sort_values('pct_chg', ascending=False).head(5)
                lead_map[industry] = [
                    {
                        "code": str(row.get('code', '')),
                        "name": str(row.get('name', '')),
                        "price": round(float(row.get('price', 0) or 0), 2),
                        "pct": round(float(row.get('pct_chg', 0) or 0), 2),
                        "role": classify_sector_role(
                            float(row.get('pct_chg', 0) or 0),
                            sector_avg,
                            rank_in_sector=idx + 1,
                        ),
                    }
                    for idx, (_, row) in enumerate(leaders.iterrows())
                ]

        items = []
        phase_order = {
            "SECTOR_CONFIRM": 0,
            "SECTOR_EARLY": 1,
            "SECTOR_CLIMAX": 2,
            "SECTOR_NEUTRAL": 3,
            "SECTOR_FADE": 4,
        }
        for industry, data in strength.items():
            item = {"industry": industry, **data, "leaders": lead_map.get(industry, [])}
            items.append(item)
        items = sorted(
            items,
            key=lambda x: (phase_order.get(x.get("sector_phase"), 9), -float(x.get("sector_momentum_score", 0))),
        )[:safe_limit]
        payload = {
            "items": items,
            "data_date": data_date,
            "updated_at": datetime.now().isoformat(),
            "cache_hit": False,
            "cache_ttl_sec": 300,
        }
        set_cached_data(cache_key, payload)
        return payload
    except Exception as e:
        logger.error(f"Error fetching sector strength: {e}")
        return {"items": [], "updated_at": datetime.now().isoformat(), "error": str(e)}


@router.get("/market/sector-push-gaps")
def get_sector_push_gaps(limit: int = 8, date: str = "", force: bool = False):
    """解释热门板块为什么有/没有进入推荐推送。"""
    try:
        safe_limit = max(1, min(limit, 30))
        dates = get_scan_dates()
        scan_date = date or (dates[0] if dates else "")
        scan_results = get_scan_history_by_date(scan_date) if scan_date else []
        sector_payload = get_sector_strength(limit=max(safe_limit, 20), force=force)
        items = build_hot_sector_push_gap_analysis(
            sector_payload.get("items", []),
            scan_results,
            limit=safe_limit,
        )
        return {
            "items": items,
            "scan_date": scan_date,
            "scan_result_count": len(scan_results),
            "updated_at": datetime.now().isoformat(),
            "cache_hit": bool(sector_payload.get("cache_hit")),
            "cache_stale": bool(sector_payload.get("cache_stale")),
        }
    except Exception as e:
        logger.error(f"Error building sector push gap analysis: {e}")
        return {"items": [], "updated_at": datetime.now().isoformat(), "error": str(e)}


@router.get("/market/research-radar")
def get_research_radar(limit: int = 10, force_refresh: bool = False):
    """Return news and announcement evidence linked to positions, watchlist, and scan candidates."""
    return build_candidate_research_radar(
        get_db_engine(),
        limit=limit,
        force_refresh=force_refresh,
    )


@router.get("/market/regime")
def get_market_regime(strategy_type: str = "squeeze"):
    """获取当前市场状态和推荐参数"""
    from core.market_regime import detect_market_regime, get_adaptive_params
    
    regime_info = detect_market_regime()
    recommended = get_adaptive_params(regime_info["regime"], strategy_type)
    
    return {
        "regime": regime_info,
        "recommended_params": recommended
    }


@router.get("/market/sentiment")
def get_market_sentiment():
    """获取市场情绪数据：涨跌停家数，连板高度"""
    try:
        engine = get_db_engine()
        snapshot = get_market_snapshot()
        snapshot_source = str((getattr(snapshot, "attrs", {}) or {}).get("source") or "live")
        if snapshot is None or snapshot.empty:
            snapshot = _get_local_market_snapshot(engine)
            snapshot_source = "local_daily_k"

        from core.data import get_tool_trade_date_hist
        trade_dates = get_tool_trade_date_hist()
        trade_dates['trade_date'] = pd.to_datetime(trade_dates['trade_date'])
        today = datetime.now()
        # Find the latest trade date <= today
        past_dates = trade_dates[trade_dates['trade_date'] <= today]
        if past_dates.empty:
            return {"error": "No trade dates found"}
        
        latest_date = past_dates.iloc[-1]['trade_date']
        quote_date = snapshot_data_date(snapshot)
        if quote_date:
            quote_timestamp = pd.to_datetime(quote_date, errors="coerce")
            if pd.notna(quote_timestamp) and quote_timestamp <= today:
                latest_date = quote_timestamp
        date_str = latest_date.strftime("%Y%m%d")

        # 涨停池
        try:
            df_up = ak.stock_zt_pool_em(date=date_str)
            up_count = len(df_up) if df_up is not None and not df_up.empty else 0
            # 计算最高连板
            max_streak = 0
            if up_count > 0 and '连板数' in df_up.columns:
                max_streak = int(df_up['连板数'].max())
        except:
            up_count = 0
            max_streak = 0
            
        # 跌停池
        try:
            df_down = ak.stock_zt_pool_dtgc_em(date=date_str)
            down_count = len(df_down) if df_down is not None and not df_down.empty else 0
        except:
            down_count = 0
            
        # 简单情绪评分 (0-100)
        score = 50
        if up_count + down_count > 0:
            ratio = up_count / (up_count + down_count)
            score = int(ratio * 100)

        from core.data import get_market_regime as get_market_regime_data
        from core.decision_layer import build_market_decision_context, load_market_cycle_history

        decision = build_market_decision_context(
            snapshot,
            get_market_regime_data(),
            load_market_cycle_history(engine),
        )

        return {
            "date": latest_date.strftime("%Y-%m-%d"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "snapshot_source": snapshot_source,
            "snapshot_count": len(snapshot) if snapshot is not None else 0,
            "limit_up_count": up_count,
            "limit_down_count": down_count,
            "max_streak": max_streak,
            "sentiment_score": decision.get("market_sentiment_score", score),
            **decision,
        }
    except Exception as e:
        logger.error(f"Error fetching market sentiment: {e}")
        return {"error": str(e)}

@router.get("/market/sentiment/history")
def get_sentiment_history_api(days: int = 10):
    """获取市场情绪历史数据（涨跌停家数趋势）"""
    from core.data import get_sentiment_history
    return get_sentiment_history(days=days)
