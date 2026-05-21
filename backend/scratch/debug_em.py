import requests
import os

# Disable proxy
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''

url = "https://82.push2.eastmoney.com/api/qt/clist/get"
params = {
    "pn": "1",
    "pz": "10",
    "po": "1",
    "np": "1",
    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
    "fields": "f12,f14"
}

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/center/gridlist.html"
}

print("Trying with headers:")
try:
    r = requests.get(url, params=params, headers=headers, timeout=10)
    print("EM HTTPS status code:", r.status_code)
    print("EM HTTPS response headers:", r.headers)
    print("EM HTTPS response text:", r.text[:200])
except Exception as e:
    print("EM HTTPS direct request failed:", e)

print("\nTrying push2.eastmoney.com with headers:")
try:
    r_main = requests.get("https://push2.eastmoney.com/api/qt/clist/get", params=params, headers=headers, timeout=10)
    print("EM push2 HTTPS status code:", r_main.status_code)
    print("EM push2 HTTPS response text:", r_main.text[:200])
except Exception as e:
    print("EM push2 HTTPS direct request failed:", e)
