import akshare as ak
import time

def test_sina_index_hist():
    print("Testing Sina index daily hist (ak.stock_zh_index_daily) on this IP...")
    start_time = time.time()
    try:
        df = ak.stock_zh_index_daily(symbol="sz399006")
        duration = time.time() - start_time
        if df is not None and not df.empty:
            print(f"SUCCESS: Fetched {len(df)} rows in {duration:.2f} seconds!")
            print(df.tail(5))
        else:
            print("FAILED: Returned empty DataFrame.")
    except Exception as e:
        print(f"FAILED with exception: {e}")

if __name__ == "__main__":
    test_sina_index_hist()
