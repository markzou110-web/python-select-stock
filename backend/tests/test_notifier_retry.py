import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.notifier import Notifier


class _Response:
    def __init__(self, ok: bool, status_code: int):
        self.ok = ok
        self.status_code = status_code
        self.text = "test"


def test_bark_retries_once_after_http_failure(monkeypatch):
    responses = iter([_Response(False, 503), _Response(True, 200)])
    calls = []
    monkeypatch.setattr("core.notifier._bark_key", lambda: "test-key")
    monkeypatch.setattr("core.notifier.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "core.notifier.requests.post",
        lambda *args, **kwargs: calls.append((args, kwargs)) or next(responses),
    )

    assert Notifier._send_bark("title", "body") is True
    assert len(calls) == 2
