import akshare as ak

try:
    print("Testing THS sectors...")
    df = ak.stock_board_industry_name_ths()
    print("THS success! Columns:", df.columns.tolist())
    print(df.head())
except Exception as e:
    print("THS failed:", e)

try:
    print("\nTesting Sina sectors...")
    df = ak.stock_sector_spot()
    print("Sina success! Columns:", df.columns.tolist())
    print(df.head())
except Exception as e:
    print("Sina failed:", e)
