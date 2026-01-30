import requests
import json
from datetime import datetime, timedelta

def verify_historical_scan():
    url = "http://127.0.0.1:8000/api/scan"
    
    # Try to find a date in the past that might have data
    # (Assuming user has synced data for the last few days)
    test_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    payload = {
        "threshold": 0.12,
        "vol_multiplier": 1.5,
        "rsi_min": 55,
        "use_macd_filter": True,
        "use_bb_sqz": True,
        "sqz_lookback": 10,
        "use_weekly": True,
        "market_range": "沪深300",
        "turnover_min": 2.0,
        "mkt_cap_min": 0,
        "use_rs_filter": True,
        "local_only": True,
        "strategy": "Resonance",
        "rf_period": 100,
        "rf_multiplier": 3.0,
        "only_signals": False,
        "use_money_flow": False,
        "scan_date": test_date
    }
    
    print(f"🚀 Testing Historical Scan for date: {test_date}...")
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            results = response.json()
            print(f"✅ Success! Found {len(results)} results for {test_date}.")
            if len(results) > 0:
                print(f"Top result: {results[0]['名称']} ({results[0]['代码']}) - Score: {results[0]['Score']}")
        else:
            print(f"❌ Failed! Status Code: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Error connecting to backend: {e}")

if __name__ == "__main__":
    verify_historical_scan()
