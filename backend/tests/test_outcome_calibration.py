import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.outcome_calibration import (
    build_blocker_report, build_calibration_report, build_execution_cohort_report,
    build_feature_ablation_report, build_opportunity_threshold_report,
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
