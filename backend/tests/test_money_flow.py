import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import money_flow
from core.data import CACHE


def setup_function():
    CACHE.clear()
    money_flow._last_request_at = 0


def test_stock_money_flow_uses_cache(monkeypatch):
    calls = {"count": 0}

    def fake_flow(stock: str, market: str):
        calls["count"] += 1
        assert stock == "000001"
        assert market == "sz"
        return pd.DataFrame([
            {
                "日期": "2026-06-01",
                "收盘价": 10,
                "涨跌幅": 1.2,
                "主力净流入-净额": 100000000,
                "主力净流入-净占比": 8.5,
                "超大单净流入-净额": 50000000,
                "大单净流入-净额": 30000000,
            }
        ])

    monkeypatch.setattr(money_flow, "MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(money_flow.ak, "stock_individual_fund_flow", fake_flow)

    first = money_flow.get_stock_money_flow("000001")
    second = money_flow.get_stock_money_flow("000001")

    assert calls["count"] == 1
    assert first["status"] == "ok"
    assert second["cache_hit"] is True
    assert first["latest"]["main_net_inflow_yi"] == 1.0
    assert first["summary"]["bias"] == "inflow"


def test_stock_money_flow_returns_stale_cache_on_failure(monkeypatch):
    monkeypatch.setattr(money_flow, "MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(money_flow.ak, "stock_individual_fund_flow", lambda **_: pd.DataFrame([{
        "日期": "2026-06-01",
        "主力净流入-净额": -200000000,
    }]))
    first = money_flow.get_stock_money_flow("600000")

    def fail_flow(**_kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(money_flow.ak, "stock_individual_fund_flow", fail_flow)
    monkeypatch.setattr(money_flow.ak, "stock_individual_fund_flow_rank", lambda indicator: (_ for _ in ()).throw(RuntimeError("rank blocked")))
    monkeypatch.setattr(money_flow.ak, "stock_fund_flow_individual", lambda symbol: (_ for _ in ()).throw(RuntimeError("ths blocked")))
    stale = money_flow.get_stock_money_flow("600000", force_refresh=True)

    assert first["status"] == "ok"
    assert stale["status"] == "stale"
    assert stale["cache_hit"] is True
    assert stale["summary"]["bias"] == "outflow"


def test_money_flow_rank_normalises_rows(monkeypatch):
    monkeypatch.setattr(money_flow, "MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(money_flow.ak, "stock_individual_fund_flow_rank", lambda indicator: pd.DataFrame([
        {"代码": "000001", "名称": "平安银行", "涨跌幅": 2.5, "主力净流入-净额": 300000000, "主力净流入-净占比": 6.1}
    ]))

    result = money_flow.get_money_flow_rank(indicator="5日", limit=5)

    assert result["status"] == "ok"
    assert result["items"][0]["code"] == "000001"
    assert result["items"][0]["main_net_inflow_yi"] == 3.0


def test_money_flow_rank_falls_back_to_ths(monkeypatch):
    monkeypatch.setattr(money_flow, "MIN_REQUEST_INTERVAL_SECONDS", 0)

    def fail_eastmoney(indicator):
        raise RuntimeError("eastmoney blocked")

    monkeypatch.setattr(money_flow.ak, "stock_individual_fund_flow_rank", fail_eastmoney)
    monkeypatch.setattr(money_flow.ak, "stock_fund_flow_individual", lambda symbol: pd.DataFrame([
        {"股票代码": "301528", "股票简称": "多浦乐", "涨跌幅": "13.62%", "净额": "1.25亿"}
    ]))

    result = money_flow.get_money_flow_rank(indicator="今日", limit=6000, force_refresh=True)

    assert result["status"] == "ok"
    assert result["source"] == "ths_akshare"
    assert result["items"][0]["code"] == "301528"
    assert result["items"][0]["main_net_inflow_yi"] == 1.25


def test_stock_money_flow_uses_rank_fallback(monkeypatch):
    monkeypatch.setattr(money_flow, "MIN_REQUEST_INTERVAL_SECONDS", 0)

    def fail_stock(**_kwargs):
        raise RuntimeError("stock endpoint blocked")

    monkeypatch.setattr(money_flow.ak, "stock_individual_fund_flow", fail_stock)
    monkeypatch.setattr(money_flow.ak, "stock_individual_fund_flow_rank", lambda indicator: pd.DataFrame([
        {"代码": "301528", "名称": "多浦乐", "主力净流入-净额": 90000000, "主力净流入-净占比": 5.2}
    ]))

    result = money_flow.get_stock_money_flow("301528", force_refresh=True)

    assert result["status"] == "ok"
    assert result["rank_fallback"] is True
    assert result["latest"]["main_net_inflow_yi"] == 0.9
