import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.execution_labels import build_executable_labels, daily_limit_pct, evaluate_execution_path, minimum_buy_shares


def _bars(opens=None):
    opens = opens or [10.0] * 3
    return pd.DataFrame({
        "日期": pd.date_range("2026-07-02", periods=3),
        "开盘": opens,
        "最高": [10.5, 10.6, 10.7],
        "最低": [9.8, 9.1, 9.9],
        "收盘": [10.2, 10.0, 10.5],
    })


def test_execution_label_uses_next_open_and_stop_first():
    bars = _bars()
    bars.loc[1, "最高"] = 10.6  # same sellable bar also reaches target; conservative stop must win
    result = evaluate_execution_path("000001", 10.0, bars, max_hold_days=3, stop_loss_pct=-8, take_profit_pct=5)
    assert result["mature"] is True
    assert result["filled"] is True
    assert result["exit_reason"] == "固定止损"
    assert result["return_pct"] < -8


def test_execution_label_respects_t_plus_one():
    bars = _bars()
    bars.loc[0, "最低"] = 8.5
    bars.loc[1, ["开盘", "最低", "最高", "收盘"]] = [9.9, 9.8, 10.1, 10.0]
    result = evaluate_execution_path("000001", 10.0, bars, max_hold_days=3, stop_loss_pct=-8)
    assert result["exit_reason"] == "持有期结束"


def test_confirmation_trigger_requires_price_reach_and_caps_open_extension():
    bars = _bars()
    bars.loc[0, ["开盘", "最高", "最低", "收盘"]] = [10.0, 10.4, 9.9, 10.2]
    not_reached = evaluate_execution_path(
        "000001",
        10.0,
        bars,
        max_hold_days=3,
        planned_entry_price=10.5,
    )
    assert not_reached["mature"] is True
    assert not_reached["filled"] is False
    assert not_reached["reason"] == "确认价未触及"

    bars.loc[0, ["开盘", "最高", "最低", "收盘"]] = [10.0, 10.6, 9.9, 10.5]
    reached = evaluate_execution_path(
        "000001",
        10.0,
        bars,
        max_hold_days=3,
        planned_entry_price=10.5,
    )
    assert reached["filled"] is True
    assert reached["entry_price"] > 10.5
    assert reached["entry_trigger_price"] == 10.5

    bars.loc[0, ["开盘", "最高", "最低", "收盘"]] = [10.9, 11.0, 10.8, 10.9]
    extended = evaluate_execution_path(
        "000001",
        10.0,
        bars,
        max_hold_days=3,
        planned_entry_price=10.5,
    )
    assert extended["filled"] is False
    assert extended["reason"] == "开盘超过确认价偏离上限"


def test_expired_confirmation_is_mature_once_t_plus_one_bar_exists():
    t_plus_one = _bars().head(1).copy()
    t_plus_one.loc[0, ["开盘", "最高", "最低", "收盘"]] = [10.0, 10.4, 9.9, 10.2]

    expired = evaluate_execution_path(
        "000001",
        10.0,
        t_plus_one,
        max_hold_days=10,
        planned_entry_price=10.5,
    )
    assert expired["mature"] is True
    assert expired["filled"] is False
    assert expired["reason"] == "确认价未触及"

    triggered = evaluate_execution_path(
        "000001",
        10.0,
        t_plus_one.assign(最高=10.6),
        max_hold_days=10,
        planned_entry_price=10.5,
    )
    assert triggered["mature"] is False
    assert triggered["filled"] is False
    assert triggered["reason"] == "持有期未成熟"


def test_t_plus_one_capacity_rejection_does_not_wait_for_holding_horizon():
    t_plus_one = _bars().head(1).assign(成交量=100).copy()

    rejected = evaluate_execution_path(
        "688001",
        10.0,
        t_plus_one,
        max_hold_days=10,
        planned_entry_price=10.0,
        planned_order_value=1500,
    )

    assert rejected["mature"] is True
    assert rejected["filled"] is False
    assert rejected["reason"] == "计划资金不足最低申报数量"


def test_execution_label_rejects_unbuyable_limit_up():
    result = evaluate_execution_path("000001", 10.0, _bars([11.0, 10.0, 10.0]), max_hold_days=3)
    assert result["mature"] is True
    assert result["filled"] is False
    assert "无法成交" in result["reason"]


def test_st_limit_and_adjustment_gap_are_point_in_time_aware():
    assert daily_limit_pct("000001", is_st=True) == 5.0
    st_bars = _bars([10.49, 10.0, 10.0])
    assert evaluate_execution_path("000001", 10.0, st_bars, max_hold_days=3, is_st=True)["filled"] is False
    gap_bars = _bars([15.0, 15.0, 15.0])
    gap_bars.loc[0, ["最高", "最低", "收盘"]] = [15.2, 14.8, 15.0]
    assert evaluate_execution_path("000001", 10.0, gap_bars, max_hold_days=3)["reason"] == "疑似复权断点"


def test_build_labels_only_uses_bars_after_signal_date():
    signals = pd.DataFrame([{"code": "000001", "signal_date": "2026-07-01", "signal_close": 10.0}])
    prices = _bars()
    prices["code"] = "000001"
    result = build_executable_labels(signals, prices, max_hold_days=3)
    assert result.loc[0, "exec_filled"]
    assert result.loc[0, "exec_execution_model_version"] == "a-share-confirmation-trigger-v4"


def test_build_labels_uses_price_action_trigger_and_structural_stop_target():
    signals = pd.DataFrame([{
        "code": "000001", "signal_date": "2026-07-01", "signal_close": 10.0,
        "price_action_detail": {"pa_entry_price": 10.5, "pa_stop_price": 9.5, "pa_target_price": 11.5},
    }])
    prices = _bars()
    prices["code"] = "000001"
    prices.loc[0, "最高"] = 10.6
    prices.loc[1, "最低"] = 9.4

    result = build_executable_labels(signals, prices, max_hold_days=3)

    assert result.loc[0, "exec_filled"]
    assert result.loc[0, "exec_entry_trigger_price"] == 10.5
    assert result.loc[0, "exec_exit_reason"] == "结构止损"
    assert result.loc[0, "exec_exit_price"] < 9.5


def test_capital_sized_label_applies_lot_capacity_and_minimum_commission():
    bars = _bars()
    bars["成交量"] = [10000, 10000, 10000]
    result = evaluate_execution_path("000001", 10.0, bars, max_hold_days=3, planned_order_value=5000)
    assert result["filled"] is True
    assert result["shares"] == 400
    assert result["fees"] >= 10
    assert result["volume_participation_pct"] == 4.0

    blocked = evaluate_execution_path("000001", 10.0, bars, max_hold_days=3, planned_order_value=7000)
    assert blocked["filled"] is False
    assert "容量" in blocked["reason"]


def test_star_market_minimum_buy_quantity_is_enforced():
    assert minimum_buy_shares("688001") == 200
    bars = _bars()
    result = evaluate_execution_path("688001", 10.0, bars, max_hold_days=3, planned_order_value=1500)
    assert result["filled"] is False
    assert "最低申报数量" in result["reason"]


def test_database_volume_lots_are_converted_to_shares():
    bars = _bars()
    bars["成交量"] = [100, 100, 100]  # 数据库单位：手，即10000股
    result = evaluate_execution_path(
        "000001", 10.0, bars, max_hold_days=3, planned_order_value=5000, volume_in_lots=True,
    )
    assert result["filled"] is True
    assert result["volume_participation_pct"] == 4.0
