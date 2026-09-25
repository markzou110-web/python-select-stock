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


def test_intent_persists_risk_normalized_position_plan_when_legacy_field_is_missing():
    engine = _engine()
    stock = _stock()
    stock.pop("suggested_position_pct")
    stock["现价"] = 10.1
    stock["position_plan"] = {
        "initial_position_pct": 4.5,
        "max_position_pct": 4.5,
        "risk_budget_pct": 1.0,
    }
    stock["execution_instruction"] = "站稳10.00后执行4.5%，跌破9.10退出"

    intent_id = create_bark_execution_intents(
        [stock], engine, issued_at=datetime(2026, 7, 10, 14, 20),
    )[0]
    detail = get_execution_intent(engine, intent_id)
    snapshot = json.loads(detail["intent"]["signal_snapshot"])

    assert detail["intent"]["planned_position_pct"] == 4.5
    assert snapshot["signal_price"] == 10.1
    assert snapshot["position_plan"]["risk_budget_pct"] == 1.0
    assert "4.5%" in snapshot["execution_instruction"]


def test_intent_persists_a_minus_trial_policy_for_health_loop():
    engine = _engine()
    stock = _stock()
    stock.update({
        "sop_grade": "B",
        "a_minus_trial": True,
        "a_minus_trial_grade": "A-",
        "a_minus_trial_policy_version": "a-minus-controlled-trial-v1",
        "position_plan": {"initial_position_pct": 5, "max_position_pct": 5},
    })

    intent_id = create_bark_execution_intents(
        [stock], engine, issued_at=datetime(2026, 7, 10, 14, 20),
    )[0]
    snapshot = json.loads(get_execution_intent(engine, intent_id)["intent"]["signal_snapshot"])

    assert snapshot["a_minus_trial"] is True
    assert snapshot["a_minus_trial_policy_version"] == "a-minus-controlled-trial-v1"


def test_intent_persists_a_eod_controlled_policy_and_limits():
    engine = _engine()
    stock = _stock()
    stock.update({
        "sop_grade": "B",
        "a_eod_controlled_trial": True,
        "a_eod_policy_version": "a-eod-controlled-trial-v1",
        "a_eod_trade_cautions": ["周线中性，降级观察"],
        "a_eod_portfolio_cap_pct": 15,
        "a_eod_max_positions": 3,
        "position_plan": {"initial_position_pct": 5, "max_position_pct": 5},
    })

    intent_id = create_bark_execution_intents(
        [stock], engine, issued_at=datetime(2026, 8, 6, 14, 30),
    )[0]
    snapshot = json.loads(get_execution_intent(engine, intent_id)["intent"]["signal_snapshot"])

    assert snapshot["a_eod_controlled_trial"] is True
    assert snapshot["a_eod_policy_version"] == "a-eod-controlled-trial-v1"
    assert snapshot["a_eod_trade_cautions"] == ["周线中性，降级观察"]
    assert snapshot["a_eod_portfolio_cap_pct"] == 15
    assert snapshot["a_eod_max_positions"] == 3


def test_intent_persists_a_eod_t1_confirmation_policy():
    engine = _engine()
    stock = _stock()
    stock.update({
        "a_eod_t1_confirmed": True,
        "a_eod_t1_policy_version": "a-eod-t1-confirmation-v1",
        "a_eod_t1_frozen_entry_price": 9.9,
        "a_eod_t1_entry_extension_pct": 1.01,
        "a_eod_t1_portfolio_cap_pct": 6,
        "a_eod_t1_max_positions": 3,
        "pa_execution_policy_version": "pa-execution-tier-v1",
        "pa_execution_tier": "T1_CONFIRM",
        "pa_execution_tier_label": "次日确认",
        "position_plan": {"initial_position_pct": 2, "max_position_pct": 2},
    })

    intent_id = create_bark_execution_intents(
        [stock], engine, issued_at=datetime(2026, 8, 7, 10, 30),
    )[0]
    snapshot = json.loads(get_execution_intent(engine, intent_id)["intent"]["signal_snapshot"])

    assert snapshot["a_eod_t1_confirmed"] is True
    assert snapshot["a_eod_t1_policy_version"] == "a-eod-t1-confirmation-v1"
    assert snapshot["a_eod_t1_frozen_entry_price"] == 9.9
    assert snapshot["a_eod_t1_portfolio_cap_pct"] == 6
    assert snapshot["a_eod_t1_max_positions"] == 3
    assert snapshot["pa_execution_policy_version"] == "pa-execution-tier-v1"
    assert snapshot["pa_execution_tier"] == "T1_CONFIRM"


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
