"""小名单快报价（get_fast_quotes）与持仓秒级快盯（FastPositionWatcher）测试。

- get_fast_quotes：腾讯直连 → 新浪直连 → 全市场快照过滤 的回退顺序与列名归一；
- FastPositionWatcher：击穿有效止损告警、30 分钟冷却、收回后重置冷却。
"""
import os
import sys
from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import core.data as data_module
from core.risk_engine import _ATR_CACHE
from core.data import get_fast_quotes
from core.sentinel import FastPositionWatcher
from core.models import Base, PaperTrading


@pytest.fixture(autouse=True)
def _clear_fast_quote_cache():
    data_module._FAST_QUOTE_CACHE.clear()
    yield
    data_module._FAST_QUOTE_CACHE.clear()


def _tencent_payload(price=10.0, last_close=10.5):
    return {
        "600519": {
            "name": "贵州茅台", "price": price, "last_close": last_close,
            "open": 10.2, "high": 10.8, "low": 9.9, "vol": 12345.0,
            "quote_time": "20260928143000", "change_pct": (price - last_close) / last_close * 100,
        },
    }


def test_get_fast_quotes_normalizes_tencent_payload(monkeypatch):
    calls = []
    monkeypatch.setattr("core.direct_sources.tencent_quote", lambda codes: calls.append(codes) or _tencent_payload())
    df = get_fast_quotes(["600519"])
    assert calls == [["600519"]]
    assert df.iloc[0]["price"] == 10.0
    assert df.iloc[0]["high"] == 10.8
    # 涨跌幅以昨收现算：(10.0 - 10.5) / 10.5 * 100
    assert df.iloc[0]["pct_chg"] == pytest.approx((10.0 - 10.5) / 10.5 * 100)
    assert df.attrs["source"] == "腾讯直连快报价"
    assert df.attrs["fetched_at"] is not None
    assert df.attrs["data_date"] == "2026-09-28"  # 来自 quote_time


def test_get_fast_quotes_falls_back_to_sina_when_tencent_fails(monkeypatch):
    def _boom(codes):
        raise RuntimeError("tencent down")
    monkeypatch.setattr("core.direct_sources.tencent_quote", _boom)
    sina_df = pd.DataFrame([{"code": "600519", "price": 10.0, "open": 10.0, "high": 10.0, "low": 10.0, "pct_chg": 1.0}])
    monkeypatch.setattr("core.direct_sources.snapshot_from_sina", lambda codes: sina_df)
    df = get_fast_quotes(["600519"])
    assert df.attrs["source"] == "新浪直连快报价"
    assert df.iloc[0]["price"] == 10.0


