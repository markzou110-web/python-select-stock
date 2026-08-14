import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.strong_trend_pullback_replay import (
    build_event_arm_rows,
    build_report,
    build_route_b_protocol_events,
    evaluate_p1_plan,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": pd.date_range("2026-01-01", periods=8),
            "开盘": [10.0, 10.4, 10.2, 10.3, 10.6, 10.5, 10.4, 10.3],
            "最高": [10.5, 10.6, 10.4, 10.7, 10.8, 10.7, 10.6, 10.5],
            "最低": [9.8, 10.2, 9.95, 10.1, 10.3, 10.2, 10.1, 10.0],
            "收盘": [10.0, 10.5, 10.2, 10.6, 10.7, 10.5, 10.3, 10.2],
            "成交量": [1_000_000, 900_000, 800_000, 850_000, 900_000, 800_000, 700_000, 700_000],
            "EMA20": [9.6, 9.7, 9.8, 9.9, 10.0, 10.1, 10.4, 10.4],
        }
    )


def test_p1_confirms_within_three_days_at_original_price_band():
    result = evaluate_p1_plan(
        _frame(),
        signal_idx=0,
        confirmation_price=10.0,
        invalidation_price=9.5,
    )

    assert result.status == "PULLBACK_CONFIRMED"
    assert result.confirmation_idx == 2
    assert result.wait_days == 2
    assert result.mature is True


def test_p1_cancels_on_structure_break_or_b0_before_confirmation():
    broken = _frame()
    broken.loc[1, "最低"] = 9.4
    structure = evaluate_p1_plan(
        broken,
        signal_idx=0,
        confirmation_price=10.0,
        invalidation_price=9.5,
    )
    assert structure.status == "CANCELLED"
    assert structure.reason == "结构失效"

    frame = _frame()
    b0 = evaluate_p1_plan(
        frame,
        signal_idx=0,
        confirmation_price=10.0,
        invalidation_price=9.5,
        market_stage_lookup={pd.Timestamp(frame.loc[1, "日期"]).normalize(): "RETREAT"},
    )
    assert b0.status == "CANCELLED"
    assert b0.reason == "市场宽度B0"


def test_p1_cancels_when_matching_strategy_sell_appears_first():
    result = evaluate_p1_plan(
        _frame(),
        signal_idx=0,
        confirmation_price=10.0,
        invalidation_price=9.5,
        cancel_signal_indices={1},
    )

    assert result.status == "CANCELLED"
    assert result.reason == "等待期先出现策略卖点"


def test_p1_expires_after_three_complete_days_and_keeps_incomplete_sample_pending():
    frame = _frame()
    frame.loc[1:3, "最低"] = 10.3
    expired = evaluate_p1_plan(
        frame,
        signal_idx=0,
        confirmation_price=10.0,
        invalidation_price=9.5,
    )
    assert expired.status == "EXPIRED"
    assert expired.mature is True

    pending = evaluate_p1_plan(
        frame.head(3),
        signal_idx=0,
        confirmation_price=10.0,
        invalidation_price=9.5,
    )
    assert pending.status == "PENDING_DATA"
    assert pending.mature is False


def test_event_arms_use_direct_next_open_vs_delayed_confirmation_next_open():
    rows = build_event_arm_rows(
        "000001",
        _frame(),
        signal_idx=0,
        sources=frozenset({"zp"}),
        zp_short_indices=[4],
        confirmation_price=10.0,
        invalidation_price=9.5,
    )
    by_arm = {row["arm"]: row for row in rows}

    assert set(by_arm) == {"P0_DIRECT", "P1_PULLBACK", "SKIP"}
    assert by_arm["P0_DIRECT"]["exec_entry_idx"] == 1
    assert by_arm["P0_DIRECT"]["exec_entry_date"] == pd.Timestamp("2026-01-02")
    assert by_arm["P1_PULLBACK"]["plan_confirmation_idx"] == 2
    assert by_arm["P1_PULLBACK"]["exec_entry_idx"] == 3
    assert by_arm["P1_PULLBACK"]["exec_exit_date"] == pd.Timestamp("2026-01-06")
    assert by_arm["SKIP"]["exec_filled"] is False


def test_event_arm_preserves_unfilled_execution_reason():
    frame = _frame()
    frame.loc[1, ["开盘", "最高", "最低", "收盘"]] = [10.9, 11.0, 10.8, 10.9]
    rows = build_event_arm_rows(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"zp"}),
        zp_short_indices=[4],
        confirmation_price=10.0,
        invalidation_price=9.5,
    )

    p0 = next(row for row in rows if row["arm"] == "P0_DIRECT")
    assert p0["exec_filled"] is False
    assert p0["exec_exit_reason"] == "高开超过入场上限"


def test_route_b_protocol_only_replays_shadow_pullback_market_states():
    dates = pd.to_datetime(["2026-01-01", "2026-01-02"])
    rows = pd.DataFrame(
        {
            "code": ["000001", "000001", "000001", "000001"],
            "signal_idx": [120, 120, 130, 130],
            "signal_date": [dates[0], dates[0], dates[1], dates[1]],
            "arm": ["P0_DIRECT", "P1_PULLBACK", "P0_DIRECT", "P1_PULLBACK"],
            "exec_filled": [True] * 4,
            "exec_return_pct": [1.0, 2.0, 3.0, 4.0],
            "exec_exit_idx": [125, 125, 135, 135],
            "exec_exit_date": pd.to_datetime(
                ["2026-01-05", "2026-01-05", "2026-01-08", "2026-01-08"]
            ),
        }
    )
    dual_axis = pd.DataFrame(
        {
            "date": dates,
            "route_b_permission": ["SHADOW_PULLBACK", "SHADOW_OBSERVE"],
        }
    )

    result = build_route_b_protocol_events(rows, dual_axis)

    assert set(result["variant"]) == {"P0_DIRECT", "P1_PULLBACK"}
    assert result["signal_date"].nunique() == 1


def test_report_remains_shadow_even_when_fixed_split_p1_looks_profitable():
    rows = []
    for signal_date, count in (("2024-01-01", 60), ("2025-01-01", 40), ("2026-01-01", 100)):
        for idx in range(count):
            for arm, return_pct in (("P0_DIRECT", -1.0), ("P1_PULLBACK", 2.0), ("SKIP", None)):
                rows.append(
                    {
                        "code": f"{signal_date[:4]}-{idx}",
                        "signal_idx": 10,
                        "signal_date": signal_date,
                        "arm": arm,
                        "plan_status": "PULLBACK_CONFIRMED" if arm == "P1_PULLBACK" else arm,
                        "plan_wait_days": 2 if arm == "P1_PULLBACK" else 0,
                        "exec_mature": True,
                        "exec_filled": arm != "SKIP",
                        "exec_return_pct": return_pct,
                        "exec_exit_reason": "测试卖点" if arm != "SKIP" else "策略跳过",
                        "exec_exit_idx": 15 if arm != "SKIP" else None,
                        "exec_hold_days": 4 if arm != "SKIP" else None,
                    }
                )

    report = build_report(pd.DataFrame(rows))

    assert report["status"] == "SHADOW_ONLY"
    assert report["fixed_split_p1_support"] is True
    assert report["arms"]["P1_PULLBACK"]["test"]["filled"] == 100
    assert "固定分段结果不能替代research_protocol中的24/6/3月走查前推" in report["e3_blockers"]
