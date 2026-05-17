import akshare as ak
try:
    df = ak.stock_zh_index_daily(symbol="sh000001")
    print("Sina sh000001:", df.tail())
except Exception as e:
    print("Error sina:", e)

try:
    df = ak.stock_zh_index_daily_tx(symbol="sh000001")
    print("Tencent sh000001:", df.tail())
except Exception as e:
    print("Error tx:", e)
