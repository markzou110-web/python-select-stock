import os
import sys

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.outcome_calibration import (
    build_a_grade_policy_report, build_blocker_report, build_bottom_discovery_report, build_calibration_report, build_execution_cohort_report,
    build_feature_ablation_report, build_opportunity_threshold_report,
    load_scan_outcomes, mark_independent_signal_events,
)


def _sample_frame() -> pd.DataFrame:
    rows = []
    for idx in range(12):
        rows.append({
            "code": f"000{idx:03d}",
            "signal_date": "2026-07-01",
            "strategy_type": "strict",
            "sop_grade": "A",
            "trade_bucket": "TRADE",
            "trade_eligible": True,
            "research_eligible": True,
            "trade_opportunity_score": 70,
            "market_regime": "OFFENSIVE",
            "score_model_version": "v1",
            "grade_stage": "A-TRADE",
            "decision_lifecycle_state": "ENTRY_CONFIRMED",
            "confirmation_event_state": "CONFIRMED",
            "early_value_transition_state": "UNKNOWN",
            "ret_1d": 0.2,
            "ret_3d": 0.8,
            "ret_5d": 2.0,
            "ret_10d": np.nan,
            "blockers": [],
        })
        rows.append({
            "code": f"001{idx:03d}",
            "signal_date": "2026-07-01",
            "strategy_type": "strict",
            "sop_grade": "B",
            "trade_bucket": "OBSERVE",
            "trade_eligible": False,
            "research_eligible": True,
            "trade_opportunity_score": 58,
            "market_regime": "OFFENSIVE",
            "score_model_version": "v1",
            "grade_stage": "B-STRUCTURE",
            "decision_lifecycle_state": "WAITING_CONFIRMATION",
            "confirmation_event_state": "NOT_READY",
            "early_value_transition_state": "UNKNOWN",
            "ret_1d": -0.1,
            "ret_3d": -0.5,
            "ret_5d": -1.0,
            "ret_10d": np.nan,
            "blockers": ["未站上确认价"],
        })
    return pd.DataFrame(rows)


def test_calibration_report_excludes_immature_horizons_and_groups_dimensions():
    report = build_calibration_report(_sample_frame(), min_samples=10)

    assert report["summary"]["signals"] == 24
    assert report["summary"]["mature_5d"] == 24
    assert report["summary"]["mature_10d"] == 0
    assert report["horizons"]["5d"]["avg_return"] == 0.5
    assert report["horizons"]["10d"]["signals"] == 0
    assert {row["value"] for row in report["by_grade"]} == {"A", "B"}
    assert {row["value"] for row in report["by_grade_stage"]} == {"A-TRADE", "B-STRUCTURE"}
    assert {row["value"] for row in report["by_confirmation_event"]} == {"CONFIRMED", "NOT_READY"}
    assert report["grade_monotonicity"]["status"] == "INSUFFICIENT"


def test_grade_monotonicity_passes_with_mature_a_b_c_samples():
    df = _sample_frame()
    c_rows = df[df["sop_grade"].eq("B")].copy()
    c_rows["code"] = [f"002{idx:03d}" for idx in range(len(c_rows))]
    c_rows["sop_grade"] = "C"
    c_rows["ret_5d"] = -2.0
    report = build_calibration_report(pd.concat([df, c_rows], ignore_index=True), min_samples=10)

    assert report["grade_monotonicity"]["status"] == "PASS"
    assert report["grade_monotonicity"]["metric"] == "avg_return_5d"


def test_overlapping_daily_signals_count_as_one_independent_event_until_five_day_maturity():
    df = pd.DataFrame([
        {"code": "000001", "strategy_type": "tv_dual", "signal_date": "2026-07-01", "maturity_5d_date": "2026-07-08"},
        {"code": "000001", "strategy_type": "tv_dual", "signal_date": "2026-07-02", "maturity_5d_date": "2026-07-09"},
        {"code": "000001", "strategy_type": "tv_dual", "signal_date": "2026-07-09", "maturity_5d_date": "2026-07-16"},
    ])

    marked = mark_independent_signal_events(df)

    assert marked["independent_event"].tolist() == [True, False, True]


def test_calibration_uses_independent_events_and_shadows_failed_grade_ordering():
    df = _sample_frame().head(4).copy()
    df["independent_event"] = [True, False, True, False]

    report = build_calibration_report(df, min_samples=1)

    assert report["summary"]["raw_signals"] == 4
    assert report["summary"]["signals"] == 2
    assert report["grade_usage"]["mode"] == "SHADOW_ONLY"
    assert report["grade_usage"]["production_effect"] is False


