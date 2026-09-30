from datetime import datetime

from routers.kline import _build_timeframe_confluence_markers, _latest_week_is_complete


def test_confluence_marks_only_first_daily_buy_in_confirmed_weekly_window():
    markers = _build_timeframe_confluence_markers(
        daily_buys=[{"time": "2026-09-28"}, {"time": "2026-09-29"}, {"time": "2026-10-06"}],
        weekly_buys=[{"time": "2026-09-25"}],
        weekly_sells=[{"time": "2026-10-02"}],
        display_start_date="2026-09-01",
    )

    assert [marker["time"] for marker in markers] == ["2026-09-28"]
    assert markers[0]["source"] == "timeframe_confluence"


def test_week_signal_is_not_confirmed_before_friday_close(monkeypatch):
    monkeypatch.setattr("core.trading_calendar.shift_a_share_trading_date", lambda *_: "2026-09-28")

    assert not _latest_week_is_complete("2026-09-25", datetime(2026, 9, 25, 14, 55))
    assert _latest_week_is_complete("2026-09-25", datetime(2026, 9, 25, 15, 10))


def test_week_with_friday_holiday_is_complete_after_last_session(monkeypatch):
    monkeypatch.setattr("core.trading_calendar.shift_a_share_trading_date", lambda *_: "2026-10-05")

    assert _latest_week_is_complete("2026-10-01", datetime(2026, 10, 2, 16, 0))
