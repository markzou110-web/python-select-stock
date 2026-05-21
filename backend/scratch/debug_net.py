import requests
import os
import sys

print("Python version:", sys.version)
print("Environment variables:")
for k, v in sorted(os.environ.items()):
    if 'proxy' in k.lower() or 'no_' in k.lower():
        print(f"  {k} = {v}")

print("\nTesting HTTP connection to baidu.com...")
try:
    r = requests.get("https://www.baidu.com", timeout=5)
    print("Baidu status:", r.status_code)
    print("Baidu headers:", r.headers)
except Exception as e:
    print("Baidu connection failed:", e)

print("\nTesting HTTP connection to httpbin.org...")
try:
    r = requests.get("https://httpbin.org/ip", timeout=5)
    print("Httpbin status:", r.status_code)
    print("Httpbin response:", r.text)
except Exception as e:
    print("Httpbin connection failed:", e)
