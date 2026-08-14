import os
import sys
import asyncio
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.notifier import BARK_BODY_MAX_BYTES, Notifier, bark_encoded_body_size, split_message_body


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


def test_bark_falls_back_to_system_proxy(monkeypatch):
    calls = []
    monkeypatch.setattr("core.notifier._bark_key", lambda: "test-key")
    monkeypatch.setattr("core.notifier._system_https_proxy", lambda: "http://127.0.0.1:7897")
    monkeypatch.setattr("core.notifier.time.sleep", lambda _: None)

    def fake_post(*args, **kwargs):
        calls.append(kwargs.get("proxies"))
        if len(calls) == 1:
            raise requests.Timeout("direct timeout")
        return _Response(True, 200)

    monkeypatch.setattr("core.notifier.requests.post", fake_post)

    assert Notifier._send_bark("title", "body") is True
    assert calls == [
        {"http": None, "https": None},
        {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"},
    ]


def test_notifier_splits_oversized_bark_before_delivery(monkeypatch):
    sent = []
    resolved = []
    monkeypatch.setattr("core.notifier._bark_key", lambda: "test-key")
    monkeypatch.setattr(
        Notifier,
        "_send_bark",
        staticmethod(lambda title, body, **kwargs: sent.append((title, body)) or True),
    )
    monkeypatch.setattr("core.audit_log.record_notification_audit", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "core.audit_log.resolve_queued_notification",
        lambda *args, **kwargs: resolved.append(args) or True,
    )

    body = "题材异动\n" + ("半导体强势，等待确认。\n" * 300)
    result = asyncio.run(Notifier().send("盘中题材", body, channels=["bark"]))

    assert result == {"bark": True}
    assert len(sent) > 1
    assert all(bark_encoded_body_size(part) <= BARK_BODY_MAX_BYTES for _, part in sent)
    assert sent[0][0].startswith("盘中题材 (1/")
    assert resolved == [("bark", part_title, part_body) for part_title, part_body in sent]


def test_partial_split_failure_only_queues_unsent_parts(monkeypatch):
    queued = []
    outcomes = iter([True, False])
    monkeypatch.setattr("core.notifier._bark_key", lambda: "test-key")
    monkeypatch.setattr(
        Notifier,
        "_send_bark",
        staticmethod(lambda *args, **kwargs: next(outcomes)),
    )
    monkeypatch.setattr(
        "core.audit_log.enqueue_notification",
        lambda *args, **kwargs: queued.append((args, kwargs)) or True,
    )
    monkeypatch.setattr("core.audit_log.record_notification_audit", lambda *args, **kwargs: True)

    body = "题材异动\n" + ("半导体强势，等待确认。\n" * 300)
    parts = split_message_body(body)
    result = asyncio.run(Notifier().send("盘中题材", body, channels=["bark"]))

    assert result == {"bark": False}
    assert [item[0][2] for item in queued] == parts[1:]
    assert queued[0][0][1] == f"盘中题材 (2/{len(parts)})"
    assert all(
        bark_encoded_body_size(item[0][2]) <= BARK_BODY_MAX_BYTES
        for item in queued
    )


def test_configured_bark_failure_is_persisted_for_retry(monkeypatch):
    queued = []
    monkeypatch.setattr("core.notifier._bark_key", lambda: "test-key")
    monkeypatch.setattr(Notifier, "_send_bark", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "core.audit_log.enqueue_notification",
        lambda *args, **kwargs: queued.append((args, kwargs)) or True,
    )
    monkeypatch.setattr("core.audit_log.record_notification_audit", lambda *args, **kwargs: True)

    result = asyncio.run(Notifier().send("title", "full body", channels=["bark"]))

    assert result == {"bark": False}
    assert queued[0][0] == ("bark", "title", "full body")


def test_bark_413_is_permanent_and_not_enqueued(monkeypatch):
    calls = []
    queued = []
    monkeypatch.setattr("core.notifier._bark_key", lambda: "test-key")
    monkeypatch.setattr(
        "core.notifier.requests.post",
        lambda *args, **kwargs: calls.append((args, kwargs)) or _Response(False, 413),
    )
    monkeypatch.setattr(
        "core.audit_log.enqueue_notification",
        lambda *args, **kwargs: queued.append((args, kwargs)) or True,
    )
    monkeypatch.setattr("core.audit_log.record_notification_audit", lambda *args, **kwargs: True)

    result = asyncio.run(Notifier().send("title", "body", channels=["bark"]))

    assert result == {"bark": False}
    assert len(calls) == 1
    assert queued == []


def test_split_message_body_respects_utf8_byte_limit():
    body = "标题\n" + ("半导体强势，等待确认。\n" * 300)

    parts = split_message_body(body)

    assert len(parts) > 1
    assert all(bark_encoded_body_size(part) <= BARK_BODY_MAX_BYTES for part in parts)
