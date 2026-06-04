"""
Trading-session guards for A-share scheduled tasks.
"""
from __future__ import annotations

from datetime import datetime, time


def is_a_share_trading_day(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return now.weekday() < 5


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
