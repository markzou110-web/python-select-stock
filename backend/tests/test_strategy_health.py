"""Tests for core/strategy_health.py — 简化止损模型（改动 #3）。

验证 build_strategy_health 的 ret_5d 反映止损保护的经济性：
- 5日内曾跌破 -9% → 计为 -9%（止损出场），而非更差
- 5日内曾涨超 +15% → 计为 +15%（止盈），而非更高
"""
import os
import sys
from datetime import date, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.strategy_health import build_strategy_health
from core.risk_constants import FIXED_STOP_LOSS_PCT, TAKE_PROFIT_PCT


def _setup_engine():
    """内存 SQLite，建表并写入 scan_history + daily_k 测试数据。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return engine


def _insert_signal(engine, code, signal_date, price, future_closes, strategy="tv_dual_strict"):
    """写入一条 scan_history 信号 + 信号后 N 个交易日的 daily_k 收盘价。

    future_closes: 信号之后每日的收盘价列表（不含信号当日）。
    """
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO scan_history (code, date, price, strategy_type, price_action_detail)
            VALUES (:code, :date, :price, :st, '{"research_eligible": true}')
        """), {"code": code, "date": signal_date, "price": price, "st": strategy})
        for i, close in enumerate(future_closes, start=1):
            d = signal_date + timedelta(days=i)
            conn.execute(text("""
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                VALUES (:code, :date, :open, :high, :low, :close, :vol)
            """), {
                "code": code, "date": d,
                "open": close, "high": close, "low": close, "close": close, "vol": 100000,
            })


def test_stop_loss_floor_applied_when_dropping_below_9pct():
    """信号后 5 日内跌破 -9% → ret_5d 计为 -9%（止损），而非实际更差的收益。

    entry=10.0，第3日跌到 8.5（-15%）→ 应触发 -9% 止损，ret_5d = -9.0。
    """
    engine = _setup_engine()
    today = date.today()
    # 5 个未来收盘：第3日 8.5（-15%，远低于 -9% 止损线 9.1）
    _insert_signal(engine, "000001", today - timedelta(days=10), 10.0,
                   future_closes=[9.8, 9.0, 8.5, 8.7, 8.6])

    health = build_strategy_health(engine, days=30)
    assert health["status"] == "ok"
    strat = health["strategies"].get("tv_dual_strict")
    assert strat is not None
    # 该信号应被计为止损 -9%，而非 -14%（8.6/10-1）
    # expected_return 应为 -9.0（单一样本）
    assert abs(strat["expected_return"] - FIXED_STOP_LOSS_PCT) < 0.5


def test_take_profit_cap_applied_when_rising_above_15pct():
    """信号后 5 日内涨超 +15% → ret_5d 计为 +15%（止盈），而非更高。

    entry=10.0，涨到 12.0（+20%）→ 应触发 +15% 止盈，ret_5d = +15.0。
    """
    engine = _setup_engine()
    today = date.today()
    _insert_signal(engine, "000002", today - timedelta(days=10), 10.0,
                   future_closes=[10.5, 11.0, 12.0, 11.8, 11.9])

    health = build_strategy_health(engine, days=30)
    strat = health["strategies"].get("tv_dual_strict")
    assert strat is not None
    # 该信号应被计为止盈 +15%，而非 +19%
    assert abs(strat["expected_return"] - TAKE_PROFIT_PCT) < 0.5


def test_normal_return_unchanged_within_bounds():
    """信号后 5 日收益在 [-9%, +15%] 区间内 → 取第5日实际收益，不封顶封底。"""
    engine = _setup_engine()
    today = date.today()
    # entry=10.0，第5日 10.5（+5%），全程在区间内
    _insert_signal(engine, "000003", today - timedelta(days=10), 10.0,
                   future_closes=[10.1, 10.2, 10.3, 10.4, 10.5])

    health = build_strategy_health(engine, days=30)
    strat = health["strategies"].get("tv_dual_strict")
    assert strat is not None
    # 应为实际 +5%
    assert abs(strat["expected_return"] - 5.0) < 0.5