def test_a_grade_policy_report_caps_discovery_and_vetoed_candidates():
    df = pd.DataFrame([
        {
            "strategy_type": "tv_dual", "sop_grade": "A", "sop_quality_score": 80,
            "sop_vetoes": [], "ret_1d": 1, "ret_3d": 1, "ret_5d": 5, "ret_10d": 5,
        },
        {
            "strategy_type": "tv_dual_strict", "sop_grade": "D", "sop_quality_score": 80,
            "sop_vetoes": ["地雷预警"], "ret_1d": 1, "ret_3d": 1, "ret_5d": 5, "ret_10d": 5,
        },
        {
            "strategy_type": "tv_dual_strict", "sop_grade": "B", "sop_quality_score": 72,
            "price_action_score": 65, "pct_5d": 8,
            "sop_vetoes": [], "ret_1d": 1, "ret_3d": 2, "ret_5d": 3, "ret_10d": 4,
        },
    ])

    report = build_a_grade_policy_report(df, min_samples=30)

    assert report["baseline"]["signals"] == 1
    assert report["proposed"]["signals"] == 1
    assert report["proposed"]["metrics"]["5d"]["win_rate"] == 100.0
    assert report["status"] == "INSUFFICIENT_DATA"


def test_blocker_report_compares_hit_and_miss_groups():
    df = _sample_frame()
    report = build_blocker_report(df, min_samples=10)

    row = next(item for item in report["items"] if item["blocker"] == "未站上确认价")
    assert row["hit"]["signals"] == 12
    assert row["miss"]["signals"] == 12
    assert row["hit"]["avg_return"] == -1.0
    assert row["miss"]["avg_return"] == 2.0
    assert row["hit_minus_miss_avg_return"] == -3.0
    assert row["controlled_comparison"]["hit_minus_miss_avg_return"] == -3.0
    assert row["controlled_comparison"]["segments"] == 1
    assert row["recommendation"] == "VALID_FILTER"


def test_blocker_report_marks_small_samples_as_research_only():
    df = _sample_frame().head(6).copy()
    df.loc[df.index[:2], "blockers"] = pd.Series([["小样本规则"], ["小样本规则"]], index=df.index[:2])
    report = build_blocker_report(df, min_samples=5)

    row = next(item for item in report["items"] if item["blocker"] == "小样本规则")
    assert row["recommendation"] == "RESEARCH_ONLY"


def test_feature_ablation_reports_rank_direction_without_claiming_causality():
    df = pd.DataFrame({"score": range(40), "exec_return_pct": range(40)})
    report = build_feature_ablation_report(df, ["score"], min_samples=30)
    assert report["items"][0]["rank_correlation"] == 1.0
    assert report["items"][0]["status"] == "OOS_REQUIRED"


def test_execution_cohorts_keep_research_blocked_and_executable_separate():
    report = build_execution_cohort_report(_sample_frame())
    cohorts = {item["cohort"]: item for item in report["cohorts"]}
    assert cohorts["research_candidate"]["signals"] == 24
    assert cohorts["blocked_candidate"]["metrics"]["5d"]["avg_return"] == -1.0
    assert cohorts["executable_candidate"]["metrics"]["5d"]["avg_return"] == 2.0


def test_opportunity_threshold_report_is_diagnostic_only():
    report = build_opportunity_threshold_report(_sample_frame())
    rows = {item["threshold"]: item for item in report["thresholds"]}
    assert rows[55.0]["signals"] == 24
    assert rows[60.0]["signals"] == 12
    assert report["production_threshold"] == 60
    assert report["status"] == "DIAGNOSTIC_ONLY"


def test_bottom_discovery_report_separates_stages_and_tracks_conversion():
    rows = []
    for code, stage, date, ret, mfe, mae in (
        ("000001", "B0_BASE", "2026-07-01", 1.0, 6.0, -2.0),
        ("000001", "B1_REVERSAL", "2026-07-04", 4.0, 9.0, -1.0),
        ("000002", "B0_BASE", "2026-07-01", -2.0, 2.0, -5.0),
    ):
        rows.append({
            "code": code, "signal_date": date, "strategy_type": "bottom_discovery",
            "bottom_discovery_stage": stage, "ret_1d": ret, "ret_3d": ret,
            "ret_5d": ret, "ret_10d": np.nan, "mfe_5d": mfe, "mae_5d": mae,
        })

    report = build_bottom_discovery_report(pd.DataFrame(rows), min_samples=1)
    stages = {row["value"]: row for row in report["by_stage"]}

    assert report["status"] == "VALIDATED"
    assert report["conversion"]["b0_unique_stocks"] == 2
    assert report["conversion"]["converted_to_b1"] == 1
    assert report["conversion"]["conversion_rate"] == 50.0
    assert report["conversion"]["median_wait_calendar_days"] == 3.0
    assert stages["B1_REVERSAL"]["excursion_5d"]["avg_mfe"] == 9.0
    assert stages["B0_BASE"]["excursion_5d"]["mae_le_minus_4_rate"] == 50.0


