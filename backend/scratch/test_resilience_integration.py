import os
import sys
import pandas as pd
import akshare as ak
import traceback

# 确保 backend 路径在 sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 禁用代理
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''

def test_raw_sina_error():
    print("Testing RAW Sina Snapshot Fetch and Column Mapping...")
    try:
        df = ak.stock_zh_a_spot()
        if df is None or df.empty:
            print("Sina returned empty snapshot directly!")
            return
            
        print("Sina columns:", df.columns.tolist())
        
        # 统一列名映射
        df = df.rename(columns={
            '代码': 'code',
            '名称': 'name',
            '最新价': 'price',
            '今开': 'open',
            '涨跌幅': 'pct_chg',
            '成交量': 'vol'
        })
        
        # 提取 6 位纯数字代码
        print("Extracting code...")
        df['code'] = df['code'].str.extract(r'(\d{6})')
        
        # 新浪成交量单位是股，东财是手。统一转换为手 (1手 = 100股)
        print("Converting volume...")
        if 'vol' in df.columns:
            df['vol'] = pd.to_numeric(df['vol'], errors='coerce') / 100.0
            
        # 补全东财快照特有的字段
        print("Filling fundamental columns...")
        df['turnover'] = None
        df['mkt_cap'] = None
        df['pe'] = None
        
        # 只保留标准快照列
        print("Filtering columns...")
        cols = ['code', 'name', 'price', 'open', 'pct_chg', 'vol', 'turnover', 'mkt_cap', 'pe']
        df = df[cols]
        print("SUCCESS! Completed all mappings without exception.")
        print(df.head(2))
    except Exception as e:
        print("FAILED with exception:")
        traceback.print_exc()

if __name__ == "__main__":
    test_raw_sina_error()
