import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.celery_app import celery_app


def test_early_value_scan_is_independent_scheduled_task():
    schedule = celery_app.conf.beat_schedule["early-value-independent-scan-1315"]

    assert schedule["task"] == "tasks.early_value_scan"
    assert "early_value" not in str(
        celery_app.conf.beat_schedule["noon-scan-review-1305"].get("kwargs", {})
    )
