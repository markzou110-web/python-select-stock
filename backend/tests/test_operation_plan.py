import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.operation_plan import operation_bands, price_instruction, watch_exit_decision, watch_instruction


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
