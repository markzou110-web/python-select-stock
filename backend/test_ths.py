import akshare as ak
try:
    df = ak.stock_board_industry_name_ths()
    print(f"THS Sectors: {len(df)}")
    print(df.head())
except Exception as e:
    print(f"Error: {e}")
