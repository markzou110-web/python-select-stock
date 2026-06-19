import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import direct_sources


def setup_function():
    direct_sources._em_last_call = 0.0


def _tencent_line(code="000001", prefix="sz", name="平安银行"):
    values = [""] * 88
    values[1] = name
    values[3] = "10.50"
    values[4] = "10.00"
    values[5] = "10.10"
    values[31] = "0.50"
    values[32] = "5.00"
    values[33] = "10.80"
    values[34] = "10.00"
    values[37] = "12345.60"
    values[38] = "2.50"
    values[39] = "8.90"
    values[43] = "8.00"
    values[44] = "2500.00"
    values[45] = "1800.00"
    values[46] = "0.80"
    values[47] = "11.00"
    values[48] = "9.00"
    values[49] = "1.20"
    values[52] = "9.10"
    return f'v_{prefix}{code}="' + "~".join(values) + '";'


def test_tencent_quote_parses_core_fields(monkeypatch):
    response = MagicMock()
    response.text = _tencent_line()
    response.raise_for_status = MagicMock()

    def fake_get(url, headers, timeout):
        assert "sz000001" in url
        assert timeout == 10
        return response

    monkeypatch.setattr(direct_sources.requests, "get", fake_get)

    result = direct_sources.tencent_quote(["000001"])

    assert result["000001"]["name"] == "平安银行"
    assert result["000001"]["price"] == 10.5
    assert result["000001"]["pe_ttm"] == 8.9
    assert result["000001"]["pb"] == 0.8
    assert result["000001"]["limit_up"] == 11.0


def test_em_get_throttles_and_reuses_session(monkeypatch):
    calls = []
    sleeps = []
    clock = {"value": 100.0}
    response = MagicMock()

    def fake_time():
        return clock["value"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["value"] += seconds

    def fake_uniform(_start, _end):
        return 0.2

    def fake_get(url, params=None, headers=None, timeout=15, **kwargs):
        calls.append({"url": url, "timeout": timeout})
        return response

    monkeypatch.setattr(direct_sources, "EM_MIN_INTERVAL", 1.0)
    monkeypatch.setattr(direct_sources.time, "time", fake_time)
    monkeypatch.setattr(direct_sources.time, "sleep", fake_sleep)
    monkeypatch.setattr(direct_sources.random, "uniform", fake_uniform)
    monkeypatch.setattr(direct_sources.EM_SESSION, "get", fake_get)

    first = direct_sources.em_get("https://push2.eastmoney.com/a")
    second = direct_sources.em_get("https://push2.eastmoney.com/b", timeout=7)

    assert first is response
    assert second is response
    assert len(calls) == 2
    assert calls[1]["timeout"] == 7
    assert sleeps == [1.2]


def test_eastmoney_concept_blocks_normalises_diff_dict(monkeypatch):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "data": {
            "diff": {
                "0": {"f14": "食品饮料", "f12": "BK0438", "f3": "1.23", "f128": "贵州茅台"},
                "1": {"f14": "贵州板块", "f12": "BK0158", "f3": "-0.50", "f128": "振华风光"},
            }
        }
    }
    monkeypatch.setattr(direct_sources, "em_get", lambda *args, **kwargs: response)

    result = direct_sources.eastmoney_concept_blocks("600519")

    assert result["total"] == 2
    assert result["concept_tags"] == ["食品饮料", "贵州板块"]
    assert result["boards"][0]["change_pct"] == 1.23


def test_stock_fund_flow_120d_parses_rows(monkeypatch):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "data": {
            "klines": [
                "2026-06-01,100,10,20,30,40,extra",
                "2026-06-02,-50,-1,-2,-3,-4,extra",
            ]
        }
    }
    monkeypatch.setattr(direct_sources, "em_get", lambda *args, **kwargs: response)

    result = direct_sources.stock_fund_flow_120d("000001")

    assert result == [
        {
            "date": "2026-06-01",
            "main_net": 100.0,
            "small_net": 10.0,
            "mid_net": 20.0,
            "large_net": 30.0,
            "super_net": 40.0,
        },
        {
            "date": "2026-06-02",
            "main_net": -50.0,
            "small_net": -1.0,
            "mid_net": -2.0,
            "large_net": -3.0,
            "super_net": -4.0,
        },
    ]


def test_ths_hot_reason_normalises_rows(monkeypatch):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "errocode": 0,
        "data": [{
            "code": "1",
            "name": "平安银行",
            "reason": "金融科技+银行",
            "zhangfu": "5.5",
            "huanshou": "2.1",
            "chengjiaoe": "123000000",
        }],
    }
    monkeypatch.setattr(direct_sources.requests, "get", lambda *args, **kwargs: response)

    result = direct_sources.ths_hot_reason("2026-06-05")

    assert result[0]["code"] == "000001"
    assert result[0]["reason"] == "金融科技+银行"
    assert result[0]["change_pct"] == 5.5


def test_eastmoney_fund_flow_minute_parses_rows(monkeypatch):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "data": {"klines": ["2026-06-05 09:31,100,10,20,30,40,extra"]}
    }
    monkeypatch.setattr(direct_sources, "em_get", lambda *args, **kwargs: response)

    result = direct_sources.eastmoney_fund_flow_minute("000001")

    assert result == [{
        "time": "2026-06-05 09:31",
        "main_net": 100.0,
        "small_net": 10.0,
        "mid_net": 20.0,
        "large_net": 30.0,
        "super_net": 40.0,
    }]


def test_industry_comparison_parses_ranking(monkeypatch):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "data": {
            "diff": [
                {"f14": "银行", "f12": "BK0475", "f3": "1.2", "f104": "30", "f105": "5", "f140": "平安银行", "f136": "6.0"},
                {"f14": "煤炭", "f12": "BK0437", "f3": "-1.0", "f104": "3", "f105": "28", "f140": "兖矿能源", "f136": "1.0"},
            ]
        }
    }
    monkeypatch.setattr(direct_sources, "em_get", lambda *args, **kwargs: response)

    result = direct_sources.industry_comparison(top_n=1)

    assert result["total"] == 2
    assert result["top"][0]["name"] == "银行"
    assert result["bottom"][0]["name"] == "煤炭"


def test_daily_dragon_tiger_filters_net_buy(monkeypatch):
    monkeypatch.setattr(direct_sources, "eastmoney_datacenter", lambda *args, **kwargs: [
        {"TRADE_DATE": "2026-06-05", "SECURITY_CODE": "000001", "SECURITY_NAME_ABBR": "平安银行", "BILLBOARD_NET_AMT": 60000000},
        {"TRADE_DATE": "2026-06-05", "SECURITY_CODE": "600000", "SECURITY_NAME_ABBR": "浦发银行", "BILLBOARD_NET_AMT": 10000000},
    ])

    result = direct_sources.daily_dragon_tiger("2026-06-05", min_net_buy_wan=5000)

    assert result["total_records"] == 1
    assert result["stocks"][0]["code"] == "000001"
    assert result["stocks"][0]["net_buy_wan"] == 6000.0
