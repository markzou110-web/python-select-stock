"""数据新鲜度看门狗测试（data-freshness-watchdog-v1）。

覆盖：心跳基准选取、各链路滞后容差判定、节假日墙钟容忍、空库场景。
"""
import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.db import init_db
from core.models import Base
from core.tasks import compute_data_freshness


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)
    return engine


def _seed(engine, table, date_column, dates):
    with engine.begin() as conn:
        for day in dates:
            conn.execute(text(f"INSERT INTO {table} ({date_column}) VALUES (:d)"), {"d": day})
        # 最小占位列（各表 NOT NULL 列差异大，按需补充）
        if table == "daily_k":
            conn.execute(text(
                "UPDATE daily_k SET code='600000', open=1, high=1, low=1, close=1, vol=1 WHERE date = :d"
            ), {"d": dates[-1]})


def test_fresh_links_report_no_stale():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO daily_k (code, date, open, high, low, close, vol) "
            "VALUES ('600000', '2026-09-28', 1, 1, 1, 1, 1)"
        ))
        conn.execute(text(
            "INSERT INTO limit_up_events (event_date, code, status, first_seen_at, last_seen_at) "
            "VALUES ('2026-09-28', '600000', 'SEALED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO breadth_history (bar_date, scope, industry, advance_ratio, strong_ratio, weak_ratio, avg_return) "
            "VALUES ('2026-09-28', 'MARKET', '__MARKET__', 55.0, 10.0, 5.0, 0.5)"
        ))
    report = compute_data_freshness(engine, now=datetime(2026, 9, 28, 19, 40))
    assert report["reference_date"] == "2026-09-28"
    assert report["stale"] == []


def test_stale_links_are_detected():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO daily_k (code, date, open, high, low, close, vol) "
            "VALUES ('600000', '2026-09-28', 1, 1, 1, 1, 1)"
        ))
        # 涨停事件停在 3 天前（容差 1 天）→ 告警
        conn.execute(text(
            "INSERT INTO limit_up_events (event_date, code, status, first_seen_at, last_seen_at) "
            "VALUES ('2026-09-25', '600000', 'SEALED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
    report = compute_data_freshness(engine, now=datetime(2026, 9, 28, 19, 40))
    assert any("limit_up_events" in item for item in report["stale"])


def test_heartbeat_lag_beyond_holiday_tolerance_alerts():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO daily_k (code, date, open, high, low, close, vol) "
            "VALUES ('600000', '2026-09-10', 1, 1, 1, 1, 1)"
        ))
    report = compute_data_freshness(engine, now=datetime(2026, 9, 28, 19, 40))
    # 心跳滞后 18 天（>5 天长假容忍窗）→ daily_k 断流告警
    assert report["heartbeat_lag_days"] == 18
    assert any("daily_k" in item for item in report["stale"])


def test_empty_database_flags_heartbeat():
    engine = _engine()
    report = compute_data_freshness(engine, now=datetime(2026, 9, 28, 19, 40))
    assert any("daily_k" in item for item in report["stale"])
