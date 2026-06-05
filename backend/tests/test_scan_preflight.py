import os
import sys
from datetime import date, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.data_source_quality import build_local_data_quality_report
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
    assert "local_data_quality" in payload["summary"]


def test_local_data_quality_warns_on_abnormal_jump_and_invalid_price():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO stock_basic (code, name, industry) VALUES ('000001', '一号', '银行')"))
        conn.execute(text("INSERT INTO stock_basic (code, name, industry) VALUES ('000002', '二号', '')"))
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close, vol)
            VALUES
            ('000001', '2025-01-01', 10, 11, 9, 10, 100000),
            ('000002', '2025-01-01', 10, 11, 9, 10, 100000),
            ('000001', '2025-01-02', 10, 40, 9, 40, 100000),
            ('000002', '2025-01-02', 0, 11, 9, 10, 100000)
        """))

    payload = build_local_data_quality_report(engine, target_date="2025-01-02", min_stock_count=2, max_abnormal_move_pct=25)
    statuses = {item["name"]: item["status"] for item in payload["checks"]}

    assert payload["status"] == "error"
    assert statuses["abnormal_move"] == "warn"
    assert statuses["invalid_price"] == "error"
    assert payload["summary"]["missing_industry_count"] == 1
