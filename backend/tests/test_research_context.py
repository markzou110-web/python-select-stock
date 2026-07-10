import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import research_context


def test_global_market_context_selects_expected_indices(monkeypatch):
    monkeypatch.setattr(research_context, "get_cached_data", lambda *args: None)
    monkeypatch.setattr(research_context, "set_cached_data", lambda *args: None)
    monkeypatch.setattr(research_context.ak, "index_global_spot_em", lambda: pd.DataFrame([
        {"名称": "纳斯达克综合指数", "最新价": 20000, "涨跌幅": 1.2, "最新行情时间": "2026-07-10"},
        {"名称": "恒生科技指数", "最新价": 5000, "涨跌幅": -0.5, "最新行情时间": "2026-07-10"},
    ]))

    payload = research_context.get_global_market_context(force_refresh=True)

    assert payload["status"] == "ok"
    assert {item["name"] for item in payload["items"]} == {"纳斯达克", "恒生科技"}


def test_ai_research_context_is_read_only_and_bounded():
    payload = research_context.build_ai_research_context(
        domestic_indices={"上证": {"pct": 1}},
        global_market={"items": []},
        daily_report={"scan_date": "2026-07-10", "summary": {"trade_count": 0}},
        strategy_health={"strategies": {"strict": {"status": "PAUSED"}}},
        research_radar={"summary": {"targets": 1}, "items": [{
            "code": "000001", "evidence": [{"title": str(index)} for index in range(10)]
        }]},
    )

    assert payload["contract_version"] == "ai-research-context-v1"
    assert len(payload["candidate_evidence"]["items"][0]["evidence"]) == 4
    assert "不修改策略参数" in payload["policy"]


def test_global_market_context_falls_back_to_tencent(monkeypatch):
    class Response:
        text = 'v_usIXIC="200~Nasdaq~.IXIC~20000~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~2026-07-10~0~1.25";'
        encoding = ""

        def raise_for_status(self):
            return None

    monkeypatch.setattr(research_context, "get_cached_data", lambda *args: None)
    monkeypatch.setattr(research_context, "get_stale_cache", lambda *args: None)
    monkeypatch.setattr(research_context, "set_cached_data", lambda *args: None)
    monkeypatch.setattr(research_context.ak, "index_global_spot_em", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(research_context.requests, "get", lambda *args, **kwargs: Response())

    payload = research_context.get_global_market_context(force_refresh=True)

    assert payload["source"] == "Tencent qt.gtimg.cn"
    assert payload["items"][0]["name"] == "纳斯达克"
    assert payload["items"][0]["pct"] == 1.25
