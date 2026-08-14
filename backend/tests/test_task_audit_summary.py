import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.celery_app import _task_result_summary


def test_task_result_summary_keeps_business_fields_without_large_body():
    summary = _task_result_summary({
        "slot": "noon",
        "scan_count": 4,
        "errors": ["scan: snapshot unavailable"],
        "body": "x" * 5000,
        "notification": {"bark": True},
        "next_day_reviewed": 5,
        "next_day_confirmed": 1,
        "next_day_confirmation_bark": 1,
    })

    payload = json.loads(summary)

    assert payload["slot"] == "noon"
    assert payload["scan_count"] == 4
    assert payload["errors"] == ["scan: snapshot unavailable"]
    assert payload["notification"] == {"bark": True}
    assert payload["next_day_reviewed"] == 5
    assert payload["next_day_confirmed"] == 1
    assert "body" not in payload
