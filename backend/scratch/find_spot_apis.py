import akshare as ak

apis = dir(ak)
spot_apis = [api for api in apis if 'spot' in api or 'realtime' in api or 'live' in api]
print("All APIs containing 'spot', 'realtime' or 'live':")
for api in sorted(spot_apis):
    print(f"- {api}")
