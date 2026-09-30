"""涨停情绪周期测试（借鉴 easy-stock 超短情绪；数据源 limit_up_events）。

compute_limit_up_sentiment 只读聚合涨停/炸板家数、最高连板、晋级率、炸板率；
record_limit_up_sentiment 与 record_breadth_extremes 写同一 MARKET 行但各管
各列，互不覆盖。
"""
import os
import sys
from datetime import date

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.db import init_db, record_breadth_extremes, record_limit_up_sentiment
from core.market_regime import compute_limit_up_sentiment, limit_up_sentiment_ebb
from core.models import Base

DAY1 = "2026-09-24"  # 周四
DAY2 = "2026-09-25"  # 周五


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)  # 走真实迁移路径，验证 breadth_history zt_* 新列
    return engine


def _seed_events(engine, rows):
    with engine.begin() as conn:
        for row in rows:
            conn.execute(text("""
                INSERT INTO limit_up_events (
                    event_date, code, name, industry, status, break_count,
                    limit_up_streak, first_seen_at, last_seen_at
                ) VALUES (:event_date, :code, '测试', '行业', :status, 0, :streak,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), row)


def test_compute_aggregates_sentiment_with_promotion_rate():
    engine = _engine()
    rows = [{"event_date": DAY1, "code": f"6000{i:02d}", "status": "SEALED", "streak": 1} for i in range(10)]
    rows += [{"event_date": DAY2, "code": f"6001{i:02d}", "status": "SEALED", "streak": 1} for i in range(5)]
    rows += [{"event_date": DAY2, "code": f"6002{i:02d}", "status": "SEALED", "streak": 2} for i in range(3)]
    rows += [{"event_date": DAY2, "code": "600300", "status": "BROKEN", "streak": 0},
             {"event_date": DAY2, "code": "600301", "status": "BROKEN", "streak": 0}]
    _seed_events(engine, rows)

    stats = compute_limit_up_sentiment(engine, event_date=DAY2)
    assert stats["bar_date"] == DAY2
    assert stats["prev_bar_date"] == DAY1
    assert stats["sealed_count"] == 8
    assert stats["broken_count"] == 2
    assert stats["max_streak"] == 2
    assert stats["streak_ge2_count"] == 3
    # 晋级率 = 今日 2 板及以上 3 家 / 昨日涨停 10 家
    assert stats["promotion_rate"] == 30.0
    # 炸板率 = 2 / (8 + 2)
    assert stats["broken_rate"] == 20.0


def test_compute_without_prev_day_returns_none_promotion():
    engine = _engine()
    _seed_events(engine, [
        {"event_date": DAY2, "code": "600100", "status": "SEALED", "streak": 1},
        {"event_date": DAY2, "code": "600101", "status": "BROKEN", "streak": 0},
    ])
    stats = compute_limit_up_sentiment(engine)
    assert stats["bar_date"] == DAY2
    assert stats["prev_bar_date"] is None
    assert stats["promotion_rate"] is None
    assert stats["broken_rate"] == 50.0


def test_compute_empty_table_returns_zeroed_result():
    engine = _engine()
    stats = compute_limit_up_sentiment(engine)
    assert stats["bar_date"] is None
    assert stats["sealed_count"] == 0
    assert stats["broken_rate"] is None


def test_ebb_predicate_uses_threshold_and_fails_open():
    assert limit_up_sentiment_ebb({"broken_rate": 41.0}) is True
    assert limit_up_sentiment_ebb({"broken_rate": 39.0}) is False
    assert limit_up_sentiment_ebb({"broken_rate": None}) is False
    assert limit_up_sentiment_ebb({}) is False


def test_record_upsert_coexists_with_breadth_extremes_columns():
    engine = _engine()
    stats = {"bar_date": DAY2, "sealed_count": 8, "broken_count": 2,
             "max_streak": 2, "promotion_rate": 30.0, "broken_rate": 20.0}
    assert record_limit_up_sentiment(stats, engine) is True

    extremes = {"bar_date": DAY2, "nh_count": 55, "nl_count": 3,
                "pct_above_ma50": 61.5, "total_count": 5000}
    assert record_breadth_extremes(extremes, engine) is True

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM breadth_history WHERE scope='MARKET' AND bar_date=:d"
        ), {"d": DAY2}).mappings().one()
    assert row["zt_sealed_count"] == 8
    assert row["zt_broken_rate"] == 20.0
    assert row["nh_count"] == 55  # NH-NL 列未被情绪 upsert 覆盖为 NULL

    # 再次更新情绪列，NH-NL 列仍保留
    stats["broken_rate"] = 45.0
    assert record_limit_up_sentiment(stats, engine) is True
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM breadth_history WHERE scope='MARKET' AND bar_date=:d"
        ), {"d": DAY2}).mappings().one()
    assert row["zt_broken_rate"] == 45.0
    assert row["nh_count"] == 55


def test_record_rejects_empty_event_day():
    engine = _engine()
    assert record_limit_up_sentiment({"bar_date": DAY2, "sealed_count": 0,
                                      "broken_count": 0}, engine) is False
    assert record_limit_up_sentiment({}, engine) is False


def test_sentiment_line_flags_ebb(monkeypatch):
    import core.tasks as tasks

    engine = _engine()
    stats = {"bar_date": DAY2, "sealed_count": 5, "broken_count": 5,
             "max_streak": 3, "promotion_rate": 20.0, "broken_rate": 50.0}
    record_limit_up_sentiment(stats, engine)
    line = tasks._latest_limit_up_sentiment_line(engine)
    assert line is not None
    assert "涨停5家/炸板5家" in line
    assert "最高3板" in line
    assert "降暴露" in line

    stats["broken_rate"] = 10.0
    record_limit_up_sentiment(stats, engine)
    line = tasks._latest_limit_up_sentiment_line(engine)
    assert "降暴露" not in line
