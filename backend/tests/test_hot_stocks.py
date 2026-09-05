import pandas as pd

import core.hot_stocks as hot_stocks
from core.hot_stocks import normalize_chart_frame, normalize_hot_rank_frame, normalize_tencent_minute_payload


def test_normalize_hot_rank_frame_keeps_source_order_and_local_context():
    frame = pd.DataFrame([
        {
            "排名较昨日变动": 18,
            "当前排名": 7,
            "代码": "SZ000001",
            "股票名称": "平安银行",
            "最新价": 12.34,
            "涨跌幅": 3.21,
        },
        {
            "排名较昨日变动": -3,
            "当前排名": 12,
            "代码": "SH600519",
            "股票名称": "贵州茅台",
            "最新价": 1500,
            "涨跌幅": -1.25,
        },
    ])

    items = normalize_hot_rank_frame(
        frame,
        period="hour",
        limit=10,
        metadata={
            "000001": {"industry": "银行", "amount": 1_250_000_000},
            "600519": {"industry": "白酒", "amount": 980_000_000},
        },
    )

    assert [item["code"] for item in items] == ["000001", "600519"]
    assert items[0] == {
        "rank": 1,
        "source_rank": 7,
        "rank_change": 18,
        "code": "000001",
        "name": "平安银行",
        "price": 12.34,
        "pct": 3.21,
        "heat_label": "热度上升 18 位",
        "concepts": ["银行"],
        "amount_yi": 12.5,
    }
    assert items[1]["heat_label"] == "热度回落 3 位"


def test_normalize_hot_rank_frame_daily_uses_popularity_rank():
    frame = pd.DataFrame([
        {"当前排名": 2, "代码": "SZ300750", "股票名称": "宁德时代", "最新价": 210, "涨跌幅": 2.5}
    ])

    items = normalize_hot_rank_frame(frame, period="day", limit=1, metadata={})

    assert items[0]["heat_label"] == "人气第 2 名"
    assert items[0]["concepts"] == []


def test_normalize_hot_rank_frame_preserves_explicit_fallback_label():
    frame = pd.DataFrame([
        {
            "当前排名": 1,
            "代码": "600000",
            "股票名称": "浦发银行",
            "最新价": 10,
            "涨跌幅": 1,
            "热度标签": "活跃第 1 名",
        }
    ])

    items = normalize_hot_rank_frame(frame, period="day", limit=1, metadata={})

    assert items[0]["heat_label"] == "活跃第 1 名"


def test_normalize_chart_frame_filters_latest_minute_session_and_invalid_rows():
    frame = pd.DataFrame([
        {"时间": "2026-09-03 15:00:00", "开盘": 10, "收盘": 10.1, "最高": 10.2, "最低": 9.9, "成交量": 100},
        {"时间": "2026-09-04 09:31:00", "开盘": 10.2, "收盘": 10.3, "最高": 10.4, "最低": 10.1, "成交量": 200},
        {"时间": "2026-09-04 09:32:00", "开盘": 10.3, "收盘": None, "最高": 10.5, "最低": 10.2, "成交量": 250},
    ])

    points, data_date = normalize_chart_frame(frame, period="minute")

    assert data_date == "2026-09-04"
    assert points == [{
        "time": "2026-09-04 09:31:00",
        "open": 10.2,
        "close": 10.3,
        "high": 10.4,
        "low": 10.1,
        "volume": 200.0,
    }]


def test_normalize_tencent_minute_payload_converts_cumulative_volume():
    payload = {
        "data": {
            "sh600000": {
                "data": {
                    "date": "20260904",
                    "data": ["0930 10.00 100 1000.00", "0931 10.05 160 1603.00"],
                }
            }
        }
    }

    frame = normalize_tencent_minute_payload(payload, "600000")

    assert frame["时间"].tolist() == ["2026-09-04 09:30:00", "2026-09-04 09:31:00"]
    assert frame["收盘"].tolist() == [10.0, 10.05]
    assert frame["成交量"].tolist() == [100.0, 60.0]


def test_minute_chart_prefers_live_tencent_over_stale_local_data(monkeypatch):
    live = pd.DataFrame([
        {"时间": "2026-09-04 09:31:00", "开盘": 10, "收盘": 10.1, "最高": 10.1, "最低": 10, "成交量": 100}
    ])
    monkeypatch.setattr(hot_stocks, "get_cached_data", lambda *_args: None)
    monkeypatch.setattr(hot_stocks, "get_stale_cache", lambda *_args: None)
    monkeypatch.setattr(hot_stocks, "set_cached_data", lambda *_args: None)
    monkeypatch.setattr(hot_stocks, "_load_tencent_minute", lambda _code: live)
    monkeypatch.setattr(hot_stocks, "_load_local_chart", lambda *_args: (_ for _ in ()).throw(AssertionError("local fallback should not run")))
    monkeypatch.setattr(hot_stocks, "_previous_close", lambda *_args: 9.9)

    payload = hot_stocks.get_hot_stock_chart("600000", period="minute")

    assert payload["source"] == "tencent_minute"
    assert payload["data_date"] == "2026-09-04"
    assert len(payload["points"]) == 1
