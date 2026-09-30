"""市场状态闸门 R3（market-state-gate-v1-shadow）测试。

依据 regime_attribution 2026-09-30 三段 walk-forward：截面代理 10 日动量
< -3% 期间交易期望三段一致为负。SHADOW 语义：只记录/提示，不改行为。
"""
import os
import sys
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.db import init_db
from core.market_regime import (
    MARKET_STATE_MOM_THRESHOLD_PCT,
    compute_market_state_gate,
)
from core.models import Base
from core.validation_gate import PROMOTION_REGISTRY


def _engine_with_proxy(daily_returns):
    """按给定截面日收益序列建库：每票维护连续价格，截面均值逐日精确等于 ret。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)
    dates = pd.bdate_range(end="2026-09-25", periods=len(daily_returns))
    prices = {f"6000{i}": base for i, base in enumerate((10.0, 20.0, 30.0))}
    with engine.begin() as conn:
        for day, ret in zip(dates, daily_returns):
            for code, price in list(prices.items()):
                prev = price
                close = price * (1 + ret)
                prices[code] = close
                conn.execute(text(
                    "INSERT INTO daily_k (code, date, open, high, low, close, vol) "
                    "VALUES (:c, :d, :o, :h, :l, :cl, 100)"
                ), {"c": code, "d": day.date().isoformat(),
                    "o": prev, "h": max(prev, close) * 1.001,
                    "l": min(prev, close) * 0.999, "cl": close})
    return engine


def test_gate_blocks_when_momentum_below_threshold():
    # 前 8 天 +0.5%/日，后 3 天 -1.5%/日 → 10 日动量显著为负
    returns = [0.005] * 8 + [-0.02] * 3 + [0.001] * 5
    gate = compute_market_state_gate(_engine_with_proxy(returns))
    assert gate["mom_10d_pct"] is not None and gate["mom_10d_pct"] < MARKET_STATE_MOM_THRESHOLD_PCT
    assert gate["blocked"] is True
    assert gate["policy_version"] == "market-state-gate-v1-shadow"


def test_gate_open_in_normal_market():
    returns = [0.004] * 16  # 持续温和上涨
    gate = compute_market_state_gate(_engine_with_proxy(returns))
    assert gate["blocked"] is False


def test_gate_fail_open_without_data():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)
    gate = compute_market_state_gate(engine)
    assert gate["blocked"] is False and gate["mom_10d_pct"] is None
    assert compute_market_state_gate(None)["blocked"] is False


def test_gate_registered_for_promotion():
    assert "market_state_gate" in PROMOTION_REGISTRY


def test_premarket_gate_line(monkeypatch):
    import core.tasks as tasks

    # 上涨市场 → 行含 [正常]；下跌 → 行含 暂停
    rising = _engine_with_proxy([0.004] * 16)
    line = tasks._market_state_gate_line(rising)
    assert line is not None and "正常" in line and "SHADOW" in line
    falling = _engine_with_proxy([0.005] * 8 + [-0.02] * 3 + [0.001] * 5)
    line = tasks._market_state_gate_line(falling)
    assert "暂停" in line
