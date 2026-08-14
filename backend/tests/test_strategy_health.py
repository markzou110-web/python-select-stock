"""Tests for core/strategy_health.py — 简化止损模型（改动 #3）。

验证 build_strategy_health 的 ret_5d 反映止损保护的经济性：
- 5日内曾跌破 -9% → 计为 -9%（止损出场），而非更差
- 5日内曾涨超 +15% → 计为 +15%（止盈），而非更高
"""
import os
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.strategy_health import apply_strategy_health_controls, build_strategy_health
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


def _insert_signal_bars(engine, code, signal_date, price, future_bars, strategy="tv_dual_strict"):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO scan_history (code, date, price, strategy_type, price_action_detail)
            VALUES (:code, :date, :price, :st, '{"research_eligible": true}')
        """), {"code": code, "date": signal_date, "price": price, "st": strategy})
        for i, (open_, high, low, close) in enumerate(future_bars, start=1):
            conn.execute(text("""
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                VALUES (:code, :date, :open, :high, :low, :close, 100000)
            """), {
                "code": code,
                "date": signal_date + timedelta(days=i),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
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


def test_immature_signal_does_not_count_as_flat_return():
    engine = _setup_engine()
    _insert_signal(
        engine,
        "000004",
        date.today() - timedelta(days=3),
        10.0,
        future_closes=[10.5, 10.6],
    )

    health = build_strategy_health(engine, days=30)

    assert health["strategies"]["tv_dual_strict"]["signals"] == 0


def test_take_profit_before_later_stop_is_counted_chronologically():
    engine = _setup_engine()
    _insert_signal_bars(
        engine,
        "000005",
        date.today() - timedelta(days=10),
        10.0,
        future_bars=[
            (10.2, 11.6, 10.1, 11.5),
            (11.4, 11.5, 8.9, 9.0),
            (9.0, 9.2, 8.8, 9.1),
            (9.1, 9.3, 9.0, 9.2),
            (9.2, 9.4, 9.1, 9.3),
        ],
    )

    health = build_strategy_health(engine, days=30)

    assert abs(health["strategies"]["tv_dual_strict"]["expected_return"] - TAKE_PROFIT_PCT) < 0.5


def test_same_day_stop_and_take_uses_conservative_stop():
    engine = _setup_engine()
    _insert_signal_bars(
        engine,
        "000006",
        date.today() - timedelta(days=10),
        10.0,
        future_bars=[
            (10.0, 11.6, 9.0, 10.5),
            (10.5, 10.8, 10.2, 10.6),
            (10.6, 10.9, 10.4, 10.7),
            (10.7, 11.0, 10.5, 10.8),
            (10.8, 11.1, 10.6, 10.9),
        ],
    )

    health = build_strategy_health(engine, days=30)

    assert abs(health["strategies"]["tv_dual_strict"]["expected_return"] - FIXED_STOP_LOSS_PCT) < 0.5


def test_selection_health_cannot_pause_execution_without_execution_evidence():
    rows = [{
        "strategy_type": "tv_dual_strict", "market_regime": "CRITICAL",
        "sector_phase": "SECTOR_CONFIRM", "trade_eligible": True,
        "trade_bucket": "TRADE", "trade_blockers": [],
    }]
    health = {
        "selection": {
            "strategies": {"tv_dual_strict": {"signals": 80, "status": "PAUSED", "reason": "观察候选负期望"}},
            "segments": {},
        },
        "execution": {"strategies": {}, "segments": {}},
    }

    apply_strategy_health_controls(rows, health)

    assert rows[0]["trade_eligible"] is True
    assert rows[0]["selection_health"]["status"] == "PAUSED"
    assert rows[0]["execution_health"]["status"] == "OBSERVE"
    assert rows[0]["strategy_health_control_cohort"] == "execution"


def test_execution_health_is_reported_separately_from_selection_health():
    engine = _setup_engine()
    signal_date = date.today() - timedelta(days=10)
    _insert_signal(engine, "000010", signal_date, 10.0, [9.5, 9.3, 9.0, 8.9, 8.8])
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO execution_intents (
                intent_id, signal_date, issued_at, source, code, strategy_type,
                instruction, state, planned_entry_price, signal_snapshot, updated_at
            ) VALUES (
                'int-health', :signal_date, :issued_at, 'bark', '000011', 'tv_dual_strict',
                '可交易', 'ISSUED', 10.0,
                '{"market_regime":"OFFENSIVE","sector_phase":"SECTOR_CONFIRM"}', :issued_at
            )
        """), {"signal_date": signal_date, "issued_at": datetime.combine(signal_date, datetime.min.time())})
        for i, close in enumerate([10.1, 10.2, 10.3, 10.4, 10.5], start=1):
            conn.execute(text("""
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                VALUES ('000011', :date, :close, :close, :close, :close, 100000)
            """), {"date": signal_date + timedelta(days=i), "close": close})

    health = build_strategy_health(engine, days=30)

    assert health["selection"]["strategies"]["tv_dual_strict"]["expected_return"] < 0
    assert health["execution"]["strategies"]["tv_dual_strict"]["expected_return"] > 0
    assert health["control_cohort"] == "execution"
