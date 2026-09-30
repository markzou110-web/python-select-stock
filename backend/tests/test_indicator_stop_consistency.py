"""batch/single 指标一致性与统一止损入口对拍测试。

背景：全市场扫描走 batch_calculate_indicators，单票详情走 calculate_indicators，
两条路径对同一根 K 线的 RSI_WILDER 曾有 ~4 点分歧（纯上涨连板股 batch 给 50、
单票给 100），导致 rsi_min=55 门槛在扫描路径误过滤最强票。本文件锁定两路径
一致性；并锁定 compute_paper_risk_levels_with_context 的 ATR TTL 缓存行为
（15 秒快盯循环换用统一止损入口后的性能保障）。
"""
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.indicators import batch_calculate_indicators, calculate_indicators
from core.risk_engine import _ATR_CACHE, _cached_daily_atr


def _ohlc(code: str, prices: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-09-25", periods=len(prices))
    return pd.DataFrame({
        "code": code,
        "日期": dates,
        "开盘": prices,
        "最高": [p * 1.01 for p in prices],
        "最低": [p * 0.99 for p in prices],
        "收盘": prices,
        "成交量": [100000.0] * len(prices),
    })


def _mixed_prices(n=60, seed=7):
    rng = np.random.default_rng(seed)
    steps = rng.normal(loc=0.001, scale=0.02, size=n)
    return list(10 * np.cumprod(1 + steps))


def test_batch_and_single_rsi_agree_on_mixed_series():
    prices = _mixed_prices()
    single = calculate_indicators(_ohlc("600100", prices).set_index("日期"))
    batch = batch_calculate_indicators(_ohlc("600100", prices))
    single_rsi = float(single["RSI_WILDER"].iloc[-1])
    batch_rsi = float(batch[batch["code"] == "600100"]["RSI_WILDER"].iloc[-1])
    assert abs(single_rsi - batch_rsi) < 0.5


def test_batch_and_single_rsi_agree_on_limit_up_streak():
    # 连续上涨：两路径都必须给 100（此前 batch 给 50，最强票被 rsi_min 误过滤）
    prices = [10 * (1 + 0.099) ** i for i in range(30)]
    single = calculate_indicators(_ohlc("600200", prices).set_index("日期"))
    batch = batch_calculate_indicators(_ohlc("600200", prices))
    assert float(single["RSI_WILDER"].iloc[-1]) == 100.0
    assert float(batch[batch["code"] == "600200"]["RSI_WILDER"].iloc[-1]) == 100.0


def test_batch_and_single_rsi_agree_on_pure_decline():
    prices = [20 * (1 - 0.05) ** i for i in range(30)]
    single = calculate_indicators(_ohlc("600300", prices).set_index("日期"))
    batch = batch_calculate_indicators(_ohlc("600300", prices))
    assert float(single["RSI_WILDER"].iloc[-1]) == 0.0
    assert float(batch[batch["code"] == "600300"]["RSI_WILDER"].iloc[-1]) == 0.0


def test_atr_cache_hits_within_ttl(monkeypatch):
    _ATR_CACHE.clear()
    calls = {"n": 0}
    fake_atr = pd.DataFrame({"收盘": np.linspace(10, 12, 30),
                             "最高": np.linspace(10.2, 12.2, 30),
                             "最低": np.linspace(9.8, 11.8, 30),
                             "开盘": np.linspace(10.1, 12.1, 30),
                             "成交量": [1000.0] * 30})

    def fake_load(code, start, engine=None):
        calls["n"] += 1
        return fake_atr

    import core.db as db_mod
    monkeypatch.setattr(db_mod, "load_from_db", fake_load)
    monkeypatch.setattr(db_mod, "get_db_engine", lambda: object())

    first = _cached_daily_atr("700100")
    second = _cached_daily_atr("700100")
    assert first is not None and first == second
    assert calls["n"] == 1  # TTL 内第二次调用命中缓存
    _ATR_CACHE.clear()
