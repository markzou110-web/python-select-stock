import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.operation_plan import alert_priority, evaluate_operation_trigger, operation_bands, pre_trade_check, price_instruction, watch_exit_decision, watch_instruction


def test_price_instruction_has_action_ranges():
    text = price_instruction(
        trigger=22.5,
        guard=22.16,
        active_stop=21.28,
        structure_stop=19.17,
        confirmed=False,
        profitable=True,
    )

    assert ">22.50: 只确认不追，等价量收齐" in text
    assert "22.16-22.50: 持有观察，不加仓" in text
    assert "<21.28: 减仓/收紧风控" in text
    assert "<19.17: 结构失效，退出复核" in text


def test_watch_instruction_uses_target_and_stop():
    payload = watch_instruction({
        "current_price": 10.5,
        "watch_price": 10.0,
        "target_price": 11.0,
        "stop_price": 9.5,
    })

    assert payload["trigger_price"] == 11.0
    assert ">11.00" in payload["instruction"]
    assert "<9.50" in payload["instruction"]


def test_watch_exit_decision_prunes_stale_item():
    created_at = (datetime.now() - timedelta(days=20)).isoformat()
    decision = watch_exit_decision({
        "status": "WATCHING",
        "created_at": created_at,
        "computed_decision": "KEEP_WATCH",
        "current_price": 10,
        "stop_price": 9,
    }, max_watch_days=15)

    assert decision["should_exit"] is True
    assert "未触发" in decision["reason"]


def test_operation_bands_create_chart_lines():
    bands = operation_bands(trigger=22.5, guard=22.16, active_stop=21.28, structure_stop=19.17)

    assert [item["label"] for item in bands] == ["加仓触发线", "加仓撤退线", "减仓线", "退出线"]


def test_evaluate_operation_trigger_detects_add_opportunity():
    trigger = evaluate_operation_trigger(22.8, {
        "add_trigger_price": 22.5,
        "add_guard_price": 22.16,
        "active_stop_price": 21.28,
        "structure_stop_price": 19.17,
    })

    assert trigger["triggered"] is True
    assert trigger["kind"] == "ADD_TRIGGER"
    assert "突破加仓触发线" in trigger["action"]


def test_evaluate_operation_trigger_detects_cancel_add_before_reduce():
    trigger = evaluate_operation_trigger(22.0, {
        "add_trigger_price": 22.5,
        "add_guard_price": 22.16,
        "active_stop_price": 21.28,
        "structure_stop_price": 19.17,
    })

    assert trigger["triggered"] is True
    assert trigger["kind"] == "CANCEL_ADD"
    assert "撤回加仓计划" in trigger["action"]


def test_evaluate_operation_trigger_prioritizes_structure_exit():
    trigger = evaluate_operation_trigger(19.0, {
        "add_trigger_price": 22.5,
        "add_guard_price": 22.16,
        "active_stop_price": 21.28,
        "structure_stop_price": 19.17,
    })

    assert trigger["triggered"] is True
    assert trigger["level"] == "critical"
    assert trigger["priority"] == "P0"
    assert trigger["kind"] == "STRUCTURE_EXIT"


def test_alert_priority_maps_operation_levels():
    assert alert_priority("critical", "STRUCTURE_EXIT")["priority"] == "P0"
    assert alert_priority("opportunity", "ADD_TRIGGER")["priority"] == "P1"
    assert alert_priority("notice", "CANCEL_ADD")["priority"] == "P2"


def test_pre_trade_check_blocks_unconfirmed_add():
    result = pre_trade_check(
        current_price=22.6,
        plan={"add_trigger_price": 22.5, "add_guard_price": 22.16},
        volume_confirmed=False,
        close_confirmed=True,
    )

    assert result["passed"] is False
    assert result["action"] == "继续观察，等待价量收齐"
    assert "量能未确认" in result["blockers"]


def test_pre_trade_check_passes_when_all_confirmed():
    result = pre_trade_check(
        current_price=22.6,
        plan={"add_trigger_price": 22.5, "add_guard_price": 22.16},
        volume_confirmed=True,
        close_confirmed=True,
    )

    assert result["passed"] is True
    assert result["action"] == "允许小仓执行"
