import os
import sys

from sqlalchemy import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.models import Base
from routers import stock as stock_router


def test_research_thesis_persists_immutable_snapshot(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(stock_router, "get_db_engine", lambda: engine)
    monkeypatch.setattr(stock_router, "get_cached_stock_research_signals", lambda code: {
        "updated_at": "2026-07-10T10:00:00",
        "summary": {"risk_flags": ["解禁风险"]},
    })
    payload = stock_router.ResearchThesisCreate(
        title="回踩确认研究",
        thesis_text="板块启动，等待个股回踩确认。",
        catalysts=["板块扩散"],
        risks=["解禁风险"],
        confirmation_condition="放量站回确认价",
        invalidation_condition="跌破结构止损",
        strategy_type="early_value",
    )

    created = stock_router.create_research_thesis("000001", payload)
    listed = stock_router.list_research_theses("000001")

    assert created["status"] == "ok"
    assert listed["count"] == 1
    assert listed["items"][0]["thesis_text"] == "板块启动，等待个股回踩确认。"
    assert listed["items"][0]["confirmation_condition"] == "放量站回确认价"
