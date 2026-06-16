import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.performance_metrics import return_metrics
from core.pro_workflow import classify_strategy_health
from core.score_calibration import calibrate_scan_scores
from core.strategy_health import apply_strategy_health_controls


def test_score_calibration_preserves_raw_score_and_bounds_output():
    rows = [
        {
            "strategy_type": "squeeze", "Score": 500, "price_action_score": 70,
            "sector_alignment_score": 80, "trade_opportunity_score": 75,
            "pa_trade_action": "READY", "trade_bucket": "TRADE", "market_regime": "OFFENSIVE",
        },
        {
            "strategy_type": "squeeze", "Score": 100, "price_action_score": 60,
            "sector_alignment_score": 60, "trade_opportunity_score": 60,
            "pa_trade_action": "WATCH", "trade_bucket": "OBSERVE", "market_regime": "OFFENSIVE",
        },
        {
            "strategy_type": "pine", "Score": 88, "price_action_score": 70,
            "sector_alignment_score": 70, "trade_opportunity_score": 70,
            "pa_trade_action": "READY", "trade_bucket": "TRADE", "market_regime": "OFFENSIVE",
        },
    ]

    calibrate_scan_scores(rows)

    assert rows[0]["raw_score"] == 500
    assert all(0 <= row["Score"] <= 100 for row in rows)
    assert rows[0]["Score"] > rows[1]["Score"]
    assert all(row["research_eligible"] for row in rows)


def test_incomplete_scan_sample_is_not_research_eligible():
    rows = [{"strategy_type": "pine", "Score": 80}]

    calibrate_scan_scores(rows)

    assert rows[0]["research_eligible"] is False
    assert "pa_trade_action" in rows[0]["research_missing_fields"]


def test_return_metrics_and_strategy_health_use_net_expectation():
    metrics = return_metrics(pd.Series([4, 2, -1, -1]))

    assert metrics["expected_return"] == 1.0
    assert metrics["profit_loss_ratio"] == 3.0
    assert classify_strategy_health({**metrics, "signals": 30})["status"] == "ACTIVE"

    paused = classify_strategy_health({
        "signals": 40, "expected_return": -1.2, "ci95_high": -0.2, "win_rate": 35,
    })
    assert paused["status"] == "PAUSED"


def test_paused_strategy_is_removed_from_trade_pool():
    rows = [{"strategy_type": "pine", "trade_eligible": True, "trade_bucket": "TRADE", "trade_blockers": []}]

    apply_strategy_health_controls(rows, {
        "strategies": {"pine": {"status": "PAUSED", "reason": "近期净期望显著为负"}},
    })

    assert rows[0]["trade_eligible"] is False
    assert rows[0]["trade_bucket"] == "OBSERVE"
    assert "自动暂停" in rows[0]["trade_blockers"][0]
