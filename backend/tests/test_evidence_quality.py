import json
import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import system as system_router


def test_evidence_quality_summarises_persisted_shadow_results(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE scan_history (
                id INTEGER PRIMARY KEY, date DATE, data_date DATE, scanned_at TIMESTAMP,
                price_action_detail TEXT
            )
        """))
        detail = {
            "evidence_grade": "D",
            "evidence_gate_mode": "SHADOW",
            "evidence_pipeline_stage": "DECISION_READY",
            "evidence_reason_codes": ["QUOTE_STALE"],
            "evidence_bundle": {"domains": {"quote": {"status": "STALE"}}},
        }
        conn.execute(text("""
            INSERT INTO scan_history (date, data_date, scanned_at, price_action_detail)
            VALUES ('2026-07-13', '2026-07-13', '2026-07-13 10:31:00', :detail)
        """), {"detail": json.dumps(detail)})
    monkeypatch.setattr(system_router, "get_db_engine", lambda: engine)

    result = system_router.get_evidence_quality()

    assert result["sample_size"] == 1
    assert result["grade_distribution"] == {"D": 1}
    assert result["reason_codes"] == {"QUOTE_STALE": 1}
    assert result["pipeline_stages"] == {"DECISION_READY": 1}
    assert result["domains"]["quote"] == {"STALE": 1}


def test_evidence_quality_ignores_unrated_legacy_rows(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE scan_history (
                id INTEGER PRIMARY KEY, date DATE, data_date DATE, scanned_at TIMESTAMP,
                price_action_detail TEXT
            )
        """))
        conn.execute(text("""
            INSERT INTO scan_history (date, data_date, scanned_at, price_action_detail)
            VALUES ('2026-07-13', '2026-07-13', '2026-07-13 10:31:00', '{}')
        """))
    monkeypatch.setattr(system_router, "get_db_engine", lambda: engine)

    assert system_router.get_evidence_quality()["sample_size"] == 0
