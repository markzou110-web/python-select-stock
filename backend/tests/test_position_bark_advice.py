import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _real_position():
    return {
        "id": 1,
        "code": "600075",
        "name": "新疆天业",
        "trade_mode": "REAL",
        "plan": {
            "entry_price": 4.96,
            "current_price": 4.91,
            "add_trigger_price": 5.06,
            "add_guard_price": 4.98,
            "active_stop_price": 4.81,
            "structure_stop_price": 4.76,
            "time_stop_date": "2026-09-18",
        },
    }


def test_premarket_position_advice_contains_executable_price_levels():
    from core.tasks import build_premarket_position_advice

    message = build_premarket_position_advice(
        [_real_position()],
        market={"desc": "减仓观望：市场进入震荡/分化期"},
        now=datetime(2026, 9, 8, 8, 45),
    )

    assert message["title"] == "📋 盘前持仓策略 09-08"
    assert "新疆天业(600075)" in message["body"]
    assert "昨收 4.91｜成本 4.96" in message["body"]
    assert "防守 4.81｜撤退 4.98｜转强 5.06" in message["body"]
    assert "持有观察，不加仓" in message["body"]
    assert "同类信号重入冷却30分钟" in message["body"]


def test_premarket_position_advice_prioritizes_breached_stop():
    from core.tasks import build_premarket_position_advice

    position = _real_position()
    position["plan"]["current_price"] = 4.78
    message = build_premarket_position_advice([position], now=datetime(2026, 9, 8, 8, 45))

    assert "已低于防守线，开盘优先减仓或退出复核" in message["body"]


def test_premarket_task_pushes_only_open_real_positions(monkeypatch):
    from core import data, tasks
    from routers import paper_trade

    sent = []

    async def fake_send(title, body, **kwargs):
        sent.append((title, body, kwargs))
        return {"bark": True}

    simulated = _real_position()
    simulated["id"] = 2
    simulated["trade_mode"] = "SIMULATED"
    monkeypatch.setattr(tasks, "is_a_share_trading_day", lambda now=None: True)
    monkeypatch.setattr(paper_trade, "get_open_trade_plans", lambda: {"items": [_real_position(), simulated]})
    monkeypatch.setattr(data, "get_market_regime", lambda: {"desc": "减仓观望"})
    monkeypatch.setattr(tasks.notifier, "send", fake_send)

    result = tasks.send_premarket_position_advice()

    assert result == {"status": "success", "bark": True, "count": 1}
    assert len(sent) == 1
    assert sent[0][2]["channels"] == ["bark"]
    assert sent[0][2]["group"] == "AlphaVision_Position"


def test_intraday_operation_task_checks_real_positions_only(monkeypatch):
    from core import tasks
    from routers import paper_trade

    calls = []
    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(
        paper_trade,
        "check_operation_triggers",
        lambda **kwargs: calls.append(kwargs) or {"status": "success", "alerts": []},
    )

    result = tasks.check_position_operation_alerts()

    assert result["status"] == "success"
    assert calls == [{"notify": True, "trade_mode": "REAL"}]


def test_premarket_bark_is_scheduled_at_0845_and_routed_to_realtime_queue():
    from core.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule["premarket-position-advice-0845"]

    assert schedule["task"] == "tasks.send_premarket_position_advice"
    assert str(schedule["schedule"]) == "<crontab: 45 8 * * 1-5 (m/h/dM/MY/d)>"
    assert celery_app.conf.task_routes["tasks.send_premarket_position_advice"]["queue"] == "realtime"

    intraday = celery_app.conf.beat_schedule["check-position-operation-alerts-every-5-minutes"]
    assert intraday["task"] == "tasks.check_position_operation_alerts"
    assert intraday["options"]["expires"] == 240


def test_position_status_summary_contains_current_action_and_monitor_state():
    from core.tasks import build_position_status_summary

    message = build_position_status_summary(
        [_real_position()],
        slot="morning",
        now=datetime(2026, 9, 8, 11, 25),
        live_refreshed=True,
    )

    assert message["title"] == "☀️ 上午持仓摘要 09-08"
    assert "监控正常｜实时行情已刷新 11:25" in message["body"]
    assert "新疆天业(600075)" in message["body"]
    assert "现价 4.91｜成本 4.96｜-1.01%" in message["body"]
    assert "状态：撤回加仓计划" in message["body"]
    assert "防守 4.81｜撤退 4.98｜转强 5.06" in message["body"]
    assert "即时提醒仍在运行" in message["body"]


def test_position_status_task_refreshes_without_touching_event_dedupe(monkeypatch):
    from core import tasks
    from routers import paper_trade

    sent = []
    refresh_calls = []

    async def fake_send(title, body, **kwargs):
        sent.append((title, body, kwargs))
        return {"bark": True}

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(
        paper_trade,
        "check_operation_triggers",
        lambda **kwargs: refresh_calls.append(kwargs) or {"status": "success", "checked": 1},
    )
    monkeypatch.setattr(paper_trade, "get_open_trade_plans", lambda: {"items": [_real_position()]})
    monkeypatch.setattr(tasks.notifier, "send", fake_send)

    result = tasks.send_position_status_summary(slot="late")

    assert result == {"status": "success", "bark": True, "count": 1, "slot": "late", "live_refreshed": True}
    assert refresh_calls == [{"notify": False, "trade_mode": "REAL"}]
    assert len(sent) == 1
    assert sent[0][0].startswith("🎯 尾盘持仓确认")
    assert sent[0][2]["channels"] == ["bark"]
    assert sent[0][2]["group"] == "AlphaVision_Position"


def test_position_status_summaries_are_scheduled_and_routed_to_realtime_queue():
    from core.celery_app import celery_app

    morning = celery_app.conf.beat_schedule["position-status-summary-1125"]
    late = celery_app.conf.beat_schedule["position-status-summary-1450"]

    assert morning["task"] == "tasks.send_position_status_summary"
    assert morning["kwargs"] == {"slot": "morning"}
    assert str(morning["schedule"]) == "<crontab: 25 11 * * 1-5 (m/h/dM/MY/d)>"
    assert late["task"] == "tasks.send_position_status_summary"
    assert late["kwargs"] == {"slot": "late"}
    assert str(late["schedule"]) == "<crontab: 50 14 * * 1-5 (m/h/dM/MY/d)>"
    assert celery_app.conf.task_routes["tasks.send_position_status_summary"]["queue"] == "realtime"
