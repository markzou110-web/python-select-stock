import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.execution_insights import build_distance_to_trade, build_execution_rr, build_frozen_plan_state


def test_execution_rr_exposes_one_current_execution_basis():
    result = build_execution_rr({"现价": 11, "pa_entry_price": 10, "pa_stop_price": 9, "pa_target_price": 13})
    assert result["planned_rr"] == 3
    assert result["current_rr"] == 1
    assert result["execution_rr"] == 1


def test_extended_frozen_plan_has_one_active_confirmation():
    result = build_frozen_plan_state({
        "execution_plan_frozen": True, "frozen_confirmation_triggered": True,
        "frozen_entry_extension_pct": 4.5, "pa_entry_price": 43.23,
        "frozen_confirmation_price": 41.38,
    })
    assert result["state"] == "EXTENDED_WAIT_PULLBACK"
    assert result["active_confirmation_price"] == 43.23


def test_distance_to_trade_is_actionable_and_short():
    result = build_distance_to_trade({
        "trade_blockers": ["涨停/近涨停，等待隔日确认", "量能未确认", "策略近期负期望，自动暂停"],
        "pa_stop_price": 9,
    })
    assert result["remaining_count"] == 3
    assert result["invalidation_price"] == 9
