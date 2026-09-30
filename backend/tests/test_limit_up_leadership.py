import os
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.decision_layer import _leadership_score
import pytest

from core.limit_up_leadership import (
    _MINUTE_BAR_WATERMARK,
    _POOL_SNAPSHOT,
    collect_candidate_minute_bars,
    collect_limit_up_events,
    load_limit_up_event_map,
    save_minute_bars,
)


@pytest.fixture(autouse=True)
def _clear_collector_watermarks():
    """隔离进程内水位：签名快照/分钟bar水位跨测试残留会破坏 diff 断言。"""
    _POOL_SNAPSHOT.clear()
    _MINUTE_BAR_WATERMARK.clear()
    yield
    _POOL_SNAPSHOT.clear()
    _MINUTE_BAR_WATERMARK.clear()


def _engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE limit_up_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date DATE NOT NULL,
                code VARCHAR(20) NOT NULL,
                name VARCHAR(80),
                industry VARCHAR(100),
                status VARCHAR(20) NOT NULL,
                first_limit_time VARCHAR(6),
                last_limit_time VARCHAR(6),
                break_count INTEGER DEFAULT 0,
                limit_up_streak INTEGER DEFAULT 0,
                seal_amount FLOAT DEFAULT 0,
                turnover FLOAT DEFAULT 0,
                amount FLOAT DEFAULT 0,
                first_seen_at TIMESTAMP,
                last_seen_at TIMESTAMP,
                UNIQUE(event_date, code)
            )
        """))
        conn.execute(text("""
            CREATE TABLE intraday_minute_bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(20) NOT NULL,
                bar_time VARCHAR(19) NOT NULL,
                open FLOAT,
                close FLOAT,
                high FLOAT,
                low FLOAT,
                volume FLOAT,
                amount FLOAT,
                average_price FLOAT,
                created_at TIMESTAMP,
                UNIQUE(code, bar_time)
            )
        """))
        conn.commit()
    return engine


def test_collection_saves_sealed_and_broken_events(monkeypatch):
    sealed = pd.DataFrame([{
        "代码": "000001", "名称": "一号", "所属行业": "银行",
        "首次封板时间": "093501", "最后封板时间": "100001",
        "炸板次数": 1, "连板数": 2, "封板资金": 200000000,
        "换手率": 8.0, "成交额": 500000000,
    }])
    broken = pd.DataFrame([{
        "代码": "000002", "名称": "二号", "所属行业": "银行",
        "首次封板时间": "094000", "炸板次数": 3, "换手率": 12.0,
        "成交额": 300000000,
    }])
    monkeypatch.setattr("core.limit_up_leadership.ak.stock_zt_pool_em", lambda date: sealed)
    monkeypatch.setattr("core.limit_up_leadership.ak.stock_zt_pool_zbgc_em", lambda date: broken)

    engine = _engine()
    result = collect_limit_up_events("20260612", engine, datetime(2026, 6, 12, 10, 5))
    event_map = load_limit_up_event_map("2026-06-12", engine)

    assert result == {"sealed": 1, "broken": 1, "saved": 2, "total_in_pool": 2, "errors": 0}
    assert event_map["000001"]["status"] == "SEALED"
    assert event_map["000001"]["limit_up_sector_rank"] == 1
    assert event_map["000002"]["status"] == "BROKEN"
    assert event_map["000002"]["limit_up_sector_rank"] == 2


def test_collection_keeps_available_pool_when_other_source_fails(monkeypatch):
    sealed = pd.DataFrame([{
        "代码": "000001", "名称": "一号", "所属行业": "银行",
        "首次封板时间": "093501", "炸板次数": 0, "连板数": 1,
    }])
    monkeypatch.setattr("core.limit_up_leadership.ak.stock_zt_pool_em", lambda date: sealed)
    monkeypatch.setattr(
        "core.limit_up_leadership.ak.stock_zt_pool_zbgc_em",
        lambda date: (_ for _ in ()).throw(RuntimeError("source down")),
    )

    result = collect_limit_up_events("20260612", _engine(), datetime(2026, 6, 12, 10, 5))

    assert result["sealed"] == 1
    assert result["saved"] == 1
    assert result["errors"] == 1


def test_limit_up_evidence_enriches_leadership_score_and_explanation():
    stock = {
        "sector_role": "CORE",
        "sector_alignment_score": 80,
        "sector_relative_pct": 2,
        "涨幅%": 10,
        "limit_up_status": "SEALED",
        "limit_up_sector_rank": 1,
        "break_count": 0,
        "limit_up_streak": 3,
        "seal_amount": 300000000,
    }

    score = _leadership_score(stock)

    assert score >= 80
    assert stock["leadership_components"]["first_limit_rank"] == 100
    assert "板块第1只触及涨停" in stock["leadership_reason"]


def test_minute_bars_are_saved_idempotently():
    frame = pd.DataFrame([{
        "时间": "2026-06-12 09:31:00",
        "开盘": 10.0, "收盘": 10.1, "最高": 10.2, "最低": 9.9,
        "成交量": 1000, "成交额": 10100, "均价": 10.05,
    }])
    engine = _engine()

    assert save_minute_bars("000001", frame, engine) == 1
    frame.loc[0, "收盘"] = 10.2
    assert save_minute_bars("000001", frame, engine) == 1
    with engine.connect() as conn:
        row = conn.execute(text("SELECT COUNT(*), MAX(close) FROM intraday_minute_bars")).fetchone()

    assert row == (1, 10.2)


def test_candidate_minute_bar_failures_do_not_pause_future_collections(monkeypatch):
    engine = _engine()
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE scan_history (
                code VARCHAR(20),
                date DATE,
                score FLOAT
            )
        """))
        conn.execute(text("""
            INSERT INTO scan_history (code, date, score)
            VALUES ('000001', '2026-06-12', 90), ('000002', '2026-06-12', 80), ('000003', '2026-06-12', 70)
        """))
        conn.commit()

    def fail_fetch(*args, **kwargs):
        raise RuntimeError("source down")

    monkeypatch.setattr("core.limit_up_leadership.ak.stock_zh_a_hist_min_em", fail_fetch)

    result = collect_candidate_minute_bars("2026-06-12", engine, max_codes=3)

    assert result == {
        "codes": 3,
        "bars": 0,
        "snapshot_bars": 0,
        "eastmoney_bars": 0,
        "errors": 3,
        "empty": 0,
        "source_paused": 0,
    }