def test_get_fast_quotes_full_snapshot_as_last_resort(monkeypatch):
    monkeypatch.setattr("core.direct_sources.tencent_quote", lambda codes: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr("core.direct_sources.snapshot_from_sina", lambda codes: (_ for _ in ()).throw(RuntimeError()))
    full = pd.DataFrame([
        {"code": "600519", "price": 10.0, "high": 10.5, "low": 9.5, "pct_chg": 1.0},
        {"code": "000001", "price": 12.0, "high": 12.5, "low": 11.5, "pct_chg": -1.0},
    ])
    full.attrs = {"fetched_at": datetime.now(), "data_date": "2026-09-28", "source": "akshare东财"}
    monkeypatch.setattr("core.data.get_market_snapshot", lambda **kwargs: full)
    df = get_fast_quotes(["600519"])
    assert len(df) == 1  # 只保留请求的票
    assert df.attrs["source"] == "akshare东财(过滤)"


def test_get_fast_quotes_uses_ttl_cache(monkeypatch):
    calls = []
    monkeypatch.setattr("core.direct_sources.tencent_quote", lambda codes: calls.append(codes) or _tencent_payload())
    get_fast_quotes(["600519"])
    get_fast_quotes(["600519"])  # TTL 内第二次调用应命中缓存
    assert len(calls) == 1
    get_fast_quotes(["600519"], force_refresh=True)
    assert len(calls) == 2


def _engine_with_position(entry_price=100.0, trade_mode="REAL"):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(PaperTrading(
        code="600519", name="贵州茅台", entry_price=entry_price, shares=100,
        high_since_entry=entry_price, status="OPEN", trade_mode=trade_mode,
    ))
    session.commit()
    session.close()
    return engine


def _quotes_frame(price, low=None):
    return pd.DataFrame([{
        "code": "600519", "price": price, "high": max(price, 100.0),
        "low": low if low is not None else price, "pct_chg": 0.0,
    }])


def test_fast_watch_alerts_on_stop_breach_then_cooldowns(monkeypatch):
    engine = _engine_with_position()
    watcher = FastPositionWatcher(interval_seconds=15)
    now = datetime(2026, 9, 28, 10, 0, 0)

    # 快盯现走统一入口 with_context：注入封闭环境（牛市、无 ATR），
    # 使止损退化为固定 -9% 口径，断言数字可确定
    _ATR_CACHE.clear()
    monkeypatch.setattr("core.risk_engine._cached_daily_atr", lambda code, ttl_seconds=600: None)
    monkeypatch.setattr("core.data.get_market_regime", lambda *a, **k: {"status": "bull"})
    # DB 持久化冷却在本测试内替换为内存桩：避免测试触碰真实开发库的
    # system_setting 槽位（跨测试运行残留会导致本测试第二次击穿被静默）
    cooldown_store: dict[str, str] = {}
    monkeypatch.setattr(
        "core.sentinel._fast_watch_cooldown_active",
        lambda code, now: cooldown_store.get(code, ""),
    )
    monkeypatch.setattr(
        "core.sentinel._fast_watch_cooldown_set",
        lambda code, now: cooldown_store.__setitem__(code, now.isoformat(timespec="seconds")),
    )
    monkeypatch.setattr(
        "core.sentinel._fast_watch_cooldown_clear",
        lambda code: cooldown_store.pop(code, None),
    )

    # entry=100 → 固定止损 91.0；现价 90 击穿
    monkeypatch.setattr("core.data.get_fast_quotes", lambda codes: _quotes_frame(90.0))
    alerts = watcher.watch_once(engine=engine, now=now)
    assert len(alerts) == 1
    assert "击穿有效止损 91.00" in alerts[0]
    assert "[实盘]" in alerts[0]
    assert "600519" in cooldown_store  # DB 槽位已记录（跨进程冷却语义）

    # 冷却期内重复击穿不重复推送
    assert watcher.watch_once(engine=engine, now=now) == []

    # 收回（价格回升）→ 冷却清零；再次击穿可重新提醒
    monkeypatch.setattr("core.data.get_fast_quotes", lambda codes: _quotes_frame(95.0))
    assert watcher.watch_once(engine=engine, now=now) == []
    monkeypatch.setattr("core.data.get_fast_quotes", lambda codes: _quotes_frame(90.0))
    assert len(watcher.watch_once(engine=engine, now=now)) == 1


def test_fast_watch_silent_when_no_breach_or_no_positions(monkeypatch):
    # 未击穿：现价 95 > 止损 91
    engine = _engine_with_position()
    watcher = FastPositionWatcher(interval_seconds=15)
    monkeypatch.setattr("core.data.get_fast_quotes", lambda codes: _quotes_frame(95.0))
    assert watcher.watch_once(engine=engine, now=datetime(2026, 9, 28, 10, 0, 0)) == []

    # 无持仓：直接返回空
    empty_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=empty_engine)
    assert watcher.watch_once(engine=empty_engine) == []


def test_fast_watch_uses_intraday_low_for_breach(monkeypatch):
    """盘中曾击穿（当日 low < 止损）即便现价收回也应提醒（与风控同口径）。"""
    # 与上一测试相同：隔离 DB 冷却槽位，避免触碰真实开发库
    monkeypatch.setattr("core.sentinel._fast_watch_cooldown_active", lambda code, now: False)
    monkeypatch.setattr("core.sentinel._fast_watch_cooldown_set", lambda code, now: None)
    monkeypatch.setattr("core.sentinel._fast_watch_cooldown_clear", lambda code: None)
    engine = _engine_with_position()
    watcher = FastPositionWatcher(interval_seconds=15)
    monkeypatch.setattr("core.data.get_fast_quotes", lambda codes: _quotes_frame(93.0, low=90.0))
    alerts = watcher.watch_once(engine=engine, now=datetime(2026, 9, 28, 10, 0, 0))
    assert len(alerts) == 1
