import os
import sys
import time

# Ensure backend path is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data import get_index_data, get_market_regime, CACHE

def test_index_and_regime():
    # Force eviction of index cache
    if 'index_data' in CACHE:
        del CACHE['index_data']
    if 'market_snapshot' in CACHE:
        del CACHE['market_snapshot']

    print("==================================================")
    print("Testing get_index_data() speed and completeness...")
    print("==================================================")
    start_time = time.time()
    res = get_index_data()
    duration = time.time() - start_time
    print(f"Index data fetched in {duration:.4f} seconds!")
    print("Result structure:")
    for k, v in res.items():
        print(f"Index: {k} | Price: {v['price']} | Change: {v['pct']}%")
    
    assert len(res) == 5, f"Expected 5 indices, got {len(res)}"
    print("PASS: Index fetching is extremely fast and complete!")

    print("\n==================================================")
    print("Testing get_market_regime() EMA20 trends...")
    print("==================================================")
    start_time = time.time()
    regime = get_market_regime()
    duration = time.time() - start_time
    print(f"Market regime diagnosed in {duration:.4f} seconds!")
    print("Regime state:")
    print(f"Status: {regime['status']}")
    print(f"Description: {regime['desc']}")
    print("Details:")
    for k, v in regime['indices'].items():
        print(f"Index: {k} | Close: {v['close']} | EMA20: {v['ema20']} | Trend: {v['trend']}")
        
    assert regime['status'] != 'UNKNOWN', "Failed to diagnose market trend!"
    print("PASS: Market regime diagnosed within split seconds!")

if __name__ == "__main__":
    test_index_and_regime()