def test_candidate_minute_bar_fetch_retries_once(monkeypatch):
    engine = _engine()
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE scan_history (
                code VARCHAR(20),
                date DATE,
                score FLOAT
            )
        """))
        conn.execute(text("""
            INSERT INTO scan_history (code, date, score)
            VALUES ('000001', '2026-06-12', 90)
        """))
        conn.commit()

    calls = {"count": 0}
    frame = pd.DataFrame([{
        "时间": "2026-06-12 09:31:00",
        "开盘": 10.0, "收盘": 10.1, "最高": 10.2, "最低": 9.9,
        "成交量": 1000, "成交额": 10100, "均价": 10.05,
    }])

    def flaky_fetch(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary source error")
        return frame

    monkeypatch.setattr("core.limit_up_leadership.ak.stock_zh_a_hist_min_em", flaky_fetch)

    result = collect_candidate_minute_bars("2026-06-12", engine, max_codes=1)

    assert calls["count"] == 2
    assert result == {
        "codes": 1,
        "bars": 1,
        "snapshot_bars": 0,
        "eastmoney_bars": 1,
        "errors": 0,
        "empty": 0,
        "source_paused": 0,
    }


def test_candidate_minute_bar_prefers_live_snapshot_over_eastmoney(monkeypatch):
    today = datetime.now().strftime("%Y-%m-%d")
    engine = _engine()
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE scan_history (
                code VARCHAR(20),
                date DATE,
                score FLOAT
            )
        """))
        conn.execute(
            text("INSERT INTO scan_history (code, date, score) VALUES ('000001', :today, 90)"),
            {"today": today},
        )
        conn.commit()

    snapshot = pd.DataFrame([{
        "code": "000001",
        "price": 10.2,
        "open": 10.0,
        "high": 10.3,
        "low": 9.9,
        "vol": 1000,
        "amount": 10200,
    }])
    snapshot.attrs["fetched_at"] = datetime.now()

    monkeypatch.setattr("core.data.get_market_snapshot", lambda: snapshot)
    monkeypatch.setattr("core.data.is_snapshot_stale", lambda frame: False)

    def fail_fetch(*args, **kwargs):
        raise AssertionError("Eastmoney should not be called when live snapshot is available")

    monkeypatch.setattr("core.limit_up_leadership.ak.stock_zh_a_hist_min_em", fail_fetch)

    result = collect_candidate_minute_bars(today, engine, max_codes=1)

    assert result["snapshot_bars"] == 1
    assert result["eastmoney_bars"] == 0
    assert result["errors"] == 0


