
import akshare as ak
import pandas as pd

def test_money_flow():
    code = "600519"
    market = "sh"
    print(f"Testing stock_individual_fund_flow for {code}...")
    try:
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        print(f"Total rows returned: {len(df)}")
        if not df.empty:
            print("Columns:", df.columns.tolist())
            print("First few rows:")
            print(df.head())
            print("Last few rows:")
            print(df.tail())
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_money_flow()
