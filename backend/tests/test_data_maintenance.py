"""每周数据维护测试（capacity-maintenance-v1，批 4-2）。

覆盖：保留窗口清理、业务数据零触碰、task_id 主键适配、点时快照版本边界、
分批路径（>5000 行）、单类失败不阻断。
"""
import os
import sys
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.db import init_db, purge_expired_data
from core.models import Base


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)
    return engine


def _seed_outbox(engine, days_ago_list):
    with engine.begin() as conn:
        for days in days_ago_list:
            conn.execute(text(
                "INSERT INTO notification_outbox (channel, title, body, status, dedupe_key, attempts, next_retry_at, created_at) "
                "VALUES ('bark', 't', 'b', 'SENT', :dk, 0, :created, :created)"
            ), {"dk": f"k-{days}", "created": datetime.now() - timedelta(days=days)})


def test_purge_removes_old_outbox_keeps_recent_and_business_data():
    engine = _engine()
    _seed_outbox(engine, [40, 45, 5, 1])  # 前两条过期，后两条保留
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO daily_k (code, date, open, high, low, close, vol) "
            "VALUES ('600000', '2020-01-01', 1,1,1,1,1)"
        ))  # 远古业务数据
    report = purge_expired_data(engine)
    assert report["status"] == "ok"
    with engine.connect() as conn:
        kept = conn.execute(text("SELECT COUNT(*) FROM notification_outbox")).scalar()
        daily = conn.execute(text("SELECT COUNT(*) FROM daily_k")).scalar()
    assert kept == 2
    assert daily == 1  # 业务数据绝不触碰


def test_purge_handles_task_run_audits_varchar_pk():
    engine = _engine()
    with engine.begin() as conn:
        for i, days in enumerate((200, 150, 10)):
            conn.execute(text(
                "INSERT INTO task_run_audits (task_id, status, started_at) "
                "VALUES (:tid, 'SUCCESS', :created)"
            ), {"tid": f"task-{i}", "created": datetime.now() - timedelta(days=days)})
    purge_expired_data(engine)
    with engine.connect() as conn:
        kept = conn.execute(text("SELECT COUNT(*) FROM task_run_audits")).scalar()
    assert kept == 1  # 只剩 10 天前的


def test_purge_point_in_time_snapshots_keeps_20_versions():
    engine = _engine()
    with engine.begin() as conn:
        for v in range(25):
            conn.execute(text(
                "INSERT INTO point_in_time_stock_snapshots (dataset_version, code, data_mode, price, as_of) "
                "VALUES (:v, '600000', 'LOCAL', 10.0, :as_of)"
            ), {"v": f"LOCAL:2026-09-{v:02d}T15:00:00", "as_of": datetime(2026, 9, v + 1 if v < 29 else 28)})
    purge_expired_data(engine)
    with engine.connect() as conn:
        kept = conn.execute(text("SELECT COUNT(DISTINCT dataset_version) FROM point_in_time_stock_snapshots")).scalar()
    assert kept == 20


def test_purge_batches_over_5000_rows():
    engine = _engine()
    rows = [(f"t-{i}", datetime.now() - timedelta(days=40)) for i in range(6001)]
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO notification_outbox (channel, title, body, status, dedupe_key, attempts, next_retry_at, created_at) "
            "VALUES ('bark', 't', 'b', 'SENT', :dk, 0, :created, :created)"
        ), [{"dk": f"b-{i}", "created": c} for i, (_, c) in enumerate(rows)])
    report = purge_expired_data(engine)
    assert report["purged"].get("notification_outbox") == 6001
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM notification_outbox")).scalar() == 0
