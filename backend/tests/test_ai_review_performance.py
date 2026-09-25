"""Point-in-time outcome comparison for AI candidate reviews."""
import os
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.ai_review_performance import build_ai_review_performance


def _engine_with_outcomes():
    engine = create_engine("sqlite:///:memory:")
    base = date.today() - timedelta(days=20)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE ai_candidate_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_date VARCHAR(10) NOT NULL,
                code VARCHAR(10) NOT NULL,
                name VARCHAR(40), action VARCHAR(10) NOT NULL,
                confidence INTEGER, source VARCHAR(20) NOT NULL,
                batch_id VARCHAR(48) NOT NULL, created_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE daily_k (
                code VARCHAR(20) NOT NULL, date DATE NOT NULL,
                open FLOAT, high FLOAT, low FLOAT, close FLOAT,
                PRIMARY KEY (code, date)
            )
        """))
        conn.execute(text("""
            INSERT INTO ai_candidate_reviews
                (review_date, code, name, action, confidence, source, batch_id, created_at)
            VALUES
                (:review_date, '000001', '上涨样本', 'BUY', 80, 'scheduled', 'b1', :created_at),
                (:review_date, '000002', '下跌样本', 'WAIT', 55, 'scheduled', 'b1', :created_at)
        """), {
            "review_date": base.isoformat(),
            "created_at": datetime.combine(base, datetime.min.time()),
        })
        up = [100, 102, 103, 105, 107, 110, 112, 114, 116, 118, 120]
        down = [100, 99, 98.5, 98, 97.5, 97, 96.5, 96, 95.5, 95.2, 95]
        bars = []
        for code, closes in (("000001", up), ("000002", down)):
            bars.extend({
                "code": code,
                "date": base + timedelta(days=index),
                "open": 100,
                "high": close,
                "low": close,
                "close": close,
            } for index, close in enumerate(closes))
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close)
            VALUES (:code, :date, :open, :high, :low, :close)
        """), bars)
    return engine


def test_ai_buy_is_compared_with_same_reviewed_universe():
    report = build_ai_review_performance(_engine_with_outcomes(), days=60)

    assert report["status"] == "available"
    assert report["price_basis"] == "next_trading_day_open_to_horizon_close"
    five_day = report["horizons"]["5d"]
    assert five_day["all_reviewed"]["signals"] == 2
    assert five_day["all_reviewed"]["win_rate"] == 50.0
    assert five_day["buy"]["signals"] == 1
    assert five_day["buy"]["win_rate"] == 100.0
    assert five_day["buy_lift"]["win_rate_pct_points"] == 50.0
    assert five_day["wait"]["avg_return"] == -3.0


def test_unmatured_horizons_are_not_counted():
    engine = _engine_with_outcomes()
    with engine.begin() as conn:
        latest = date.today().isoformat()
        conn.execute(text("""
            INSERT INTO ai_candidate_reviews
                (review_date, code, name, action, confidence, source, batch_id, created_at)
            VALUES (:review_date, '000003', '未成熟', 'BUY', 70, 'scheduled', 'b2', :created_at)
        """), {"review_date": latest, "created_at": datetime.now()})
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close)
            VALUES ('000003', :date, 10, 10, 10, 10)
        """), {"date": latest})

    report = build_ai_review_performance(engine, days=60)

    assert report["horizons"]["5d"]["buy"]["signals"] == 1
