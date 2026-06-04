from fastapi import APIRouter
from typing import Dict, Any, List
from datetime import datetime, timedelta
import time
import akshare as ak
import pandas as pd

from core.logging_config import logger
from core.data import (
    get_index_data,
    get_hot_sectors,
    get_market_snapshot,
    get_sector_map,
    get_sector_trends,
    get_cached_data,
    set_cached_data,
)
from core.db import get_db_engine
from core.sector_strength import build_sector_strength, build_sector_history_context, classify_sector_role

router = APIRouter(prefix="/api", tags=["market"])

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
            data["earnings"] = df_earnings[df_earnings['首次预约'] >= today_str]['股票代码'].tolist()[:500]

        # 2. Unlocks (Next 30 days) - using detailed release schedule
        end_date = (today + timedelta(days=30)).strftime("%Y%m%d")

        df_unlocks = fetch_with_timeout(
            lambda: ak.stock_restricted_release_detail_em(start_date=today_str, end_date=end_date),
            timeout=10
        )
        if df_unlocks is not None and not df_unlocks.empty:
            data["unlocks"] = df_unlocks['股票代码'].tolist()[:500]

        # 3. Reductions (Block trades - 大宗交易)
        df_reduce = fetch_with_timeout(
            lambda: ak.stock_dzjy_mrtj(),
            timeout=10
        )
        if df_reduce is not None and not df_reduce.empty:
            data["reductions"] = df_reduce['证券代码'].tolist()[:500]

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
        if not force:
            cached = get_cached_data(cache_key, 300)
            if cached:
                return {**cached, "cache_hit": True}

        snapshot = get_market_snapshot()
        sector_map = get_sector_map()
        sector_trends = get_sector_trends()
        history_context = build_sector_history_context(get_db_engine(), sector_map)
        strength = build_sector_strength(snapshot, sector_map, sector_trends, history_context)
        if not strength:
            return {"items": [], "updated_at": datetime.now().isoformat()}

        lead_map: Dict[str, List[Dict[str, Any]]] = {}
        if snapshot is not None and not snapshot.empty:
            df = snapshot.copy()
            df['code'] = df['code'].astype(str).str.zfill(6)
            df['industry'] = df['code'].map(sector_map).fillna('未知')
            df['pct_chg'] = pd.to_numeric(df['pct_chg'], errors='coerce').fillna(0)
            if 'price' in df.columns:
                df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0)
            for industry, group in df[df['industry'] != '未知'].groupby('industry'):
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
            "updated_at": datetime.now().isoformat(),
            "cache_hit": False,
            "cache_ttl_sec": 300,
        }
        set_cached_data(cache_key, payload)
        return payload
    except Exception as e:
        logger.error(f"Error fetching sector strength: {e}")
        return {"items": [], "updated_at": datetime.now().isoformat(), "error": str(e)}


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
        from core.data import get_tool_trade_date_hist
        trade_dates = get_tool_trade_date_hist()
        trade_dates['trade_date'] = pd.to_datetime(trade_dates['trade_date'])
        today = datetime.now()
        # Find the latest trade date <= today
        past_dates = trade_dates[trade_dates['trade_date'] <= today]
        if past_dates.empty:
            return {"error": "No trade dates found"}
        
        latest_date = past_dates.iloc[-1]['trade_date']
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
            
        return {
            "date": latest_date.strftime("%Y-%m-%d"),
            "limit_up_count": up_count,
            "limit_down_count": down_count,
            "max_streak": max_streak,
            "sentiment_score": score
        }
    except Exception as e:
        logger.error(f"Error fetching market sentiment: {e}")
        return {"error": str(e)}

@router.get("/market/sentiment/history")
def get_sentiment_history_api(days: int = 10):
    """获取市场情绪历史数据（涨跌停家数趋势）"""
    from core.data import get_sentiment_history
    return get_sentiment_history(days=days)
