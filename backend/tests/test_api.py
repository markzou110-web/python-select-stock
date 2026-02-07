import sys
import os
from fastapi.testclient import TestClient

# Add parent directory to path to import app from main
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_get_indices():
    response = client.get("/api/market/indices")
    # This might depend on network or cache, but it should return 200 if cache is pre-warmed or network is up
    assert response.status_code == 200
    assert isinstance(response.json(), (list, dict))

def test_get_sync_status():
    response = client.get("/api/sync/status")
    assert response.status_code == 200
    assert "is_running" in response.json()

def test_get_settings():
    response = client.get("/api/settings/")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)
