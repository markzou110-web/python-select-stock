import json
import os
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.a_minus_trial import build_a_minus_trial_health
from core.models import Base


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _insert_trial(engine, idx, returns_pct):
    signal_date = date.today() - timedelta(days=10)
    code = f"{idx:06d}"
    snapshot = json.dumps({"a_minus_trial": True})
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO execution_intents(
                intent_id,signal_date,issued_at,source,code,strategy_type,instruction,state,
                planned_entry_price,signal_snapshot,updated_at
            ) VALUES (:id,:day,:issued,'bark',:code,'tv_dual_strict','可交易','ISSUED',10,:snapshot,:issued)
        """), {
            "id": f"trial-{idx}", "day": signal_date,
            "issued": datetime.combine(signal_date, datetime.min.time()),
            "code": code, "snapshot": snapshot,
        })
        closes = [10.0] * 4 + [10 * (1 + returns_pct / 100)]
        for offset, close in enumerate(closes, start=1):
            conn.execute(text("""
                INSERT INTO daily_k(code,date,open,high,low,close,vol)
                VALUES (:code,:day,:close,:close,:close,:close,100000)
            """), {"code": code, "day": signal_date + timedelta(days=offset), "close": close})


def test_a_minus_health_collects_before_thirty_mature_samples():
    engine = _engine()
    for idx in range(5):
        _insert_trial(engine, idx, 2)

    health = build_a_minus_trial_health(engine)

    assert health["status"] == "COLLECTING"
    assert health["enabled"] is True
    assert health["mature_samples"] == 5


def test_a_minus_health_pauses_after_failed_thirty_sample_gate():
    engine = _engine()
    for idx in range(30):
        _insert_trial(engine, idx, 0.2 if idx < 20 else -2)

    health = build_a_minus_trial_health(engine)

    assert health["status"] == "PAUSED"
    assert health["enabled"] is False
    assert health["mature_samples"] == 30


def test_a_minus_health_marks_profitable_cohort_promotion_eligible():
    engine = _engine()
    for idx in range(30):
        _insert_trial(engine, idx, 2 if idx < 24 else -1)

    health = build_a_minus_trial_health(engine)

    assert health["status"] == "PROMOTION_ELIGIBLE"
    assert health["enabled"] is True
    assert health["avg_return"] >= 0.8
    assert health["profit_factor"] >= 1.3
