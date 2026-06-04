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


SYNC_SCHEDULE_DEFAULT = "08:30,12:10,18:00"
_LOCK_KEY = "market_sync_lock_until"


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
            from routers.sync import background_sync_task

            background_sync_task()
        finally:
            release_sync_lock()


market_sync_scheduler = MarketSyncScheduler()
