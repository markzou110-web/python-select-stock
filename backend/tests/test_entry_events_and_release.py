import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.entry_events import classify_entry_event, save_entry_event
from core.models import Base
from core.strategy_release import evaluate_challenger, select_champion


def test_entry_confirmation_states_are_explicit():
    assert classify_entry_event(9.96, 10.0) == "APPROACHED"
    assert classify_entry_event(10.1, 10.0) == "BROKE_OUT"
    assert classify_entry_event(10.1, 10.0, close_confirmed=True) == "CLOSE_CONFIRMED"
    assert classify_entry_event(8.9, 10.0, invalidation_price=9.0) == "INVALIDATED"


def test_entry_event_is_appended_to_lifecycle_stream():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    assert save_entry_event(engine, {
        "event_type": "CLOSE_CONFIRMED", "code": "000001", "strategy_type": "tv_dual_strict",
        "confirmation_price": 10.0, "current_price": 10.1,
    })
    with engine.connect() as conn:
        row = conn.execute(text("SELECT event_type, payload FROM lifecycle_events")).one()
    assert row[0] == "CLOSE_CONFIRMED"
    assert "confirmation-event-v1" in row[1]


def test_challenger_requires_all_oos_gates():
    good = evaluate_challenger({
        "oos_trades": 120, "consecutive_positive_windows": 3, "net_expectancy": 1.0,
        "profit_factor": 1.5, "alpha": 2.0, "drawdown_within_budget": True,
    })
    assert good["eligible"] is True
    assert evaluate_challenger({"oos_trades": 10})["status"] == "SHADOW"
    assert select_champion([{"status": "CHAMPION", "name": "base"}])["name"] == "base"
