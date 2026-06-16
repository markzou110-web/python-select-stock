import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from routers.paper_trade import (
    _count_holding_trading_days,
    _evaluate_time_stop,
    _wind_control_decision,
)


def test_holding_days_use_trading_days_from_daily_k():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close, vol)
            VALUES
            ('600000', '2026-05-25', 10, 10, 10, 10, 100),
            ('600000', '2026-05-26', 10, 10, 10, 10, 100),
            ('600000', '2026-05-27', 10, 10, 10, 10, 100),
            ('600000', '2026-05-28', 10, 10, 10, 10, 100),
            ('600000', '2026-05-29', 10, 10, 10, 10, 100),
            ('600000', '2026-06-01', 10, 10, 10, 10, 100)
        """))

    hold_days = _count_holding_trading_days(
        engine,
        "600000",
        datetime(2026, 5, 25),
        datetime(2026, 6, 1),
    )

    assert hold_days == 5


def test_time_stop_warning_does_not_force_close():
    result = _evaluate_time_stop(5, -0.5, "pine")

    assert result is not None
    assert result["severity"] == "warning"
    assert result["should_close"] is False
    assert "预警" in result["reason"]


def test_time_stop_force_close_after_long_non_profit_hold():
    result = _evaluate_time_stop(10, 0.0, "pine")

    assert result is not None
    assert result["severity"] == "close"
    assert result["should_close"] is True
    assert "确认" in result["reason"]


def test_squeeze_strategy_uses_slower_time_stop():
    assert _evaluate_time_stop(5, -0.5, "squeeze") is None

    result = _evaluate_time_stop(7, -0.5, "squeeze")

    assert result is not None
    assert result["severity"] == "warning"


def test_wind_control_only_closes_on_price_or_confirmed_time_stop():
    healthy_risk = {"active_stop_price": 21.28, "risk_stage": "保本保护"}

    hold = _wind_control_decision(21.99, healthy_risk, None)
    warning = _wind_control_decision(
        21.99,
        healthy_risk,
        {"reason": "时间风控预警", "should_close": False},
    )
    stop = _wind_control_decision(21.20, healthy_risk, None)

    assert hold == {"reason": "", "should_close": False}
    assert warning == {"reason": "时间风控预警", "should_close": False}
    assert stop["should_close"] is True
    assert "21.28" in stop["reason"]


def test_wind_control_respects_t1_locked_snapshot():
    result = _wind_control_decision(
        9.0,
        {"active_stop_price": 9.1, "risk_stage": "初始/结构防守"},
        None,
        {"action": "CLOSE", "executable": False, "trigger": "跌破止损；T+1锁定"},
    )

    assert result["should_close"] is False
    assert "T+1" in result["reason"]


# ── REDUCE 真减仓（改动 #1）──

def test_wind_control_reduce_in_profit_triggers_partial_close():
    """REDUCE + 可执行 + 当前盈利 → 返回 should_reduce=True（触发部分平仓）。"""
    result = _wind_control_decision(
        11.0,  # 当前价 11，买入价 10 → 盈利
        {"active_stop_price": 9.1, "risk_stage": "保本保护"},
        None,
        {"action": "REDUCE", "executable": True, "trigger": "分批止盈：+8%"},
        entry_price=10.0,
    )

    assert result["should_close"] is False
    assert result["should_reduce"] is True


def test_wind_control_reduce_in_loss_degrades_to_warning():
    """REDUCE + 可执行 + 当前亏损 → 降级为预警（不砍亏损仓位）。"""
    result = _wind_control_decision(
        9.5,  # 当前价 9.5，买入价 10 → 亏损
        {"active_stop_price": 9.1, "risk_stage": "初始/结构防守"},
        None,
        {"action": "REDUCE", "executable": True, "trigger": "分批止盈：+8%"},
        entry_price=10.0,
    )

    assert result["should_close"] is False
    assert result.get("should_reduce", False) is False  # 亏损不真减仓


def test_wind_control_reduce_t1_locked_does_not_execute():
    """REDUCE + T+1 锁定（executable=False）→ 不触发部分平仓。"""
    result = _wind_control_decision(
        11.0,
        {"active_stop_price": 9.1, "risk_stage": "保本保护"},
        None,
        {"action": "REDUCE", "executable": False, "trigger": "分批止盈；T+1锁定"},
        entry_price=10.0,
    )

    assert result["should_close"] is False
    assert result.get("should_reduce", False) is False
