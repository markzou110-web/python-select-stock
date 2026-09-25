import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.price_action import analyze_price_action
from core.timeframe_context import build_completed_timeframe_context, build_timeframe_shadow_report


def _daily_bars(end="2026-09-14"):
    dates = pd.bdate_range("2025-01-02", end)
    close = pd.Series([10 + index * 0.035 for index in range(len(dates))])
    return pd.DataFrame({
        "日期": dates,
        "开盘": close - 0.1,
        "最高": close + 0.2,
        "最低": close - 0.2,
        "收盘": close,
        "成交量": 1000,
    })


def test_current_month_and_week_do_not_change_completed_timeframe_labels():
    bars = _daily_bars()
    before = build_completed_timeframe_context(bars)
    spike = bars.iloc[-1:].copy()
    spike["日期"] = pd.Timestamp("2026-09-15")
    spike["收盘"] = 99
    spike["成交量"] = 100000
    after = build_completed_timeframe_context(pd.concat([bars, spike], ignore_index=True))

    assert before["pa_monthly_state"] == "UP"
    assert before["pa_monthly_as_of"] == "2026-08-31"
    assert before["pa_weekly_position_state"] != "UNAVAILABLE"
    assert before == after


def test_completed_weekly_patterns_are_research_labels():
    fridays = pd.date_range("2026-01-02", periods=24, freq="W-FRI")

    def labels(weeks):
        bars = pd.DataFrame(weeks)
        bars["日期"] = fridays
        next_week = bars.iloc[-1:].copy()
        next_week["日期"] = fridays[-1] + pd.Timedelta(days=3)
        return build_completed_timeframe_context(pd.concat([bars, next_week], ignore_index=True))["pa_weekly_pattern_signals"]

    base = [{"开盘": 10.0, "收盘": 10.0, "最高": 10.5, "最低": 9.8, "成交量": 1000} for _ in fridays]

    five_week = [dict(week) for week in base]
    for index, low in zip(range(-4, 0), (9.5, 9.6, 9.7, 9.8)):
        five_week[index]["最低"] = low
    five_week[-1].update({"开盘": 10.1, "收盘": 10.8, "最高": 10.9, "成交量": 1300})
    assert "站稳5周线" in labels(five_week)

    breakout = [dict(week) for week in base]
    breakout[-1].update({"开盘": 10.1, "收盘": 11.0, "最高": 11.1, "成交量": 1400})
    assert "周线平台放量突破" in labels(breakout)

    moving_averages = [dict(week) for week in base]
    for index, week in enumerate(moving_averages):
        close = 10 + index * 0.05 + index * index * 0.004
        week.update({"开盘": close - 0.1, "收盘": close, "最高": close + 0.1,
                     "最低": close - 0.2, "成交量": 1000 + index * 5})
    assert "5/20周均线转强" in labels(moving_averages)

    pileup = [dict(week) for week in base]
    for index, volume in zip(range(-4, -1), (1000, 1100, 1200)):
        pileup[index]["成交量"] = volume
    pileup[-2]["收盘"] = 10.3
    pileup[-1].update({"开盘": 10.2, "收盘": 10.1, "最高": 10.3,
                       "最低": 10.05, "成交量": 800})
    assert "周线堆量后缩量回踩" in labels(pileup)


