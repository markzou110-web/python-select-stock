import akshare as ak

apis = dir(ak)
tx_apis = [api for api in apis if 'tx' in api or 'tencent' in api]
print("All APIs containing 'tx' or 'tencent':")
for api in sorted(tx_apis):
    print(f"- {api}")
