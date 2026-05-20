import requests

def test_indices():
    indices = ["sh000001", "sz399006", "sh000300", "sh000688", "sh000852"]
    url = f"http://qt.gtimg.cn/q={','.join(indices)}"
    print(f"Requesting indices from Tencent API: {url}")
    try:
        r = requests.get(url, timeout=5)
        r.encoding = 'gbk'
        lines = r.text.strip().split('\n')
        for line in lines:
            if not line or '"' not in line:
                continue
            content = line.split('"')[1]
            parts = content.split('~')
            if len(parts) < 33:
                print(f"Bad format: {line}")
                continue
            code = parts[2]
            name = parts[1]
            price = parts[3]
            pct_chg = parts[32]
            print(f"Code: {code} | Name: {name} | Price: {price} | Change: {pct_chg}%")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_indices()
