import os
import sys

import pytest
from pydantic import ValidationError
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
        title="Serenity 卡脖子研究",
        thesis_text="板块启动，等待个股回踩确认。",
        catalysts=["板块扩散"],
        risks=["解禁风险"],
        confirmation_condition="放量站回确认价",
        invalidation_condition="跌破结构止损",
        strategy_type="early_value",
        serenity_snapshot={
            "sector_cycle": "SUPPLY_CONSTRAINED",
            "supply_chain_path": ["终端产品", "核心零部件", "关键材料"],
            "bottleneck_node": "关键材料",
            "bottleneck_reason": "扩产周期长且国内自给率低。",
            "authenticity": "PARTIAL",
            "evidence_urls": ["https://example.com/source"],
            "observed_at": "2026-07-10T10:30:00+08:00",
        },
    )

    created = stock_router.create_research_thesis("000001", payload)
    listed = stock_router.list_research_theses("000001")

    assert created["status"] == "ok"
    assert listed["count"] == 1
    assert listed["items"][0]["thesis_text"] == "板块启动，等待个股回踩确认。"
    assert listed["items"][0]["confirmation_condition"] == "放量站回确认价"
    snapshot = listed["items"][0]["serenity_snapshot"]
    assert snapshot["mode"] == "SHADOW"
    assert snapshot["score_effect"] == 0
    assert snapshot["trade_eligible"] is False
    assert snapshot["bottleneck_node"] == "关键材料"
    assert snapshot["evidence_urls"] == ["https://example.com/source"]


def test_serenity_snapshot_rejects_trade_fields():
    with pytest.raises(ValidationError):
        stock_router.ResearchThesisCreate(
            thesis_text="只用于研究观察。",
            serenity_snapshot={
                "bottleneck_node": "关键材料",
                "trade_eligible": True,
            },
        )
