from datetime import datetime

from core.trading_calendar import (
    is_a_share_after_close_sync_window,
    is_a_share_intraday_session,
    is_a_share_trading_day,
)


def test_weekend_is_not_trading_day_or_session():
    sunday = datetime(2026, 5, 31, 10, 0)

    assert not is_a_share_trading_day(sunday)
    assert not is_a_share_intraday_session(sunday)
    assert not is_a_share_after_close_sync_window(sunday)


def test_weekday_intraday_sessions_are_allowed():
    monday_morning = datetime(2026, 5, 25, 10, 0)
    monday_afternoon = datetime(2026, 5, 25, 14, 20)

    assert is_a_share_intraday_session(monday_morning)
    assert is_a_share_intraday_session(monday_afternoon)


def test_lunch_and_after_close_guards():
    monday_lunch = datetime(2026, 5, 25, 12, 0)
    monday_after_close = datetime(2026, 5, 25, 18, 0)

    assert not is_a_share_intraday_session(monday_lunch)
    assert is_a_share_after_close_sync_window(monday_after_close)
