import os
import sys
from datetime import date, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.scan_preflight import build_scan_preflight


def test_scan_preflight_blocks_without_database():
    payload = build_scan_preflight(None)

    assert payload["status"] == "error"
    assert payload["blocking"] is True


def test_scan_preflight_blocks_empty_daily_k():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    payload = build_scan_preflight(engine)

    assert payload["status"] == "error"
    assert payload["blocking"] is True


def test_scan_preflight_warns_on_thin_coverage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        start = date(2025, 1, 1)
        for i in range(30):
            conn.execute(
                text("""
                    INSERT INTO daily_k (code, date, open, high, low, close, vol)
                    VALUES ('000001', :date, 10, 11, 9, 10.5, 100000)
                """),
                {"date": (start + timedelta(days=i)).isoformat()},
            )

    payload = build_scan_preflight(engine, min_stock_count=1000, min_history_days=120)

    assert payload["status"] == "warn"
    assert payload["blocking"] is False
    assert payload["summary"]["stock_count"] == 1


def test_scan_preflight_passes_ready_data():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        start = date(2025, 1, 1)
        for stock_idx in range(3):
            code = f"00000{stock_idx}"
            for i in range(130):
                conn.execute(
                    text("""
                        INSERT INTO daily_k (code, date, open, high, low, close, vol)
                        VALUES (:code, :date, 10, 11, 9, 10.5, 100000)
                    """),
                    {"code": code, "date": (start + timedelta(days=i)).isoformat()},
                )

    payload = build_scan_preflight(engine, min_stock_count=3, min_history_days=120)

    assert payload["status"] == "ok"
    assert payload["blocking"] is False
    assert payload["summary"]["ready_history_count"] == 3
