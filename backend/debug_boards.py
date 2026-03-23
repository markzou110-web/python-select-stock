import sys
import os
import akshare as ak

# Add backend to path
sys.path.insert(0, '/Users/liangzou/Desktop/AI_Tools/python-select-stock/backend')

def debug_boards():
    print("Fetching industry boards...")
    try:
        df_board = ak.stock_board_industry_name_em()
        if df_board.empty:
            print("Error: Industry boards list is empty.")
            return
        
        print(f"Total boards found: {len(df_board)}")
        print("First 10 boards:")
        print(df_board['板块名称'].head(10).tolist())
        
        test_board = df_board['板块名称'].iloc[0]
        print(f"\nFetching components for board: {test_board}")
        df_cons = ak.stock_board_industry_cons_em(symbol=test_board)
        if df_cons.empty:
            print(f"Error: Components for {test_board} are empty.")
        else:
            print(f"Found {len(df_cons)} components.")
            print("Columns:", df_cons.columns.tolist())
            print("First 5 components (Code, Name):")
            print(df_cons[['代码', '名称']].head(5))

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_boards()
