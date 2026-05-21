import requests
import os

# Disable proxy
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''

symbols = ["bj430139", "bj830832"]
url = f"http://qt.gtimg.cn/q={','.join(symbols)}"

print("Fetching Beijing stocks from Tencent API:", url)
try:
    r = requests.get(url, timeout=5)
    r.encoding = 'gbk'
    lines = r.text.strip().split('\n')
    for line in lines:
        print("Response line:", line[:150])
except Exception as e:
    print("Failed to query Tencent API for Beijing stocks:", e)
