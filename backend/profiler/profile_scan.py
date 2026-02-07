import time
import cProfile
import pstats
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.scan_service import run_market_scan

def run_test_scan():
    print("🚀 Starting test scan for profiling...")
    start_time = time.time()
    # Use small market range and local only for quick test
    results = run_market_scan(
        market_range="沪深300",
        turnover_min=5.0,
        local_only=True
    )
    duration = time.time() - start_time
    print(f"✅ Scan found {len(results)} matches in {duration:.2f}s")
    return results

if __name__ == "__main__":
    profiler = cProfile.Profile()
    profiler.enable()
    run_test_scan()
    profiler.disable()
    
    stats = pstats.Stats(profiler).sort_stats('tottime')
    stats.print_stats(30)
