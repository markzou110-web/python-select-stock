import os
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.decision_layer import _leadership_score
from core.limit_up_leadership import collect_limit_up_events, load_limit_up_event_map, save_minute_bars


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

    assert result == {"sealed": 1, "broken": 1, "saved": 2, "errors": 0}
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
