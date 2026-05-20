import sys
import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

# Append project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import get_db_engine, get_stock_basic_map
from core.logging_config import logger

def format_tencent_code(code: str) -> str:
    if code.startswith('6') or code.startswith('900'):
        return f'sh{code}'
    elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
        return f'sz{code}'
    elif code.startswith('8') or code.startswith('4') or code.startswith('920'):
        return f'bj{code}'
    return code

def fetch_tencent_chunk(chunk_codes):
    symbols = [format_tencent_code(c) for c in chunk_codes]
    url = f"http://qt.gtimg.cn/q={','.join(symbols)}"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return []
        r.encoding = 'gbk'
        lines = r.text.strip().split('\n')
        results = []
        for line in lines:
            if not line or '"' not in line:
                continue
            try:
                content = line.split('"')[1]
                parts = content.split('~')
                if len(parts) < 46:
                    continue
                
                # Extract fields
                code = parts[2]
                name = parts[1]
                price = float(parts[3]) if parts[3] else None
                open_val = float(parts[5]) if parts[5] else None
                pct_chg = float(parts[32]) if parts[32] else 0.0
                vol = float(parts[6]) if parts[6] else 0.0 # already in lots (手)
                turnover = float(parts[38]) if parts[38] else None
                mkt_cap = float(parts[45]) * 100000000.0 if parts[45] else None # convert "亿" to Yuan
                pe = float(parts[39]) if parts[39] else None
                
                results.append({
                    'code': code,
                    'name': name,
                    'price': price,
                    'open': open_val,
                    'pct_chg': pct_chg,
                    'vol': vol,
                    'turnover': turnover,
                    'mkt_cap': mkt_cap,
                    'pe': pe
                })
            except Exception as ex:
                # Silently skip bad rows
                continue
        return results
    except Exception as e:
        print(f"Chunk fetch failed: {e}")
        return []

def fetch_all_tencent():
    print("Loading stock list from database...")
    basic_map = get_stock_basic_map()
    codes = list(basic_map.keys())
    if not codes:
        print("No stock codes found in DB!")
        return pd.DataFrame()
    
    print(f"Loaded {len(codes)} stock codes. Starting batch fetch from Tencent...")
    start_time = time.time()
    
    # Chunk codes into batches of 200
    chunk_size = 200
    chunks = [codes[i:i + chunk_size] for i in range(0, len(codes), chunk_size)]
    
    all_results = []
    # Fetch in parallel
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_chunk = {executor.submit(fetch_tencent_chunk, chunk): chunk for chunk in chunks}
        for future in as_completed(future_to_chunk):
            res = future.result()
            if res:
                all_results.extend(res)
                
    df = pd.DataFrame(all_results)
    duration = time.time() - start_time
    print(f"Fetch completed in {duration:.2f} seconds. Fetched {len(df)} records.")
    return df

if __name__ == "__main__":
    df = fetch_all_tencent()
    if not df.empty:
        print("\n--- Columns ---")
        print(df.columns.tolist())
        print("\n--- Sample Data ---")
        print(df.head(5))
        print("\n--- Missing Value counts ---")
        print(df.isnull().sum())
    else:
        print("Tencent full fetch failed!")
