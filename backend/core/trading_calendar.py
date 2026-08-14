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
import threading
from datetime import datetime, time, timedelta
from typing import Optional, Set

logger = logging.getLogger(__name__)

# ── 节假日识别（BUG3 修复）──
# 内存缓存：A 股交易日集合（date 字符串 "YYYY-MM-DD"），24h TTL。
_trade_dates_cache: Optional[Set[str]] = None
_cache_updated_at: Optional[datetime] = None
_CACHE_TTL_HOURS = 24

# 加载锁：tool_trade_date_hist_sina() 内部会实例化 py_mini_racer.MiniRacer()（V8 引擎），
# V8 地址池在多线程并发初始化时会触发 native crash (address_pool_manager
# Check failed: !pool->IsInitialized())，该段错误无法被 try/except 捕获，
# 会杀掉整个进程。此锁确保同一时刻只有一个线程初始化 V8。
# 配合 preload_trade_calendar() 在进程启动时（单线程）预加载，从根本上避免并发。
_trade_dates_lock = threading.Lock()


def _load_trade_dates() -> Set[str]:
    """从 akshare 加载 A 股交易日历，缓存 24 小时。

    失败时返回空集合（调用方回退到 weekday 判断）。
    线程安全：通过 _trade_dates_lock 串行化，避免并发触发 V8 初始化崩溃。
    """
    global _trade_dates_cache, _cache_updated_at
    # 无锁快速路径：缓存有效直接返回
    now = datetime.now()
    if _trade_dates_cache is not None and _cache_updated_at is not None:
        if (now - _cache_updated_at).total_seconds() < _CACHE_TTL_HOURS * 3600:
            return _trade_dates_cache

    with _trade_dates_lock:
        # 二次检查：可能在等锁期间已被其他线程加载
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


def preload_trade_calendar() -> bool:
    """在进程启动时（单线程、后台线程启动前）预加载交易日历。

    目的：让 V8/mini_racer 在主线程安全初始化一次，避免后续 sentinel /
    sync_scheduler 后台线程并发调用 _load_trade_dates 时触发 V8 地址池
    重复初始化的 native crash。

    必须在 sentinel.start() / market_sync_scheduler.start() 之前调用。

    Returns:
        True 加载成功，False 加载失败（已回退到 weekday 判断，不阻断启动）
    """
    try:
        dates = _load_trade_dates()
        return bool(dates)
    except Exception as exc:
        logger.warning(f"preload_trade_calendar 失败（不阻断启动）: {exc}")
        return False


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


def previous_a_share_trading_date(value: str) -> str:
    """Return the trading date immediately before ``value``."""
    target = datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    trade_dates = _load_trade_dates()
    if trade_dates:
        previous = [item for item in trade_dates if item < target.isoformat()]
        if previous:
            return max(previous)

    current = target - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.isoformat()


def shift_a_share_trading_date(value: str, sessions: int) -> str:
    """Shift by A-share trading sessions, with a weekday fallback."""
    target = datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    if sessions == 0:
        return target.isoformat()

    trade_dates = sorted(_load_trade_dates())
    if trade_dates:
        if sessions > 0:
            candidates = [item for item in trade_dates if item > target.isoformat()]
            if len(candidates) >= sessions:
                return candidates[sessions - 1]
        else:
            candidates = [item for item in trade_dates if item < target.isoformat()]
            if len(candidates) >= abs(sessions):
                return candidates[sessions]

    direction = 1 if sessions > 0 else -1
    remaining = abs(sessions)
    current = target
    while remaining:
        current += timedelta(days=direction)
        if current.weekday() < 5:
            remaining -= 1
    return current.isoformat()


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
