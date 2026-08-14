import os
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.models import Base
from core.next_day_confirmation import (
    _evaluate_a_eod_t1_plan,
    evaluate_next_day_reviews,
    load_pending_next_day_reviews,
    send_next_day_confirmation,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_load_pending_reviews_unions_failed_after_close_delivery_candidates(monkeypatch):
    from core import next_day_confirmation as module

    engine = _engine()
    prior = date(2026, 8, 7)
    monkeypatch.setattr(module, "previous_a_share_trading_date", lambda value: prior.isoformat())
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO recommendation_events (
                event_date,event_time,source,code,name,strategy_type,
                recommendation_price,pa_entry_price,pa_stop_price
            ) VALUES (:day,:at,'bark_next_day','000001','测试股','tv_dual_strict',10,10.2,9.5)
        """), {"day": prior, "at": datetime.combine(prior, datetime.min.time())})

    pending = load_pending_next_day_reviews(engine)

    assert len(pending) == 1
    assert pending[0]["code"] == "000001"
    assert pending[0]["confirmation_price"] == 10.2


def test_load_pending_review_prefers_rich_frozen_snapshot(monkeypatch):
    from core import next_day_confirmation as module
    from core.signal_performance import save_intraday_signal_snapshots

    engine = _engine()
    signal_day = date(2026, 8, 6)
    monkeypatch.setattr(module, "previous_a_share_trading_date", lambda value: signal_day.isoformat())
    candidate = {
        "代码": "000001", "名称": "冻结计划", "strategy_type": "tv_dual_strict",
        "sop_grade": "M", "现价": 10.0, "pa_entry_price": 10.2,
        "pa_stop_price": 9.5, "pa_target_price": 11.6,
        "trade_bucket": "OBSERVE", "trade_eligible": False,
        "execution_review_state": "NEXT_DAY_REVIEW", "a_eod_t1_plan": True,
        "a_eod_t1_policy_version": "a-eod-t1-confirmation-v1",
        "a_eod_t1_frozen_entry_price": 10.2,
        "a_eod_t1_frozen_stop_price": 9.5,
        "a_eod_t1_frozen_target_price": 11.6,
    }
    save_intraday_signal_snapshots(
        [candidate], engine, "bark_next_day", datetime(2026, 8, 6, 15, 10),
    )

    pending = load_pending_next_day_reviews(engine, as_of=date(2026, 8, 7))

    assert len(pending) == 1
    assert pending[0]["a_eod_t1_plan"] is True
    assert pending[0]["confirmation_price"] == 10.2
    assert pending[0]["stop_price"] == 9.5
    assert pending[0]["target_price"] == 11.6


def test_evaluate_next_day_review_never_promotes_without_fresh_trade_state():
    pending = [{"code": "000001", "name": "测试股", "strategy_type": "tv_dual_strict"}]
    waiting = evaluate_next_day_reviews(pending, [{
        "代码": "000001", "strategy_type": "tv_dual_strict",
        "trade_eligible": False, "trade_bucket": "OBSERVE",
        "execution_review_state": "NEXT_DAY_REVIEW",
        "trade_blockers": ["量能未确认"],
    }])
    confirmed = evaluate_next_day_reviews(pending, [{
        "代码": "000001", "strategy_type": "tv_dual_strict",
        "trade_eligible": True, "trade_bucket": "TRADE",
        "execution_review_state": "TRADE", "现价": 10.3,
    }])

    assert waiting[0]["instruction"] == "不可交易"
    assert waiting[0]["status"] == "CONTINUE_WAIT"
    assert confirmed[0]["instruction"] == "可交易"
    assert confirmed[0]["status"] == "CONFIRMED_TRADE"


def test_a_eod_t1_plan_uses_frozen_trigger_without_fresh_reselection():
    prior = {
        "code": "000001", "name": "测试股", "strategy_type": "tv_dual_strict",
        "signal_price": 10.0, "confirmation_price": 10.2, "stop_price": 9.5,
        "target_price": 11.6, "sop_grade": "M", "a_eod_t1_plan": True,
        "a_eod_t1_policy_version": "a-eod-t1-confirmation-v1",
    }
    current = {
        "代码": "000001", "名称": "测试股", "strategy_type": "tv_dual_strict",
        "现价": 10.30, "开盘": 10.10, "最高": 10.35, "最低": 10.02,
        "涨幅%": 3.0, "limit_up": 11.0,
        "trade_eligible": False, "trade_bucket": "OBSERVE",
    }

    reviewed = _evaluate_a_eod_t1_plan(prior, current)

    assert reviewed["status"] == "CONFIRMED_TRADE"
    assert reviewed["instruction"] == "可交易"
    assert reviewed["current_candidate"]["trade_eligible"] is True
    assert reviewed["current_candidate"]["trade_bucket"] == "TRADE"
    assert reviewed["current_candidate"]["a_eod_t1_confirmed"] is True
    assert reviewed["current_candidate"]["position_plan"]["initial_position_pct"] == 2


def test_a_eod_t1_plan_rejects_overextension_limit_lock_and_stop_break():
    prior = {
        "code": "000001", "strategy_type": "tv_dual_strict",
        "signal_price": 10.0, "confirmation_price": 10.2, "stop_price": 9.5,
        "a_eod_t1_plan": True,
    }
    base = {
        "代码": "000001", "现价": 10.3, "开盘": 10.1, "最高": 10.35,
        "最低": 10.0, "涨幅%": 3.0, "limit_up": 11.0,
    }

    overextended = _evaluate_a_eod_t1_plan(prior, {**base, "开盘": 10.6, "现价": 10.6, "最高": 10.7})
    limit_locked = _evaluate_a_eod_t1_plan(prior, {**base, "现价": 11.0, "最高": 11.0})
    stop_broken = _evaluate_a_eod_t1_plan(prior, {**base, "最低": 9.4})
    faded = _evaluate_a_eod_t1_plan(prior, {**base, "开盘": 10.3, "最高": 10.4, "现价": 10.1})

    assert overextended["instruction"] == "不可交易"
    assert "偏离" in overextended["reason"]
    assert limit_locked["instruction"] == "不可交易"
    assert "涨停" in limit_locked["reason"]
    assert stop_broken["instruction"] == "不可交易"
    assert "失效" in stop_broken["reason"]
    assert faded["instruction"] == "不可交易"
    assert "跌回" in faded["reason"]


def test_confirmation_push_creates_intent_only_after_successful_bark(monkeypatch):
    from core import next_day_confirmation as module

    pending = [{
        "signal_date": "2026-07-16", "code": "000001", "name": "测试股",
        "strategy_type": "tv_dual_strict", "confirmation_price": 10.2,
    }]
    current = [{
        "代码": "000001", "名称": "测试股", "strategy_type": "tv_dual_strict",
        "trade_eligible": True, "trade_bucket": "TRADE",
        "execution_review_state": "TRADE", "现价": 10.3,
    }]
    sent = []
    intents = []

    monkeypatch.setattr(module, "load_pending_next_day_reviews", lambda engine, as_of=None: pending)

    async def fake_send(title, body, **kwargs):
        sent.append((title, body, kwargs))
        return {"bark": True}

    monkeypatch.setattr("core.notifier.notifier.send", fake_send)
    monkeypatch.setattr("core.audit_log.record_lifecycle_event", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "core.execution_intents.create_bark_execution_intents",
        lambda candidates, engine, issued_at=None: intents.extend(candidates) or ["intent"],
    )

    result = send_next_day_confirmation(object(), current, now=datetime(2026, 7, 17, 10, 30))

    assert result["confirmed"] == 1
    assert result["bark"] is True
    assert "指令：可交易" in sent[0][1]
    assert sent[0][2]["enqueue_failed"] is False
    assert intents == current


def test_confirmation_stays_silent_when_no_stock_is_tradeable(monkeypatch):
    from core import next_day_confirmation as module

    pending = [{
        "signal_date": "2026-08-07", "code": "000001", "name": "观察股",
        "strategy_type": "tv_dual", "confirmation_price": 10.2,
    }]
    current = [{
        "代码": "000001", "名称": "观察股", "strategy_type": "tv_dual",
        "trade_eligible": False, "trade_bucket": "OBSERVE",
        "execution_review_state": "NEXT_DAY_REVIEW", "现价": 10.1,
    }]
    sent = []

    monkeypatch.setattr(module, "load_pending_next_day_reviews", lambda engine, as_of=None: pending)

    async def fake_send(*args, **kwargs):
        sent.append((args, kwargs))
        return {"bark": True}

    monkeypatch.setattr("core.notifier.notifier.send", fake_send)
    monkeypatch.setattr("core.audit_log.record_lifecycle_event", lambda *args, **kwargs: True)

    result = send_next_day_confirmation(object(), current, now=datetime(2026, 8, 10, 10, 30))

    assert result["reviewed"] == 1
    assert result["confirmed"] == 0
    assert result["bark"] is False
    assert result["reason"] == "no_confirmed_trade"
    assert sent == []
