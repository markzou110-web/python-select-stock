import json
import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.execution_intents import (
    build_execution_attribution,
    create_bark_execution_intents,
    expire_due_execution_intents,
    get_execution_intent,
    transition_execution_intent,
)
from core.models import Base


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _stock(tradable=True):
    return {
        "代码": "000001", "名称": "测试", "strategy_type": "tv_dual_strict",
        "trade_eligible": tradable, "trade_bucket": "TRADE" if tradable else "WATCH",
        "sop_grade": "A", "Score": 90, "trade_opportunity_score": 80,
        "pa_entry_price": 10.0, "pa_stop_price": 9.1, "pa_target_price": 12.0,
        "suggested_position_pct": 5,
        "evidence_id": "ev_test", "evidence_grade": "A", "evidence_status": "PASS",
        "evidence_reason_codes": [],
    }


def test_only_delivered_tradable_candidate_creates_idempotent_intent():
    engine = _engine()
    issued = datetime(2026, 7, 10, 14, 20)
    first = create_bark_execution_intents([_stock(), _stock(False)], engine, issued_at=issued)
    duplicate = create_bark_execution_intents([_stock()], engine, issued_at=issued)
    assert len(first) == 1
    assert duplicate == []
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM execution_intents")).scalar() == 1
        assert conn.execute(text("SELECT instruction FROM execution_intents")).scalar() == "可交易"
    snapshot = json.loads(get_execution_intent(engine, first[0])["intent"]["signal_snapshot"])
    assert snapshot["evidence_id"] == "ev_test"
    assert snapshot["evidence_grade"] == "A"


def test_intent_lifecycle_records_timeline_and_slippage():
    engine = _engine()
    intent_id = create_bark_execution_intents([_stock()], engine, issued_at=datetime(2026, 7, 10, 14, 20))[0]
    assert transition_execution_intent(engine, intent_id, "SEEN", {})["changed"]
    assert transition_execution_intent(engine, intent_id, "ACCEPTED", {})["changed"]
    assert transition_execution_intent(engine, intent_id, "ORDERED", {"shares": 500})["changed"]
    filled = transition_execution_intent(engine, intent_id, "FILLED", {"shares": 500, "actual_price": 10.1})
    assert filled["slippage_pct"] == 1.0
    detail = get_execution_intent(engine, intent_id)
    assert detail["intent"]["state"] == "FILLED"
    assert [event["to_state"] for event in detail["events"]] == ["ISSUED", "SEEN", "ACCEPTED", "ORDERED", "FILLED"]


def test_invalid_jump_and_overfill_are_rejected():
    engine = _engine()
    intent_id = create_bark_execution_intents([_stock()], engine, issued_at=datetime(2026, 7, 10, 14, 20))[0]
    assert transition_execution_intent(engine, intent_id, "FILLED", {"shares": 100, "actual_price": 10})["error"] == "transition_not_allowed"
    transition_execution_intent(engine, intent_id, "ACCEPTED", {})
    transition_execution_intent(engine, intent_id, "ORDERED", {"shares": 100})
    assert transition_execution_intent(engine, intent_id, "PARTIAL", {"shares": 200, "actual_price": 10})["error"] == "filled_shares_exceed_order"


def test_due_intent_expires_with_audit_event():
    engine = _engine()
    intent_id = create_bark_execution_intents([_stock()], engine, issued_at=datetime(2026, 7, 10, 14, 20))[0]
    assert expire_due_execution_intents(engine, now=datetime(2026, 7, 10, 15, 10)) == 1
    detail = get_execution_intent(engine, intent_id)
    assert detail["intent"]["state"] == "EXPIRED"
    assert detail["events"][-1]["to_state"] == "EXPIRED"


def test_execution_attribution_separates_fill_rate_from_strategy_win_rate():
    engine = _engine()
    first = create_bark_execution_intents([_stock()], engine, issued_at=datetime.now())[0]
    second_stock = {**_stock(), "代码": "000002"}
    second = create_bark_execution_intents([second_stock], engine, issued_at=datetime.now())[0]
    transition_execution_intent(engine, first, "ACCEPTED", {})
    transition_execution_intent(engine, first, "ORDERED", {"shares": 100})
    transition_execution_intent(engine, first, "FILLED", {"shares": 100, "actual_price": 10.1})
    transition_execution_intent(engine, second, "SKIPPED", {"note": "人工放弃"})
    report = build_execution_attribution(engine, days=30)
    assert report["total_intents"] == 2
    assert report["fill_rate_pct"] == 50.0
    assert report["skip_rate_pct"] == 50.0
    assert "不等于策略胜率" in report["note"]
