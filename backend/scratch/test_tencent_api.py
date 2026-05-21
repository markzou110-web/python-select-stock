import requests
import os

# Disable proxy
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''

symbols = ["sh600000", "sz000001", "sz300059"]
url = f"http://qt.gtimg.cn/q={','.join(symbols)}"

print("Fetching from Tencent API:", url)
try:
    r = requests.get(url, timeout=5)
    r.encoding = 'gbk' # Tencent API returns GBK encoding
    print("Status code:", r.status_code)
    lines = r.text.strip().split('\n')
    for line in lines:
        print("\nRaw line:", line[:150])
        # Extract content between double quotes
        content = line.split('"')[1]
        parts = content.split('~')
        print(f"Parts length: {len(parts)}")
        for idx, part in enumerate(parts):
            # Print some interesting indices
            if idx in [1, 2, 3, 4, 5, 6, 32, 33, 34, 38, 39, 44, 45]:
                print(f"  Index {idx}: {part}")
except Exception as e:
    print("Failed to query Tencent API:", e)
