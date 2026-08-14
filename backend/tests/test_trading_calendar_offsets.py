from datetime import datetime

from core import trading_calendar


def test_shift_a_share_trading_date_counts_sessions_not_calendar_days(monkeypatch):
    monkeypatch.setattr(trading_calendar, "_trade_dates_cache", {
        "2026-08-06",
        "2026-08-07",
        "2026-08-10",
        "2026-08-11",
        "2026-08-12",
        "2026-08-13",
    })
    monkeypatch.setattr(trading_calendar, "_cache_updated_at", datetime.now())

    assert trading_calendar.shift_a_share_trading_date("2026-08-12", -3) == "2026-08-07"
    assert trading_calendar.shift_a_share_trading_date("2026-08-07", 3) == "2026-08-12"
