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
