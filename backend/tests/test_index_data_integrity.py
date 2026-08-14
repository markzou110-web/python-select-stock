"""Tests for index-data integrity — 防止用个股冒充指数。

回归 BUG：get_index_hist 本地兜底曾用 daily_k 里 code='000001' 的数据，但该 code 在
库里是**平安银行**（个股，收盘~10），不是上证指数（收盘~3000+）。这导致 benchmark/alpha
以及 market_regime 判定全部基于一只银行股。修复：检测到收盘价 < 100 视为个股，拒绝当指数用。
"""
import os
import sys
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base


def _setup_engine_with_bank_stock():
    """内存 SQLite，daily_k 里塞一只 code='000001' 的银行股（收盘~10，模拟平安银行）。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    today = date.today()
    rows = []
    for i in range(100):
        d = today - timedelta(days=99 - i)
        rows.append({
            "code": "000001", "date": d, "open": 10.0, "high": 10.5,
            "low": 9.8, "close": 10.0 + (i % 5) * 0.1, "vol": 100000.0,
        })
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO daily_k (code, date, open, high, low, close, vol)
                             VALUES (:code, :date, :open, :high, :low, :close, :vol)"""), rows)
    return engine


def test_local_bank_stock_not_used_as_index(monkeypatch):
    """daily_k 里 000001 是银行股（收盘~10）→ get_index_hist 本地兜底应拒绝当指数用。"""
    import core.data as data_mod
    import core.db as db_mod

    fake_engine = _setup_engine_with_bank_stock()

    # 强制走本地兜底路径：禁用在线 Sina/Eastmoney/akshare 接口
    monkeypatch.setattr(data_mod, "_fetch_index_hist_sina", lambda code: pd.DataFrame())
    monkeypatch.setattr(data_mod.ak, "index_zh_a_hist", lambda **_kwargs: pd.DataFrame())
    # data.py 内部用 `from core.db import get_db_engine`，patch 源模块的引用
    monkeypatch.setattr(db_mod, "get_db_engine", lambda: fake_engine)
    monkeypatch.setattr(data_mod, "get_db_engine", lambda: fake_engine)
    # 跳过缓存，确保走真实路径
    monkeypatch.setattr(data_mod, "get_cached_data", lambda key, ttl: None)
    monkeypatch.setattr(data_mod, "set_cached_data", lambda key, val: None)

    result = data_mod.get_index_hist("000001")
    # 银行股收盘 ~10 < 100 → 拒绝当指数 → 返回空 DataFrame（诚实降级，不用错误数据）
    assert result is None or result.empty, "银行股(收盘<100)不应被当作上证指数返回"


def test_prefixed_local_index_data_is_used(monkeypatch):
    """独立代码 sh000001 的本地指数数据应作为上证指数兜底。"""
    import core.data as data_mod
    import core.db as db_mod

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    today = date.today()
    rows = []
    for i in range(100):
        d = today - timedelta(days=99 - i)
        rows.append({
            "code": "sh000001", "date": d, "open": 4000.0, "high": 4100.0,
            "low": 3950.0, "close": 4000.0 + i * 2, "vol": 100000.0,
        })
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO daily_k (code, date, open, high, low, close, vol)
                             VALUES (:code, :date, :open, :high, :low, :close, :vol)"""), rows)

    monkeypatch.setattr(data_mod, "_fetch_index_hist_sina", lambda code: pd.DataFrame())
    monkeypatch.setattr(data_mod.ak, "index_zh_a_hist", lambda **_kwargs: pd.DataFrame())
    monkeypatch.setattr(db_mod, "get_db_engine", lambda: engine)
    monkeypatch.setattr(data_mod, "get_db_engine", lambda: engine)
    monkeypatch.setattr(data_mod, "get_cached_data", lambda key, ttl: None)

    result = data_mod.get_index_hist("000001")
    assert result is not None and not result.empty
    assert float(result["收盘"].iloc[-1]) > 1000  # 真指数数据


def test_local_chinext_stock_not_used_as_chinext_index(monkeypatch):
    """daily_k 里 399006 若是低价个股污染数据，也不能当创业板指使用。"""
    import core.data as data_mod
    import core.db as db_mod

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    today = date.today()
    rows = []
    for i in range(100):
        d = today - timedelta(days=99 - i)
        rows.append({
            "code": "399006", "date": d, "open": 10.0, "high": 10.5,
            "low": 9.8, "close": 12.0 + (i % 5) * 0.1, "vol": 100000.0,
        })
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO daily_k (code, date, open, high, low, close, vol)
                             VALUES (:code, :date, :open, :high, :low, :close, :vol)"""), rows)

    monkeypatch.setattr(data_mod, "_fetch_index_hist_sina", lambda code: pd.DataFrame())
    monkeypatch.setattr(data_mod.ak, "index_zh_a_hist", lambda **_kwargs: pd.DataFrame())
    monkeypatch.setattr(db_mod, "get_db_engine", lambda: engine)
    monkeypatch.setattr(data_mod, "get_db_engine", lambda: engine)
    monkeypatch.setattr(data_mod, "get_cached_data", lambda key, ttl: None)
    monkeypatch.setattr(data_mod, "set_cached_data", lambda key, val: None)

    result = data_mod.get_index_hist("399006")

    assert result is None or result.empty


def test_unavailable_index_source_is_short_cached(monkeypatch):
    """同一页面重复计算指标时，不应连续等待同一个已超时的指数源。"""
    import core.data as data_mod
    import core.db as db_mod

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    cache = {}
    calls = []

    def fetch(_code):
        calls.append(_code)
        return pd.DataFrame()

    monkeypatch.setattr(data_mod, "_fetch_index_hist_sina", fetch)
    monkeypatch.setattr(data_mod.ak, "index_zh_a_hist", lambda **_kwargs: pd.DataFrame())
    monkeypatch.setattr(db_mod, "get_db_engine", lambda: engine)
    monkeypatch.setattr(data_mod, "get_db_engine", lambda: engine)
    monkeypatch.setattr(data_mod, "get_cached_data", lambda key, _ttl: cache.get(key))
    monkeypatch.setattr(data_mod, "set_cached_data", lambda key, value: cache.__setitem__(key, value))

    assert data_mod.get_index_hist("000001").empty
    assert data_mod.get_index_hist("000001").empty
    assert calls == ["000001"]