def test_bottom_discovery_validation_gate_uses_mature_b1_samples():
    row = {
        "code": "000001", "signal_date": "2026-07-01", "strategy_type": "bottom_discovery",
        "bottom_discovery_stage": "B1_REVERSAL", "ret_1d": 1.0, "ret_3d": 1.0,
        "ret_5d": 1.0, "ret_10d": np.nan, "mfe_5d": 3.0, "mae_5d": -1.0,
    }

    assert build_bottom_discovery_report(pd.DataFrame([row]), min_samples=1)["status"] == "VALIDATED"
    assert build_bottom_discovery_report(pd.DataFrame([row]), min_samples=2)["status"] == "INSUFFICIENT_DATA"


def test_bottom_discovery_followup_only_links_later_events_inside_window():
    def event(code, date, strategy, *, stage=None, eligible=False, bucket="OBSERVE"):
        return {
            "code": code, "signal_date": date[:10], "scanned_at": date,
            "strategy_type": strategy, "bottom_discovery_stage": stage,
            "trade_eligible": eligible, "trade_bucket": bucket,
            "ret_1d": 1.0, "ret_3d": 1.0, "ret_5d": 1.0, "ret_10d": np.nan,
            "mfe_5d": 3.0, "mae_5d": -1.0,
        }

    frame = pd.DataFrame([
        event("000001", "2026-06-30T09:00:00Z", "squeeze", eligible=True, bucket="TRADE"),
        event("000001", "2026-07-01T09:00:00Z", "bottom_discovery", stage="B1_REVERSAL"),
        event("000001", "2026-07-04T09:00:00Z", "tv_dual_strict"),
        event("000001", "2026-07-06T09:00:00Z", "squeeze", eligible=True, bucket="TRADE"),
        event("000002", "2026-07-01T09:00:00Z", "bottom_discovery", stage="B1_REVERSAL"),
        event("000002", "2026-08-10T09:00:00Z", "tv_dual_strict", eligible=True, bucket="TRADE"),
        event("000003", "2026-08-05T09:00:00Z", "bottom_discovery", stage="B1_REVERSAL"),
    ])

    report = build_bottom_discovery_report(frame, min_samples=1)

    assert report["formal_confirmation"]["b1_unique_stocks"] == 3
    assert report["formal_confirmation"]["mature_b1_followups"] == 2
    assert report["formal_confirmation"]["confirmed_stocks"] == 1
    assert report["formal_confirmation"]["confirmation_rate"] == 50.0
    assert report["formal_confirmation"]["median_wait_calendar_days"] == 5.0
    assert report["formal_confirmation"]["confirmed_by_strategy"] == {"squeeze": 1}
    assert report["strict_strategy_lead"]["matched_stocks"] == 1
    assert report["strict_strategy_lead"]["match_rate"] == 50.0
    assert report["strict_strategy_lead"]["median_lead_calendar_days"] == 3.0


def test_load_scan_outcomes_uses_exactly_five_future_trading_rows_for_excursion():
    engine = create_engine("sqlite:///:memory:")
    signal_date = pd.Timestamp.now().normalize() - pd.Timedelta(days=10)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE scan_history (
                signal_id INTEGER PRIMARY KEY, code TEXT, name TEXT, data_date TEXT, date TEXT,
                strategy_type TEXT, sop_grade TEXT, sop_quality_score REAL, sop_vetoes TEXT,
                price_action_detail TEXT, scanned_at TEXT
            )
        """))
        connection.execute(text("""
            CREATE TABLE daily_k (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL)
        """))
        connection.execute(text("""
            INSERT INTO scan_history VALUES (
                1, '000001', '测试', :signal_date, :signal_date, 'bottom_discovery',
                'C', 50, '[]', '{"bottom_discovery_stage":"B0_BASE"}', :scanned_at
            )
        """), {"signal_date": signal_date.date().isoformat(), "scanned_at": signal_date.isoformat()})
        bars = [{
            "code": "000001", "date": (signal_date + pd.Timedelta(days=offset)).date().isoformat(),
            "open": close, "high": high, "low": low, "close": close,
        } for offset, close, high, low in (
            (0, 10, 10, 10), (1, 11, 11, 9), (2, 12, 12, 8),
            (3, 13, 13, 9), (4, 14, 14, 9), (5, 15, 15, 9), (6, 16, 99, 1),
        )]
        connection.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close)
            VALUES (:code, :date, :open, :high, :low, :close)
        """), bars)

    outcomes = load_scan_outcomes(engine, days=30)

    assert len(outcomes) == 1
    assert outcomes.iloc[0]["ret_5d"] == 50.0
    assert outcomes.iloc[0]["mfe_5d"] == 50.0
    assert outcomes.iloc[0]["mae_5d"] == -20.0
    assert outcomes.iloc[0]["bottom_discovery_stage"] == "B0_BASE"
    assert bool(outcomes.iloc[0]["research_eligible"]) is False
    assert pd.isna(outcomes.iloc[0]["trade_opportunity_score"])
