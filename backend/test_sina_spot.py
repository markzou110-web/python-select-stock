import sys
import os
import akshare as ak

# Disable proxies
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

def test_sina():
    print("Testing Sina stock spot...")
    try:
        df = ak.stock_zh_a_spot()
        if df.empty:
            print("Error: Sina spot list is empty.")
            return
        
        print(f"Total stocks found: {len(df)}")
        print("Columns:", df.columns.tolist())
        print("\nSample records:")
        print(df.head(5))
        
        if 'symbol' in df.columns:
            # Check for industry in specific columns
            potential_industry_cols = [c for c in df.columns if '行业' in c or 'industry' in c.lower()]
            print(f"Potential industry columns: {potential_industry_cols}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_sina()
