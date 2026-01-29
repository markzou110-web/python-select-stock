import time
from core.data import safe_ak_call, PREFERRED_SOURCES

def test_sync_speed():
    stocks = ['600000', '600001', '600002']
    
    print("🚀 Starting test: Circuit Breaker Verification")
    
    for i, code in enumerate(stocks):
        start = time.time()
        print(f"\n--- [Step {i+1}] Syncing {code} ---")
        try:
            # First call should hit SSL error and Learn
            # Subsequent calls should use Preferred Source (Sina) directly
            res = safe_ak_call('stock_zh_a_hist', symbol=code, period='daily', start_date='20250101')
            elapsed = time.time() - start
            print(f"✅ Finished {code} in {elapsed:.2f}s. Rows: {len(res) if res is not None else 0}")
            print(f"📊 Current Preferred: {PREFERRED_SOURCES.get('stock_zh_a_hist')}")
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == '__main__':
    test_sync_speed()