def test_second_identical_collection_writes_only_changed_rows(monkeypatch):
    """变化量写入：同一池重复采集 → 0 行落库；封单变化 → 只写该行。"""
    sealed = pd.DataFrame([{
        "代码": "000011", "名称": "一号", "所属行业": "银行",
        "首次封板时间": "093501", "最后封板时间": "100001",
        "炸板次数": 1, "连板数": 2, "封板资金": 200000000,
        "换手率": 8.0, "成交额": 500000000,
    }])
    monkeypatch.setattr("core.limit_up_leadership.ak.stock_zt_pool_em", lambda date: sealed)
    monkeypatch.setattr("core.limit_up_leadership.ak.stock_zt_pool_zbgc_em", lambda date: pd.DataFrame())
    engine = _engine()

    first = collect_limit_up_events("20260701", engine, datetime(2026, 7, 1, 10, 0))
    assert first["saved"] == 1 and first["total_in_pool"] == 1
    second = collect_limit_up_events("20260701", engine, datetime(2026, 7, 1, 10, 1))
    assert second["saved"] == 0  # 无变化不重写

    sealed.loc[0, "封板资金"] = 260000000  # 封单变化
    third = collect_limit_up_events("20260701", engine, datetime(2026, 7, 1, 10, 2))
    assert third["saved"] == 1  # 只写变化行


def test_minute_bar_watermark_skips_unchanged_history(monkeypatch):
    """分钟 bar 水位：第二次拉全天只落水位之后的新 bar。"""
    bars = pd.DataFrame([
        {"时间": "2026-07-01 10:00:00", "开盘": 1, "收盘": 1, "最高": 1, "最低": 1, "成交量": 1, "成交额": 1, "均价": 1},
        {"时间": "2026-07-01 10:01:00", "开盘": 1, "收盘": 1, "最高": 1, "最低": 1, "成交量": 1, "成交额": 1, "均价": 1},
    ])
    monkeypatch.setattr("core.limit_up_leadership._snapshot_minute_frame", lambda codes, date: pd.DataFrame())
    monkeypatch.setattr(
        "core.limit_up_leadership._fetch_minute_bars",
        lambda code, date, retries=1: bars.copy(),
    )
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE scan_history (code VARCHAR(20), date DATE, score FLOAT)"))
        conn.execute(text("INSERT INTO limit_up_events (event_date, code, status) VALUES ('2026-07-01', '000021', 'SEALED')"))

    first = collect_candidate_minute_bars("2026-07-01", engine, max_codes=1)
    assert first["bars"] == 2
    second = collect_candidate_minute_bars("2026-07-01", engine, max_codes=1)
    assert second["bars"] == 0  # 全天已落库，水位后无新 bar
