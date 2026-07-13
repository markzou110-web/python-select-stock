import os
import sys
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.point_in_time_warehouse import build_point_in_time_coverage


def test_point_in_time_coverage_reports_collection_gap():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE point_in_time_stock_snapshots(dataset_version TEXT, as_of TIMESTAMP)"))
        c.execute(text("CREATE TABLE scan_audit_log(scan_date DATE, research_only INTEGER)"))
        c.execute(text("CREATE TABLE event_catalysts(verified INTEGER, published_at TIMESTAMP)"))
    report = build_point_in_time_coverage(engine)
    assert report["status"] == "collecting"
    assert report["target"]["trading_dates"] == 750
