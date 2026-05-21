import requests
import os
import re

# Disable proxies
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''

from akshare.stock.cons import zh_sina_a_stock_count_url, zh_sina_a_stock_url, zh_sina_a_stock_payload

print("Count URL:", zh_sina_a_stock_count_url)
try:
    res = requests.get(zh_sina_a_stock_count_url)
    print("Count response status:", res.status_code)
    print("Count response text (first 200 chars):")
    print(res.text[:200])
    match = re.findall(re.compile(r"\d+"), res.text)
    print("Regexp match:", match)
except Exception as e:
    print("Count URL request failed:", e)

print("\nSpot URL:", zh_sina_a_stock_url)
try:
    payload = zh_sina_a_stock_payload.copy()
    payload.update({"page": 1})
    r = requests.get(zh_sina_a_stock_url, params=payload)
    print("Spot response status:", r.status_code)
    print("Spot response text (first 500 chars):")
    print(r.text[:500])
except Exception as e:
    print("Spot URL request failed:", e)
