import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.decision_semantics import apply_decision_semantics
from core.strategy_health import apply_strategy_health_controls


def test_a_grade_semantics_distinguish_structure_early_and_trade():
    rows = [
        {"sop_grade": "A", "trade_bucket": "OBSERVE", "trade_eligible": False},
        {"sop_grade": "A", "trade_bucket": "EARLY", "trade_eligible": False},
        {"sop_grade": "A", "trade_bucket": "TRADE", "trade_eligible": True},
    ]

    apply_decision_semantics(rows)

    assert [row["grade_stage"] for row in rows] == ["A-STRUCTURE", "A-EARLY", "A-TRADE"]
    assert rows[0]["confirmation_event_state"] == "WAIT_PRICE_CONFIRMATION"
    assert rows[2]["decision_lifecycle_state"] == "ENTRY_CONFIRMED"


def test_segment_health_overrides_strategy_health_when_mature():
    rows = [{
        "strategy_type": "strict",
        "market_regime": "OFFENSIVE",
        "sector_phase": "SECTOR_EARLY",
        "trade_eligible": True,
        "trade_bucket": "TRADE",
        "trade_blockers": [],
        "calibrated_score": 90,
        "Score": 90,
    }]
    health = {
        "strategies": {"strict": {"signals": 50, "status": "PAUSED", "reason": "整体偏弱"}},
        "segments": {
            "strict|OFFENSIVE|SECTOR_EARLY": {
                "signals": 20, "status": "ACTIVE", "reason": "当前环境有效"
            }
        },
    }

    apply_strategy_health_controls(rows, health)

    assert rows[0]["trade_eligible"] is True
    assert rows[0]["strategy_health_scope"] == "segment"


def test_early_value_semantics_distinguish_sector_pending_and_confirmed():
    rows = [
        {
            "strategy_type": "early_value",
            "sop_grade": "B",
            "trade_bucket": "OBSERVE",
            "trade_eligible": False,
            "early_value_sector_pending": True,
        },
        {
            "strategy_type": "early_value",
            "sop_grade": "B",
            "trade_bucket": "OBSERVE",
            "trade_eligible": False,
            "early_value_sector_confirmed": True,
        },
    ]

    apply_decision_semantics(rows)

    assert rows[0]["early_value_transition_state"] == "TECHNICAL_MATCH_SECTOR_PENDING"
    assert rows[1]["early_value_transition_state"] == "SECTOR_CONFIRMED_WAIT_PRICE"
