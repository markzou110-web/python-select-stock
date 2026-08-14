"""Settings notification endpoints must report actual channel delivery."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import settings


def test_webhook_test_does_not_treat_false_delivery_as_success(monkeypatch):
    async def fake_send(*args, **kwargs):
        return {"bark": False}

    monkeypatch.setattr("core.notifier.notifier.send", fake_send)

    result = settings.test_webhook("bark")

    assert result["status"] == "error"
    assert result["delivery"] == {"bark": False}


def test_push_test_reports_actual_bark_success(monkeypatch):
    async def fake_send(*args, **kwargs):
        return {"bark": True}

    monkeypatch.setattr("core.notifier.notifier.send", fake_send)

    result = settings.test_push_notification()

    assert result["status"] == "success"
    assert result["delivery"] == {"bark": True}
