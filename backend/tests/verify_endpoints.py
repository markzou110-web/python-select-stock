import requests
import time

BASE_URL = "http://localhost:8000"

def test_api_health():
    print("Testing /api/health...")
    try:
        response = requests.get(f"{BASE_URL}/api/health")
        print(f"Status: {response.status_code}, Body: {response.json()}")
        assert response.status_code == 200
        return True
    except Exception as e:
        print(f"Failed: {e}")
        return False

def test_api_sync_status():
    print("Testing /api/sync/status...")
    try:
        response = requests.get(f"{BASE_URL}/api/sync/status")
        print(f"Status: {response.status_code}, Body: {response.json()}")
        assert response.status_code == 200
        return True
    except Exception as e:
        print(f"Failed: {e}")
        return False

def test_api_scan():
    print("Testing /api/stock/scan...")
    payload = {
        "threshold": 0.12,
        "vol_multiplier": 1.5,
        "rsi_min": 55,
        "use_macd_filter": True,
        "market_range": "沪深300",
        "turnover_min": 8.0,
        "local_only": True
    }
    try:
        response = requests.post(f"{BASE_URL}/api/stock/scan", json=payload)
        print(f"Status: {response.status_code}, Results: {len(response.json()) if response.status_code == 200 else response.text}")
        assert response.status_code == 200
        return True
    except Exception as e:
        print(f"Failed: {e}")
        return False

if __name__ == "__main__":
    tests = [test_api_health, test_api_sync_status, test_api_scan]
    results = [t() for t in tests]
    if all(results):
        print("\nAll integration tests passed! ✅")
    else:
        print("\nSome integration tests failed! ❌")
