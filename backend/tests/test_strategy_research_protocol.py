import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.strategy_research_protocol import (
    apply_confirmation_hysteresis,
    breadth_axis,
    build_causal_breadth_history,
    build_dual_axis_history,
    build_walk_forward_report,
    generate_walk_forward_windows,
    route_permissions,
    select_frozen_test_events,
)


def test_breadth_axis_and_route_permission_matrix_follow_spec():
    assert breadth_axis("ADVANCE") == "B2"
    assert breadth_axis("CLIMAX") == "B2H"
    assert breadth_axis("V_REPAIR") == "B1"
    assert breadth_axis("ICE") == "B0"

    assert route_permissions("T2", "B2")["route_a"] == "CONFIRM"
    assert route_permissions("T2", "B2H")["route_a"] == "PULLBACK_ONLY"
    assert route_permissions("T1", "B2")["route_a"] == "OBSERVE"
    assert route_permissions("T0", "B1")["route_a"] == "BLOCKED"
    assert route_permissions("T2", "B0")["route_b"] == "BLOCKED"
    assert route_permissions("T0", "B1")["route_c"] == "SHADOW_REPAIR"


def test_permission_upgrade_needs_consecutive_days_but_risk_downgrade_is_immediate():
    candidate = pd.Series(
        ["OBSERVE", "CONFIRM", "CONFIRM", "BLOCKED", "CONFIRM", "CONFIRM"],
        index=pd.date_range("2026-01-01", periods=6),
    )

    result = apply_confirmation_hysteresis(candidate, upgrade_days=2)

    assert result.tolist() == ["OBSERVE", "OBSERVE", "CONFIRM", "BLOCKED", "OBSERVE", "CONFIRM"]


def test_dual_axis_history_uses_both_official_indices_and_keeps_1d_2d_variants():
    dates = pd.date_range("2026-01-01", periods=30)
    sh = pd.DataFrame({"date": dates, "close": [100 + idx for idx in range(30)]})
    cyb = pd.DataFrame({"date": dates, "close": [200 + idx for idx in range(30)]})
    breadth = pd.DataFrame(
        {
            "date": dates,
            "market_sentiment_stage": ["REPAIR"] * 27 + ["ADVANCE"] * 3,
        }
    )

    result = build_dual_axis_history(sh, cyb, breadth, start_date="2026-01-20")

    assert result.iloc[-1]["index_axis"] == "T2"
    assert result.iloc[-1]["breadth_axis"] == "B2"
    assert result.iloc[-3]["route_a_permission_1d"] == "CONFIRM"
    assert result.iloc[-3]["route_a_permission_2d"] == "OBSERVE"
    assert result.iloc[-2]["route_a_permission_2d"] == "CONFIRM"


def test_causal_breadth_history_uses_only_rows_available_through_each_day(monkeypatch):
    import core.strategy_research_protocol as protocol

    aggregates = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-02", "2026-01-03"]),
            "advance_ratio": [30.0, 70.0],
            "strong_ratio": [2.0, 8.0],
            "weak_ratio": [8.0, 2.0],
            "avg_return": [-1.0, 1.0],
            "limit_down_count": [10, 0],
        }
    )
    history_lengths = []

    def fake_context(_stocks, market, *, cycle_history, data_date):
        history_lengths.append((data_date, len(cycle_history), market["status"]))
        return {
            "market_sentiment_stage": "RETREAT"
            if market["status"] == "DEFENSIVE"
            else "ADVANCE"
        }

    monkeypatch.setattr(protocol, "build_market_decision_context", fake_context)
    result = build_causal_breadth_history(
        aggregates,
        {
            pd.Timestamp("2026-01-02"): False,
            pd.Timestamp("2026-01-03"): True,
        },
    )

    assert result["market_sentiment_stage"].tolist() == ["RETREAT", "ADVANCE"]
    assert history_lengths == [
        ("2026-01-02", 1, "DEFENSIVE"),
        ("2026-01-03", 2, "OFFENSIVE"),
    ]


def test_walk_forward_windows_use_24_6_3_months_and_step_three_months():
    dates = pd.date_range("2022-05-12", "2026-07-24", freq="B")

    windows = generate_walk_forward_windows(dates)

    assert windows[0]["train_start"] == pd.Timestamp("2022-05-12")
    assert windows[0]["train_end"] == pd.Timestamp("2024-05-11")
    assert windows[0]["validation_start"] == pd.Timestamp("2024-05-12")
    assert windows[0]["validation_end"] == pd.Timestamp("2024-11-11")
    assert windows[0]["test_start"] == pd.Timestamp("2024-11-12")
    assert windows[0]["test_end"] == pd.Timestamp("2025-02-11")
    assert windows[1]["train_start"] == pd.Timestamp("2022-08-12")


def test_walk_forward_selection_excludes_trades_exiting_after_training_cutoff():
    events = pd.DataFrame(
        [
            {
                "variant": "1d",
                "signal_date": "2022-06-01",
                "exit_date": "2024-05-12",
                "exec_filled": True,
                "exec_return_pct": 100.0,
            },
            {
                "variant": "1d",
                "signal_date": "2022-07-01",
                "exit_date": "2022-07-10",
                "exec_filled": True,
                "exec_return_pct": -2.0,
            },
            {
                "variant": "2d",
                "signal_date": "2022-07-01",
                "exit_date": "2022-07-10",
                "exec_filled": True,
                "exec_return_pct": 2.0,
            },
            {
                "variant": "2d",
                "signal_date": "2024-06-01",
                "exit_date": "2024-06-10",
                "exec_filled": True,
                "exec_return_pct": 2.0,
            },
            {
                "variant": "2d",
                "signal_date": "2024-12-01",
                "exit_date": "2024-12-10",
                "exec_filled": True,
                "exec_return_pct": 3.0,
            },
        ]
    )

    report = build_walk_forward_report(
        events,
        variants=("1d", "2d"),
        min_train_completed=1,
        min_validation_completed=1,
        start_date="2022-05-12",
        end_date="2025-02-11",
    )

    first = report["windows"][0]
    assert first["selected_variant"] == "2d"
    assert first["variants"]["1d"]["train"]["completed"] == 1
    assert first["selected_test"]["avg_return"] == 3.0
    assert report["status"] == "E3_NOT_REACHED"
    selected = select_frozen_test_events(events, report)
    assert selected["exec_return_pct"].tolist() == [3.0]


def test_walk_forward_excludes_exits_after_validation_and_test_cutoffs():
    events = pd.DataFrame(
        {
            "variant": ["2d", "2d", "2d"],
            "signal_date": pd.to_datetime(["2024-02-01", "2024-08-01", "2024-08-10"]),
            "exit_date": pd.to_datetime(["2024-07-01", "2024-10-01", "2024-08-12"]),
            "exec_filled": [True, True, True],
            "exec_return_pct": [2.0, 20.0, 3.0],
        }
    )

    report = build_walk_forward_report(
        events,
        variants=("2d",),
        start_date="2022-01-01",
        end_date="2025-03-31",
        min_train_completed=1,
        min_validation_completed=1,
    )

    first = report["windows"][0]
    assert first["variants"]["2d"]["validation"]["completed"] == 0
    assert first["variants"]["2d"]["test"]["completed"] == 1
