import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from routers.paper_trade import (
    _count_holding_trading_days,
    _evaluate_time_stop,
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
