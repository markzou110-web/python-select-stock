from fastapi import APIRouter
from typing import Dict, Any, List
from datetime import datetime, timedelta
import time
import akshare as ak

from core.logging_config import logger
from core.data import get_index_data, get_hot_sectors

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
