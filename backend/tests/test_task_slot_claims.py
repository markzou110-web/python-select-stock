import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.db import claim_task_slot


def test_task_slot_claim_is_atomic_and_idempotent():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE task_slot_claims(slot_key VARCHAR(160) PRIMARY KEY, claimed_at TIMESTAMP, status VARCHAR(30))"))
    assert claim_task_slot("daily:2026-07-12", engine) is True
    assert claim_task_slot("daily:2026-07-12", engine) is False
