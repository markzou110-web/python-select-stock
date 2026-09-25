"""
Local market-data sync scheduler.

Runs inside the API process so scheduled sync works when the desktop app/backend is open,
without requiring a separate Celery Beat process.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from core.db import get_setting, save_setting
from core.logging_config import logger
from core.sync_state import sync_progress, sync_progress_lock
from core.trading_calendar import is_a_share_trading_day


# 收盘后调度槽：15:10 用于当日 K 线最终化（午间同步写入的是半日部分数据）
SYNC_SCHEDULE_DEFAULT = "08:30,12:10,15:10,18:00"
_LOCK_KEY = "market_sync_lock_until"
LAST_SYNC_KEY = "last_market_sync_at"
POST_CLOSE_READY_TIME = "15:05"


def needs_post_close_catch_up(
    now: datetime,
    last_sync_at: Optional[datetime],
    is_trading_day: bool,
) -> bool:
    """启动时是否需要补跑收盘后同步。

    调度器只在精确到分钟的计划时刻触发，错过（进程未运行/重启跨过时刻）不会补，
    会导致当日 daily_k 停留在午间半日数据。此函数判断是否需要立即补一次。
    """
    if not is_trading_day:
        return False
    current_hm = now.strftime("%H:%M")
    if current_hm < POST_CLOSE_READY_TIME:
        return False
    if last_sync_at and last_sync_at.date() == now.date() and last_sync_at.strftime("%H:%M") >= POST_CLOSE_READY_TIME:
        return False
    return True


def _parse_lock_until(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def acquire_sync_lock(hours: int = 4) -> bool:
    """Best-effort cross-scheduler lock shared by API scheduler and Celery tasks."""
    now = datetime.now()
    lock_until = _parse_lock_until(get_setting(_LOCK_KEY, ""))
    if lock_until and lock_until > now:
        return False
    return save_setting(_LOCK_KEY, (now + timedelta(hours=hours)).isoformat(timespec="seconds"))


def release_sync_lock() -> None:
    save_setting(_LOCK_KEY, "")


def get_sync_schedule_times() -> list[str]:
    raw = get_setting("market_sync_schedule_times", SYNC_SCHEDULE_DEFAULT)
    return [item.strip() for item in str(raw).split(",") if item.strip()]


class MarketSyncScheduler:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._triggered_today: set[str] = set()
        self.schedule_times = get_sync_schedule_times()

    def update_schedule(self, times_str: Optional[str] = None) -> None:
        if times_str is not None:
            self.schedule_times = [item.strip() for item in str(times_str).split(",") if item.strip()]
        else:
            self.schedule_times = get_sync_schedule_times()
        logger.info(f"Market sync schedule updated to: {self.schedule_times}")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.update_schedule()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Market sync scheduler started.")
        self._maybe_catch_up_after_close()

    def _maybe_catch_up_after_close(self) -> None:
        """启动补跑：错过的收盘后同步（进程未运行/重启跨过计划时刻）在启动时补一次。

        背景：调度器只在精确到分钟的计划时刻触发，2026-09-21 实例中 API 进程
        21:32 才启动，导致 18:00 的收盘最终化同步未执行，当日 daily_k 停留在
        12:10 写入的半日部分数据，图表与实时价不一致。
        """
        try:
            now = datetime.now()
            last_sync_at = _parse_lock_until(get_setting(LAST_SYNC_KEY, ""))
            if not needs_post_close_catch_up(now, last_sync_at, is_a_share_trading_day(now)):
                return
            logger.info("Post-close catch-up sync triggered at startup (missed scheduled slot).")
            threading.Thread(
                target=self._run_scheduled_sync, args=("catch-up",), daemon=True,
            ).start()
        except Exception as exc:
            logger.warning(f"Post-close catch-up check failed (ignored): {exc}")

    def stop(self) -> None:
        self._stop_event.set()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now()
            current_date = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M")

            if current_time == "00:00":
                self._triggered_today.clear()
                self.update_schedule()

            trigger_key = f"{current_date} {current_time}"
            if (
                is_a_share_trading_day(now)
                and current_time in self.schedule_times
                and trigger_key not in self._triggered_today
            ):
                self._triggered_today.add(trigger_key)
                threading.Thread(
                    target=self._run_scheduled_sync,
                    args=(current_time,),
                    daemon=True,
                ).start()

            time.sleep(20)

    def _run_scheduled_sync(self, trigger_time: str) -> None:
        with sync_progress_lock:
            if sync_progress.get("is_running"):
                logger.info(f"Scheduled sync skipped at {trigger_time}: sync already running.")
                return

        if not acquire_sync_lock():
            logger.info(f"Scheduled sync skipped at {trigger_time}: another scheduler owns the lock.")
            return

        try:
            logger.info(f"Starting scheduled market data sync at {trigger_time}.")
            save_setting(LAST_SYNC_KEY, datetime.now().isoformat(timespec="seconds"))
            from routers.sync import background_sync_task

            background_sync_task()
        finally:
            release_sync_lock()


market_sync_scheduler = MarketSyncScheduler()
