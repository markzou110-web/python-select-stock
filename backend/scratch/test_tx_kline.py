import requests
import json

def test_tx_kline():
    # Test for sh000001 (上证指数)
    url = "https://web.ifzq.gtimg.cn/app/kline/kline?symbol=sh000001&type=day&num=60"
    print(f"Requesting K-line from: {url}")
    try:
        r = requests.get(url, timeout=5)
        print(f"Status Code: {r.status_code}")
        data = r.json()
        print("Data keys:", data.keys())
        if 'data' in data:
            print("Type of data['data']:", type(data['data']))
            print("Value of data['data'] (truncated):", str(data['data'])[:500])
        else:
            print(f"Unexpected response structure: {data}")
    except Exception as e:
        print(f"Error fetching from Tencent web API: {e}")

if __name__ == "__main__":
    test_tx_kline()
