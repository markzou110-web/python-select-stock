import akshare as ak
from sqlalchemy import create_engine, text
import pandas as pd
from datetime import datetime, timedelta

# Update with user's info
USER = 'liangzou'
DB = 'stock_db'
URL = f"postgresql://{USER}:@{'localhost'}:{'5432'}/{DB}"

def test():
    try:
        engine = create_engine(URL)
        with engine.connect() as conn:
            print("Connection successful!")
            res = conn.execute(text("SELECT 1")).fetchone()
            print(f"Test query result: {res}")
            
        print("Testing akshare...")
        df = ak.stock_zh_a_hist(symbol="600000", period="daily", start_date="20240101", adjust="qfq")
        print(f"Akshare result: {len(df)} rows")
        
        if not df.empty:
            print("Testing to_sql...")
            data = df[['日期', '开盘', '最高', '最低', '收盘', '成交量']].copy()
            data['code'] = '600000'
            data = data.rename(columns={'日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'vol'})
            data.to_sql('daily_k_test', engine, if_exists='replace', index=False)
            print("to_sql successful!")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test()
