import os
import sys
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.task_idempotency import claim_slot, finish_slot, release_slot


def _engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE task_slot_claims(slot_key VARCHAR(160) PRIMARY KEY, claimed_at TIMESTAMP, status VARCHAR(30))"))
    return engine


def test_slot_duplicate_timeout_takeover_and_manual_release():
    engine = _engine()
    assert claim_slot("scan:today", engine=engine) is True
    assert claim_slot("scan:today", engine=engine) is False
    with engine.begin() as conn:
        conn.execute(text("UPDATE task_slot_claims SET claimed_at=:old WHERE slot_key='scan:today'"), {"old": datetime.now() - timedelta(hours=3)})
    assert claim_slot("scan:today", timeout_minutes=60, engine=engine) is True
    finish_slot("scan:today", "SUCCESS", engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE task_slot_claims SET claimed_at=:old WHERE slot_key='scan:today'"), {"old": datetime.now() - timedelta(hours=3)})
    assert claim_slot("scan:today", timeout_minutes=60, engine=engine) is False
    finish_slot("scan:today", "FAILED", engine)
    assert claim_slot("scan:today", engine=engine) is True
    finish_slot("scan:today", "FAILED", engine)
    assert release_slot("scan:today", engine) is True
