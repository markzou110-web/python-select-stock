"""
Trading-session guards for A-share scheduled tasks.

包含节假日识别（修复 BUG3）：原 is_a_share_trading_day 仅判断 weekday，
不识别 A 股公共节假日（春节/国庆/清明等落在工作日的休市日），
导致休市日 sentinel 把收盘数据当实时数据推送。

修复：通过 akshare 交易日历（tool_trade_date_hist_sina）获取官方交易日集合，
24h 缓存。获取失败时回退到 weekday 判断（保证可用性）。
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Optional, Set

logger = logging.getLogger(__name__)

# ── 节假日识别（BUG3 修复）──
# 内存缓存：A 股交易日集合（date 字符串 "YYYY-MM-DD"），24h TTL。
_trade_dates_cache: Optional[Set[str]] = None
_cache_updated_at: Optional[datetime] = None
_CACHE_TTL_HOURS = 24


def _load_trade_dates() -> Set[str]:
    """从 akshare 加载 A 股交易日历，缓存 24 小时。

    失败时返回空集合（调用方回退到 weekday 判断）。
    """
    global _trade_dates_cache, _cache_updated_at
    now = datetime.now()
    if _trade_dates_cache is not None and _cache_updated_at is not None:
        if (now - _cache_updated_at).total_seconds() < _CACHE_TTL_HOURS * 3600:
            return _trade_dates_cache

    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        dates = set(str(d)[:10] for d in df[col].tolist())
        _trade_dates_cache = dates
        _cache_updated_at = now
        logger.info(f"交易日历已加载: {len(dates)} 个交易日（缓存 {_CACHE_TTL_HOURS}h）")
        return dates
    except Exception as exc:
        logger.warning(f"交易日历加载失败，回退到 weekday 判断: {exc}")
        # 保留旧缓存（如果有），避免一次失败就丢缓存
        return _trade_dates_cache or set()


def is_a_share_trading_day(now: datetime | None = None) -> bool:
    """判断是否为 A 股交易日。

    优先查交易日历（含节假日识别）；未加载/获取失败时回退到 weekday 判断。
    """
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False  # 周末必定休市，无需查日历

    # 工作日：查交易日历判断是否为节假日休市
    trade_dates = _load_trade_dates()
    if trade_dates:
        return now.strftime("%Y-%m-%d") in trade_dates
    # 日历不可用时回退到原逻辑（仅 weekday）
    return True


def is_a_share_intraday_session(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if not is_a_share_trading_day(now):
        return False

    current = now.time()
    morning = time(9, 25) <= current <= time(11, 35)
    afternoon = time(12, 55) <= current <= time(15, 5)
    return morning or afternoon


def is_a_share_after_close_sync_window(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return is_a_share_trading_day(now) and now.time() >= time(15, 0)
