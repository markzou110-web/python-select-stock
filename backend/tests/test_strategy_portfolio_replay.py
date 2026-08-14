import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.strategy_portfolio_replay import (
    PORTFOLIO_CONFIGS,
    compare_portfolio_configs,
    simulate_portfolio,
)


def _prices() -> pd.DataFrame:
    dates = pd.to_datetime(["2026-01-02", "2026-01-03", "2026-01-04"])
    return pd.DataFrame(
        {
            "date": list(dates) * 3,
            "code": ["A"] * 3 + ["B"] * 3 + ["C"] * 3,
            "close": [10.0, 9.0, 11.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
            "volume": [1_000_000] * 9,
        }
    )


def test_preregistered_r1_r2_limits_match_confirmed_document():
    r1, r2 = PORTFOLIO_CONFIGS["R1"], PORTFOLIO_CONFIGS["R2"]

    assert (r1.single_risk_pct, r1.total_risk_pct, r1.sector_risk_pct) == (0.5, 2.0, 1.0)
    assert (r1.single_capital_pct, r1.total_capital_pct) == (15.0, 60.0)
    assert (r2.single_risk_pct, r2.total_risk_pct, r2.sector_risk_pct) == (0.75, 3.0, 1.5)
    assert (r2.single_capital_pct, r2.total_capital_pct) == (20.0, 80.0)


def test_position_size_uses_account_risk_and_marks_equity_daily():
    events = pd.DataFrame(
        [
            {
                "code": "A",
                "industry": "半导体",
                "entry_date": "2026-01-02",
                "exit_date": "2026-01-04",
                "entry_price": 10.0,
                "exit_price": 11.0,
                "stop_price": 9.0,
                "score": 80.0,
            }
        ]
    )

    report = simulate_portfolio(
        events,
        _prices(),
        PORTFOLIO_CONFIGS["R1"],
        initial_capital=100_000,
        benchmark=pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-02", "2026-01-04"]),
                "close": [100.0, 105.0],
            }
        ),
    )

    trade = report["trades"][0]
    assert trade["shares"] == 500
    assert trade["account_risk_amount"] == 500.0
    assert report["daily_equity"][1]["equity"] < 100_000
    assert report["final_equity"] > 100_000
    assert report["max_drawdown_pct"] > 0
    assert report["relative_benchmark_return_pct"] < 0


def test_same_day_candidates_are_ranked_point_in_time_and_capacity_rejections_are_kept():
    events = pd.DataFrame(
        [
            {
                "code": code,
                "industry": "半导体",
                "entry_date": "2026-01-02",
                "exit_date": "2026-01-04",
                "entry_price": 10.0,
                "exit_price": 10.0,
                "stop_price": 9.0,
                "score": score,
            }
            for code, score in (("A", 70.0), ("B", 90.0), ("C", 80.0))
        ]
    )

    report = simulate_portfolio(
        events,
        _prices(),
        PORTFOLIO_CONFIGS["R1"],
        initial_capital=100_000,
    )

    assert [trade["code"] for trade in report["trades"]] == ["B", "C"]
    assert report["unfilled"][0]["code"] == "A"
    assert report["unfilled"][0]["reason"] == "同板块风险额度不足"


def test_open_trade_is_marked_to_market_without_artificial_data_end_exit():
    events = pd.DataFrame(
        [
            {
                "code": "A",
                "industry": "未知",
                "entry_date": "2026-01-02",
                "exit_date": None,
                "entry_price": 10.0,
                "exit_price": None,
                "stop_price": 9.0,
                "score": 80.0,
            }
        ]
    )

    report = simulate_portfolio(
        events,
        _prices(),
        PORTFOLIO_CONFIGS["R1"],
        initial_capital=100_000,
    )

    assert report["open_positions"] == 1
    assert report["trades"][0]["status"] == "OPEN"
    assert report["final_equity"] > 100_000


def test_r0_r1_r2_comparison_does_not_auto_select_without_confirmed_drawdown_budget():
    events = pd.DataFrame(
        [
            {
                "code": "A",
                "industry": "半导体",
                "entry_date": "2026-01-02",
                "exit_date": "2026-01-04",
                "entry_price": 10.0,
                "exit_price": 11.0,
                "stop_price": 9.0,
                "score": 80.0,
            }
        ]
    )

    report = compare_portfolio_configs(events, _prices(), initial_capital=100_000)

    assert set(report["configs"]) == {"R0", "R1", "R2"}
    assert report["selected_config"] is None
    assert report["status"] == "E3_NOT_REACHED"
