import pandas as pd

from core.price_action_timeframes import build_intraday_price_action_context


def _points(direction: str = "up"):
    times = list(pd.date_range("2026-09-07 09:30", periods=121, freq="min"))
    times += list(pd.date_range("2026-09-07 13:00", periods=121, freq="min"))
    rows = []
    for index, time in enumerate(times):
        step = index * 0.005 * (1 if direction == "up" else -1)
        close = 10.0 + step
        rows.append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), "open": close - 0.01,
            "high": close + 0.03, "low": close - 0.03, "close": close, "volume": 1000,
        })
    return rows


def test_intraday_context_builds_daily_60m_5m_alignment_and_opening_range():
    result = build_intraday_price_action_context(
        {"price_action_regime": "多头趋势"}, _points("up"), previous_close=9.9,
    )

    assert result["state"] == "ALIGNED"
    assert result["daily"]["direction"] == "BULL"
    assert result["60m"]["direction"] == "BULL"
    assert result["60m"]["bars"] == 4
    assert result["5m"]["direction"] == "BULL"
    assert result["5m"]["bars"] == 48
    assert result["opening"]["range_high"] > result["opening"]["range_low"]
    assert result["production_effect"] is False


def test_intraday_context_flags_daily_hourly_conflict():
    result = build_intraday_price_action_context(
        {"price_action_regime": "多头趋势"}, _points("down"), previous_close=10.1,
    )

    assert result["state"] == "CONFLICT"


def test_intraday_context_degrades_without_minutes():
    result = build_intraday_price_action_context({"price_action_regime": "多头趋势"}, [])

    assert result["state"] == "UNAVAILABLE"
    assert result["production_effect"] is False