def test_weekly_pattern_scan_is_watch_only(monkeypatch):
    from core import scanner

    fridays = pd.date_range("2026-01-02", periods=24, freq="W-FRI")
    bars = pd.DataFrame({
        "日期": fridays,
        "开盘": 10.0,
        "收盘": 10.0,
        "最高": 10.5,
        "最低": 9.8,
        "成交量": 1000,
    })
    bars.loc[bars.index[-1], ["开盘", "收盘", "最高", "成交量"]] = [10.1, 11.0, 11.1, 1400]
    next_week = bars.iloc[-1:].copy()
    next_week["日期"] = fridays[-1] + pd.Timedelta(days=3)
    bars = pd.concat([bars, next_week], ignore_index=True)
    monkeypatch.setattr(scanner, "calculate_indicators", lambda frame, **kwargs: frame)
    result = scanner.single_stock_task(
        code="000001", name="测试", price=11.0, vol=1400, open_price=10.1,
        threshold=0.1, vol_multiplier=1.2, rsi_min=50, use_macd_filter=False,
        use_bb_sqz=False, sqz_lookback=20, use_weekly=False,
        preloaded_df=bars, strategy_type="weekly_four_patterns", min_data_days=20,
    )
    assert result["weekly_pattern_watch_only"] is True
    assert "周线平台放量突破" in result["pa_weekly_pattern_signals"]
    assert result["Score"] == 0


def test_short_history_is_marked_unavailable_and_cannot_be_a_shadow_match():
    result = build_completed_timeframe_context(_daily_bars().tail(100))
    assert result["pa_monthly_state"] == "UNAVAILABLE"
    assert result["pa_weekly_position_state"] == "UNAVAILABLE"
    assert result["pa_timeframe_shadow_only"] is True


def test_price_action_exposes_labels_without_changing_trade_plan():
    result = analyze_price_action(_daily_bars())
    assert result["pa_monthly_state"] == "UP"
    assert result["pa_timeframe_shadow_only"] is True
    assert result["pa_swing_entry_route"] in {"BREAKOUT", "PULLBACK", "WAIT"}
    assert result["pa_trade_plan"]["action"] in {"READY", "WAIT", "AVOID"}


def test_shadow_report_compares_nested_arms_without_legacy_or_block_samples():
    frame = pd.DataFrame([
        {"code": "000001", "signal_date": "2026-09-14", "strategy_type": "tv_dual", "trade_bucket": "OBSERVE", "pa_trade_action": "READY", "pa_monthly_state": "UP", "pa_weekly_position_state": "PULLBACK", "pa_swing_entry_route": "PULLBACK", "open_1d": 10, "close_5d": 11, "data_quality_excluded": False},
        {"code": "000002", "signal_date": "2026-09-14", "strategy_type": "tv_dual", "trade_bucket": "OBSERVE", "pa_trade_action": "READY", "pa_monthly_state": "UP", "pa_weekly_position_state": "EXTENDED", "pa_swing_entry_route": "BREAKOUT", "open_1d": 10, "close_5d": 9, "data_quality_excluded": False},
        {"code": "000003", "signal_date": "2026-09-14", "strategy_type": "tv_dual", "trade_bucket": "BLOCK", "pa_trade_action": "AVOID", "pa_monthly_state": "UP", "pa_weekly_position_state": "PULLBACK", "pa_swing_entry_route": "PULLBACK", "open_1d": 10, "close_5d": 20, "data_quality_excluded": False},
        {"code": "000004", "signal_date": "2026-09-14", "strategy_type": "tv_dual", "trade_bucket": "OBSERVE", "pa_trade_action": "READY", "pa_monthly_state": None, "pa_weekly_position_state": None, "pa_swing_entry_route": None, "open_1d": 10, "close_5d": 20, "data_quality_excluded": False},
    ])
    report = build_timeframe_shadow_report(frame)
    assert report["contract"]["mode"] == "SHADOW_ONLY"
    assert report["tagged_candidates"] == 2
    assert [arm["candidates"] for arm in report["arms"]] == [2, 2, 1, 1]
    assert report["arms"][0]["win_rate_5d_pct"] == 50.0
    assert report["arms"][-1]["win_rate_5d_pct"] == 100.0


def test_shadow_api_stays_read_only_and_returns_http_200(monkeypatch):
    from routers import review

    monkeypatch.setattr(review, "_load_scan_performance_df", lambda days: pd.DataFrame())
    app = FastAPI()
    app.include_router(review.router)
    response = TestClient(app).get("/api/review/multi-timeframe-shadow?days=30")
    assert response.status_code == 200
    assert response.json()["production_logic_changed"] is False
