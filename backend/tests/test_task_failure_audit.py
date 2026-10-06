import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import audit_log
from core.celery_app import audit_task_finished


@pytest.mark.parametrize("state", ["SUCCESS", None])
def test_returned_error_is_audited_as_failure(monkeypatch, state):
    record = MagicMock()
    monkeypatch.setattr(audit_log, "record_task_run", record)

    audit_task_finished(
        task_id="task-1",
        task=SimpleNamespace(name="tasks.scan"),
        state=state,
        retval={"status": "error", "error": "snapshot unavailable"},
    )

    args, kwargs = record.call_args
    assert args == ("task-1", "tasks.scan", "FAILURE")
    assert kwargs["error_message"] == "snapshot unavailable"
    assert json.loads(kwargs["result_summary"]) == {
        "status": "error", "error": "snapshot unavailable",
    }


def test_returned_error_takes_priority_over_partial_errors(monkeypatch):
    record = MagicMock()
    monkeypatch.setattr(audit_log, "record_task_run", record)

    audit_task_finished(
        state="SUCCESS",
        retval={"status": "error", "error": "scan failed", "errors": ["data missing"]},
    )

    assert record.call_args.args[2] == "FAILURE"
    assert record.call_args.kwargs["error_message"] == "scan failed"


@pytest.mark.parametrize(
    "message_fields,expected",
    [
        ({"error": "scan failed", "detail": "data missing", "reason": "blocked"}, "scan failed"),
        ({"error": "", "detail": "data missing", "reason": "blocked"}, "data missing"),
        ({"detail": "", "reason": "blocked"}, "blocked"),
        ({}, "unknown task failure"),
    ],
)
def test_failure_message_uses_available_business_detail(monkeypatch, message_fields, expected):
    record = MagicMock()
    monkeypatch.setattr(audit_log, "record_task_run", record)
    result = {"status": "error", **message_fields}

    audit_task_finished(state="SUCCESS", retval=result)

    assert record.call_args.args[2] == "FAILURE"
    assert record.call_args.kwargs["error_message"] == expected
    assert json.loads(record.call_args.kwargs["result_summary"]) == result


@pytest.mark.parametrize(
    "state,retval,expected",
    [
        ("SUCCESS", {"errors": ["partial scan failure"]}, "SUCCESS_WITH_ERRORS"),
        ("SUCCESS", {"status": "success", "errors": []}, "SUCCESS"),
        ("SUCCESS", [1, 2], "SUCCESS"),
        ("FAILURE", {"errors": ["partial scan failure"]}, "FAILURE"),
        ("REVOKED", {"status": "error", "error": "cancelled"}, "REVOKED"),
    ],
)
def test_existing_task_states_are_preserved(monkeypatch, state, retval, expected):
    record = MagicMock()
    monkeypatch.setattr(audit_log, "record_task_run", record)

    audit_task_finished(state=state, retval=retval)

    assert record.call_args.args[2] == expected
    assert record.call_args.kwargs.get("error_message") is None
