import akshare as ak

codes = ["sh000001", "sz399006", "sh000300", "sh000688", "sh000852"]
for c in codes:
    try:
        df = ak.stock_zh_index_daily_tx(symbol=c)
        print(f"{c}: {len(df)} rows, last close={df.iloc[-1]['close']}")
    except Exception as e:
        print(f"Error {c}: {e}")
