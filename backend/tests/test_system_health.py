import os
import sys
from datetime import date, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.system_health import build_system_health_snapshot


def test_system_health_reports_missing_database():
    payload = build_system_health_snapshot(None)

    assert payload["status"] == "error"
    assert payload["score"] == 0
    assert payload["checks"][0]["name"] == "database_connection"


def test_system_health_snapshot_with_empty_schema():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    payload = build_system_health_snapshot(engine)

    assert payload["status"] in {"warn", "error"}
    assert payload["summary"]["stock_count"] == 0
    assert any(item["name"] == "schema_integrity" and item["status"] == "ok" for item in payload["checks"])
    assert any("行情" in item for item in payload["recommendations"])


def test_system_health_snapshot_scores_ready_system():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO stock_basic (code, name, industry) VALUES ('000001', '平安银行', '银行')"))
        start = date(2025, 1, 1)
        for i in range(130):
            conn.execute(
                text("""
                    INSERT INTO daily_k (code, date, open, high, low, close, vol)
                    VALUES ('000001', :date, 10, 11, 9, 10.5, 100000)
                """),
                {"date": (start + timedelta(days=i)).isoformat()},
            )
        conn.execute(text("INSERT INTO scan_history (code, date, name, score) VALUES ('000001', '2025-05-30', '平安银行', 88)"))
        conn.execute(text("INSERT INTO stock_fundamentals (code, roe, net_profit_yoy) VALUES ('000001', 12, 20)"))
        conn.execute(text("INSERT INTO strategy_templates (name, strategy_type, params_json) VALUES ('稳健', 'squeeze', '{}')"))
        conn.execute(text("INSERT INTO strategy_templates (name, strategy_type, params_json) VALUES ('进攻', 'pine', '{}')"))
        conn.execute(text("INSERT INTO strategy_templates (name, strategy_type, params_json) VALUES ('防守', 'consensus', '{}')"))
        conn.execute(text("INSERT INTO system_settings (key, value) VALUES ('bark_key', 'demo')"))

    payload = build_system_health_snapshot(engine)

    assert payload["score"] >= 70
    assert payload["summary"]["latest_scan_date"] == "2025-05-30"
    assert any(item["name"] == "notification" and item["status"] == "ok" for item in payload["checks"])


def test_system_health_detects_same_day_high_contamination():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO paper_trading (
                code, name, entry_price, entry_date, current_price, high_since_entry, status, trade_mode
            ) VALUES (
                '000001', '平安银行', 10, CURRENT_DATE, 10, 11, 'OPEN', 'SIMULATED'
            )
        """))

    payload = build_system_health_snapshot(engine)

    assert payload["summary"]["same_day_high_anomalies"] == 1
    assert any(
        item["name"] == "position_decision_consistency" and item["status"] == "error"
        for item in payload["checks"]
    )
