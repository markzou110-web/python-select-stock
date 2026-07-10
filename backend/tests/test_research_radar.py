import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import research_radar


def test_candidate_research_radar_classifies_and_dedupes(monkeypatch):
    monkeypatch.setattr(research_radar, "get_cached_data", lambda *args: None)
    monkeypatch.setattr(research_radar, "set_cached_data", lambda *args: None)
    monkeypatch.setattr(research_radar, "_load_research_targets", lambda engine, limit: [{
        "code": "000001",
        "name": "平安银行",
        "industry": "银行",
        "scopes": ["WATCHLIST", "SCAN"],
    }])
    monkeypatch.setattr(research_radar, "eastmoney_stock_news", lambda code, page_size: [
        {"title": "公司获得重大订单", "time": "2026-07-10", "source": "测试", "url": "n1"},
    ])
    monkeypatch.setattr(research_radar, "cninfo_announcements", lambda code, page_size: [
        {"title": "公司获得重大订单", "date": "2026-07-10", "type": "公告", "url": "a1"},
        {"title": "股东减持计划公告", "date": "2026-07-09", "type": "公告", "url": "a2"},
    ])

    payload = research_radar.build_candidate_research_radar(object(), limit=5)

    assert payload["summary"] == {
        "targets": 1,
        "evidence": 2,
        "risk": 1,
        "catalyst": 1,
        "partial_targets": 0,
    }
    assert {item["kind"] for item in payload["items"][0]["evidence"]} == {"RISK", "CATALYST"}
    assert payload["policy"].startswith("研究证据只展示")
