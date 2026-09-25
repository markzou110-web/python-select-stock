"""Wiring tests for the scheduled after-close AI review task."""
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture()
def review_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ai_candidate_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_date VARCHAR(10) NOT NULL,
                code VARCHAR(10) NOT NULL,
                name VARCHAR(40),
                action VARCHAR(10) NOT NULL,
                confidence INTEGER,
                summary TEXT,
                positive_factors TEXT,
                risk_factors TEXT,
                data_limitations TEXT,
                guardrail_adjusted INTEGER DEFAULT 0,
                entry_price FLOAT,
                stop_price FLOAT,
                target_price FLOAT,
                market_summary TEXT,
                model VARCHAR(80),
                source VARCHAR(20) NOT NULL,
                batch_id VARCHAR(48) NOT NULL,
                created_at TIMESTAMP,
                UNIQUE(review_date, code, source)
            )
        """))
        conn.commit()
    return engine


def _scan_rows():
    return [
        {"代码": "000001", "名称": "一号", "trade_bucket": "TRADE", "trade_eligible": True, "display_opportunity_score": 88},
        {"代码": "000002", "名称": "二号", "trade_bucket": "OBSERVE", "trade_eligible": False, "display_opportunity_score": 60},
    ]


def _ai_result():
    return {
        "status": "success",
        "model": "test-model",
        "market_summary": "候选结构偏强，注意量能确认",
        "analyses": [{
            "code": "000001", "name": "一号", "action": "BUY", "confidence": 82,
            "summary": "可进入人工复核", "positive_factors": ["策略共振"], "risk_factors": ["波动"],
            "data_limitations": [], "guardrail_adjusted": False,
            "strategy_rank": 1, "ai_rank": 1,
            "system_levels": {"entry_price": 12.5, "stop_price": 11.4, "target_price": 14.5},
        }],
        "usage": {},
    }


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 11, 18, 10, tzinfo=tz)


def test_daily_ai_review_skips_when_unconfigured(monkeypatch):
    from core import tasks
    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now: True)
    monkeypatch.setattr("core.config.config.is_ai_analysis_configured", lambda: False)

    result = tasks.send_daily_ai_review()

    assert result == {"status": "skipped", "reason": "ai_not_configured"}


def test_daily_ai_review_runs_and_pushes(monkeypatch, review_engine):
    from core import tasks
    from core.db import get_latest_ai_candidate_reviews

    pushed = {}

    async def fake_send(title, body, **kwargs):
        pushed["title"] = title
        pushed["body"] = body
        pushed["group"] = kwargs.get("group")
        return {"bark": True}

    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now: True)
    monkeypatch.setattr(tasks, "datetime", _FixedDateTime)
    monkeypatch.setattr("core.config.config.is_ai_analysis_configured", lambda: True)
    monkeypatch.setattr("core.db.get_db_engine", lambda: review_engine)
    monkeypatch.setattr("core.db.get_scan_dates", lambda engine=None: ["2026-09-11"])
    monkeypatch.setattr("core.db.get_scan_history_by_date", lambda date_str, engine=None: _scan_rows())
    warmed = []
    monkeypatch.setattr(
        "core.stock_research.build_stock_research_signals",
        lambda code, trade_date=None, force_refresh=False: warmed.append(code),
    )
    monkeypatch.setattr("core.ai_stock_analysis.analyze_strategy_candidates", lambda candidates: _ai_result())
    monkeypatch.setattr(tasks.notifier, "send", fake_send)
    monkeypatch.setattr(
        "core.data.get_market_regime",
        lambda: {"status": "OFFENSIVE", "desc": "进攻：双指数均站上20日线"},
    )

    result = tasks.send_daily_ai_review()

    assert result["status"] == "success"
    assert result["bark"] is True
    assert result["scan_date"] == "2026-09-11"
    assert result["candidates"] == 1
    assert result["buy_count"] == 1
    # 研究预热覆盖全部候选
    assert warmed == ["000001", "000002"]
    # 推送的是日报正文，含AI复核区块
    assert pushed["title"] == "收盘AI复核 2026-09-11"
    assert "市场：进攻：双指数均站上20日线" in pushed["body"]
    assert "AI复核（test-model）BUY 1 / WAIT 0 / AVOID 0" in pushed["body"]
    assert "策略#1 → AI#1" in pushed["body"]
    assert "依据：策略共振" in pushed["body"]
    assert pushed["group"] == "AlphaVision_Report"
    # 结果已持久化为 scheduled 批次
    review = get_latest_ai_candidate_reviews("2026-09-11", engine=review_engine)
    assert review["source"] == "scheduled"
    assert review["analyses"][0]["action"] == "BUY"


def test_daily_ai_review_degrades_without_push(monkeypatch, review_engine):
    from core import tasks

    pushed = {}

    async def fake_send(title, body, **kwargs):
        pushed["title"] = title
        return {"bark": True}

    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now: True)
    monkeypatch.setattr(tasks, "datetime", _FixedDateTime)
    monkeypatch.setattr("core.config.config.is_ai_analysis_configured", lambda: True)
    monkeypatch.setattr("core.db.get_db_engine", lambda: review_engine)
    monkeypatch.setattr("core.db.get_scan_dates", lambda engine=None: ["2026-09-11"])
    monkeypatch.setattr("core.db.get_scan_history_by_date", lambda date_str, engine=None: _scan_rows())
    monkeypatch.setattr(
        "core.ai_stock_analysis.analyze_strategy_candidates",
        lambda candidates: {"status": "degraded", "message": "AI分析暂不可用", "analyses": []},
    )
    monkeypatch.setattr(tasks.notifier, "send", fake_send)

    result = tasks.send_daily_ai_review()

    assert result["status"] == "degraded"
    assert "title" not in pushed


def test_daily_ai_review_skips_stale_scan_date(monkeypatch, review_engine):
    from core import tasks

    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now: True)
    monkeypatch.setattr(tasks, "datetime", _FixedDateTime)
    monkeypatch.setattr("core.config.config.is_ai_analysis_configured", lambda: True)
    monkeypatch.setattr("core.db.get_scan_dates", lambda engine=None: ["2026-09-10"])

    result = tasks.send_daily_ai_review()

    assert result == {
        "status": "skipped",
        "reason": "stale_scan_date",
        "scan_date": "2026-09-10",
        "expected_date": "2026-09-11",
    }


def test_daily_ai_review_pushes_reference_report_without_buy(monkeypatch, review_engine):
    from core import tasks

    pushed = []
    wait_result = _ai_result()
    wait_result["analyses"] = [
        {**wait_result["analyses"][0], "action": "WAIT", "confidence": 55},
    ]

    async def fake_send(*args, **kwargs):
        pushed.append((args, kwargs))
        return {"bark": True}

    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now: True)
    monkeypatch.setattr(tasks, "datetime", _FixedDateTime)
    monkeypatch.setattr("core.config.config.is_ai_analysis_configured", lambda: True)
    monkeypatch.setattr("core.db.get_db_engine", lambda: review_engine)
    monkeypatch.setattr("core.db.get_scan_dates", lambda engine=None: ["2026-09-11"])
    monkeypatch.setattr("core.db.get_scan_history_by_date", lambda date_str, engine=None: _scan_rows())
    monkeypatch.setattr(
        "core.stock_research.build_stock_research_signals",
        lambda code, trade_date=None, force_refresh=False: {},
    )
    monkeypatch.setattr("core.ai_stock_analysis.analyze_strategy_candidates", lambda candidates: wait_result)
    monkeypatch.setattr(tasks.notifier, "send", fake_send)
    monkeypatch.setattr(
        "core.data.get_market_regime",
        lambda: {"status": "CRITICAL", "desc": "空仓防守：双指数均跌破20日线"},
    )

    result = tasks.send_daily_ai_review()

    assert result["status"] == "success"
    assert result["bark"] is True
    assert result["reason"] == "no_buy_candidates"
    assert result["buy_count"] == 0
    # 无BUY不再静默：照常推送观察版日报，附确定性市场概况
    assert len(pushed) == 1
    (title, body), kwargs = pushed[0]
    assert title == "收盘AI复核 2026-09-11｜无BUY"
    assert "今日无BUY推荐" in body
    assert "市场：空仓防守：双指数均跌破20日线" in body
    assert kwargs.get("group") == "AlphaVision_Report"


def test_daily_ai_review_attaches_industry_prosperity_to_candidates(monkeypatch, review_engine):
    from core import tasks

    seen = {}

    def fake_analyze(candidates):
        seen["candidates"] = candidates
        return _ai_result()

    async def fake_send(*args, **kwargs):
        return {"bark": True}

    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now: True)
    monkeypatch.setattr(tasks, "datetime", _FixedDateTime)
    monkeypatch.setattr("core.config.config.is_ai_analysis_configured", lambda: True)
    monkeypatch.setattr("core.db.get_db_engine", lambda: review_engine)
    monkeypatch.setattr("core.db.get_scan_dates", lambda engine=None: ["2026-09-11"])
    monkeypatch.setattr(
        "core.db.get_scan_history_by_date",
        lambda date_str, engine=None: [
            {"代码": "000001", "名称": "一号", "行业": "白酒", "ROE": 12.0, "净利YOY": 25.0,
             "trade_bucket": "TRADE", "trade_eligible": True, "display_opportunity_score": 88},
            {"代码": "000002", "名称": "二号", "行业": "白酒", "ROE": 10.0, "净利YOY": 35.0,
             "trade_bucket": "OBSERVE", "trade_eligible": False, "display_opportunity_score": 60},
        ],
    )
    monkeypatch.setattr(
        "core.stock_research.build_stock_research_signals",
        lambda code, trade_date=None, force_refresh=False: {},
    )
    monkeypatch.setattr("core.ai_stock_analysis.analyze_strategy_candidates", fake_analyze)
    monkeypatch.setattr(tasks.notifier, "send", fake_send)
    monkeypatch.setattr(
        "core.data.get_market_regime",
        lambda: {"status": "OFFENSIVE", "desc": "进攻：双指数均站上20日线"},
    )

    result = tasks.send_daily_ai_review()

    assert result["status"] == "success"
    first = seen["candidates"][0]
    assert first["industry_prosperity"]["label"] == "高景气"
    assert first["industry_prosperity"]["roe_median"] == 12.0
    assert first["industry_prosperity"]["sample_count"] == 2
