import os
import sys
import json
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.strategy_governance import transition_strategy


def _engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE strategy_release_states(strategy_key TEXT PRIMARY KEY,state TEXT,version TEXT,evidence TEXT,reason TEXT,updated_at TIMESTAMP)"))
        c.execute(text("CREATE TABLE backtest_experiments(experiment_id TEXT PRIMARY KEY,experiment_type TEXT,strategy_type TEXT,code_version TEXT,data_hash TEXT,result_payload TEXT)"))
    return engine


def _artifact(engine, experiment_id, kind, result):
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO backtest_experiments VALUES (:id,:kind,'squeeze','abc123','data123',:result)
        """), {"id": experiment_id, "kind": kind, "result": json.dumps(result)})


def test_strategy_lifecycle_requires_order_and_evidence():
    engine = _engine()
    assert transition_strategy(engine, "shadow-v1", "SHADOW", {})["changed"] is True
    denied = transition_strategy(engine, "shadow-v1", "CHAMPION", {"mature_signals": 100, "avg_return": 2, "profit_factor": 1.5})
    assert denied["error"] == "transition_not_allowed"


def test_paper_rejects_manual_metrics_without_experiment():
    engine = _engine()
    transition_strategy(engine, "squeeze-v1", "SHADOW", {})
    transition_strategy(engine, "squeeze-v1", "CHALLENGER", {})
    denied = transition_strategy(engine, "squeeze-v1", "PAPER", {
        "mature_signals": 999, "avg_return": 10, "profit_factor": 5,
    })
    assert denied["error"] == "experiment_evidence_not_met"
    assert "experiment_id" in denied["reason"]


def test_verified_walk_forward_artifact_can_promote_to_paper():
    engine = _engine()
    _artifact(engine, "exp_walk", "walk_forward", {
        "items": [{"out_of_sample": {"signal_count": 40, "avg_return": 1.2, "profit_factor": 1.4}}],
    })
    transition_strategy(engine, "squeeze-v1", "SHADOW", {})
    transition_strategy(engine, "squeeze-v1", "CHALLENGER", {})
    promoted = transition_strategy(engine, "squeeze-v1", "PAPER", {
        "experiment_id": "exp_walk", "strategy_type": "squeeze",
        "mature_signals": 9999,
    })
    assert promoted["changed"] is True
    assert promoted["evidence"]["mature_signals"] == 40
    assert promoted["evidence"]["source"] == "immutable_experiment"


def test_limited_live_requires_positive_rolling_artifact():
    engine = _engine()
    _artifact(engine, "exp_walk", "walk_forward", {
        "items": [{"out_of_sample": {"signal_count": 40, "avg_return": 1.2, "profit_factor": 1.4}}],
    })
    _artifact(engine, "exp_roll", "rolling_walk_forward", {
        "summary": {"test_signals": 60, "oos_weighted_avg_return": 0.8, "oos_weighted_profit_factor": 1.3},
    })
    transition_strategy(engine, "squeeze-v1", "SHADOW", {})
    transition_strategy(engine, "squeeze-v1", "CHALLENGER", {})
    transition_strategy(engine, "squeeze-v1", "PAPER", {"experiment_id": "exp_walk", "strategy_type": "squeeze"})
    promoted = transition_strategy(engine, "squeeze-v1", "LIMITED_LIVE", {
        "experiment_id": "exp_roll", "strategy_type": "squeeze",
    })
    assert promoted["changed"] is True
    assert promoted["evidence"]["avg_return"] == 0.8
