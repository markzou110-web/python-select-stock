from datetime import datetime

from core.trading_calendar import (
    _trade_dates_cache,
    _cache_updated_at,
    is_a_share_after_close_sync_window,
    is_a_share_intraday_session,
    is_a_share_trading_day,
    previous_a_share_trading_date,
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


# ── BUG3 修复：节假日识别 ──

def _inject_mock_calendar(dates_set):
    """注入 mock 交易日历（避免测试依赖网络）。"""
    import core.trading_calendar as tc
    tc._trade_dates_cache = dates_set
    tc._cache_updated_at = datetime.now()


def test_holiday_weekday_is_not_trading_day(monkeypatch):
    """工作日节假日（如春节/国庆）应识别为休市。"""
    # mock 交易日历：包含正常工作日，不包含节假日
    _inject_mock_calendar({
        "2026-02-17", "2026-02-18",  # 春节后交易日
        "2026-10-08",                # 国庆后交易日
    })
    spring_festival = datetime(2026, 2, 16)  # 周一，春节休市
    national_day = datetime(2026, 10, 1)     # 周四，国庆休市
    assert not is_a_share_trading_day(spring_festival), "春节应休市"
    assert not is_a_share_trading_day(national_day), "国庆应休市"
    # 正常工作日仍为交易日
    assert is_a_share_trading_day(datetime(2026, 2, 17))


def test_no_calendar_falls_back_to_weekday(monkeypatch):
    """交易日历不可用时（获取失败）回退到 weekday 判断（保证可用性）。"""
    _inject_mock_calendar(set())  # 空集合（模拟获取失败）
    # 工作日 → True（回退到 weekday）
    assert is_a_share_trading_day(datetime(2026, 3, 16))  # 周一
    # 周末 → False
    assert not is_a_share_trading_day(datetime(2026, 3, 21))  # 周六


def test_previous_trading_date_uses_calendar_and_weekday_fallback():
    _inject_mock_calendar({"2026-09-30", "2026-10-08"})
    assert previous_a_share_trading_date("2026-10-08") == "2026-09-30"

    _inject_mock_calendar(set())
    assert previous_a_share_trading_date("2026-07-27") == "2026-07-24"
