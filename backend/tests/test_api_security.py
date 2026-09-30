import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api import app
from core.config import config


def test_write_auth_rejects_missing_token_and_accepts_valid_token(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_AUTH", True)
    monkeypatch.setattr(config, "API_TOKEN", "test-secret")
    client = TestClient(app)
    denied = client.post("/api/system/strategy-release/evaluate", json={})
    assert denied.status_code == 401
    allowed = client.post(
        "/api/system/strategy-release/evaluate", json={},
        headers={"Authorization": "Bearer test-secret"},
    )
    assert allowed.status_code != 401


def test_security_headers_are_present():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_settings_get_masks_bark_key_and_post_mask_is_noop(monkeypatch):
    """GET /api/settings 不得回传明文 bark_key；POST 掩码值不得覆盖真值。"""
    from routers import settings as settings_router

    monkeypatch.setattr(
        settings_router, "get_setting",
        lambda key, default="": "abcd1234efgh" if key == "bark_key" else default,
    )
    client = TestClient(app)
    data = client.get("/api/settings").json()
    assert "bark_key" not in data
    assert data["bark_key_set"] is True
    assert data["bark_key_masked"].endswith("efgh")
    assert "•" in data["bark_key_masked"]

    response = client.post("/api/settings", json={"bark_key": data["bark_key_masked"]})
    assert response.status_code == 200
    assert settings_router.get_setting("bark_key", "") == "abcd1234efgh"


def test_webhook_settings_never_return_raw_url(monkeypatch):
    from routers import settings as settings_router

    monkeypatch.setattr(
        settings_router, "get_setting",
        lambda key, default="": (
            "https://open.feishu.cn/open-apis/bot/v2/hook/secrettoken123"
            if key == "feishu_webhook_url" else default
        ),
    )
    client = TestClient(app)
    data = client.get("/api/settings/webhook").json()
    assert "url" not in data["feishu"]
    assert "secrettoken123" not in data["feishu"]["preview"]
    assert data["feishu"]["configured"] is True


def test_side_effect_gets_require_token_when_auth_enabled(monkeypatch):
    """GET /api/scan（触发扫描）与 GET /api/settings/test/push（真实推送）
    属副作用端点，开启鉴权后必须带 token。"""
    monkeypatch.setattr(config, "ENABLE_AUTH", True)
    monkeypatch.setattr(config, "API_TOKEN", "test-secret")
    client = TestClient(app)
    for path in ("/api/scan", "/api/settings/test/push"):
        assert client.get(path).status_code == 401, path
        assert client.get(
            path, headers={"Authorization": "Bearer test-secret"}
        ).status_code != 401, path


def test_redis_health_watch_pushes_only_on_transition(monkeypatch):
    import asyncio as _asyncio

    import core.tasks as tasks_mod

    sent = []
    monkeypatch.setattr(tasks_mod, "notifier", type(
        "N", (), {"send": staticmethod(lambda *a, **k: _asyncio.ensure_future(_asyncio.sleep(0, result={"bark": True})))}
    )())
    # 直接驱动状态机：down → down 不推送；down → up 推送一条
    monkeypatch.setattr(tasks_mod, "_REDIS_HEALTH_STATE", "down")
    class _FakeRedis:
        def __init__(self, *a, **k): pass
        def ping(self): return True

        @classmethod
        def from_url(cls, *a, **k):
            return cls()
    import redis as redis_mod
    monkeypatch.setattr(redis_mod, "Redis", _FakeRedis)
    result = tasks_mod.redis_health_watch.run()
    assert result["transition"] is True and result["status"] == "up"
    assert tasks_mod._REDIS_HEALTH_STATE == "up"
