import os
import sys
import pandas as pd
import akshare as ak

# 确保 backend 路径在 sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logging_config import logger

# 禁用代理
os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''

def _fetch_snapshot_sina():
    print("Fetching Sina snapshot...")
    df = ak.stock_zh_a_spot()
    if df is None or df.empty:
        raise ValueError("Sina returned empty snapshot")
    
    print(f"Sina raw columns: {df.columns.tolist()}")
    print("Formatting Sina snapshot...")
    
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
    df['code'] = df['code'].str.extract(r'(\d{6})')
    
    # 转换成交量单位（新浪是股，东财是手，除以 100）
    df['vol'] = df['vol'] / 100.0
    
    # 补全东财快照特有的字段
    df['turnover'] = None
    df['mkt_cap'] = None
    df['pe'] = None
    
    # 保留标准列
    cols = ['code', 'name', 'price', 'open', 'pct_chg', 'vol', 'turnover', 'mkt_cap', 'pe']
    df = df[cols]
    
    return df

def test_sina_format():
    try:
        df = _fetch_snapshot_sina()
        print("\n--- Sina Formatted Snapshot Success ---")
        print(f"Total stocks: {len(df)}")
        print(f"Formatted columns: {df.columns.tolist()}")
        print("Sample data:")
        print(df.head(5))
        
        # 简单校验
        assert 'code' in df.columns, "Missing 'code' column"
        assert 'price' in df.columns, "Missing 'price' column"
        assert len(df.iloc[0]['code']) == 6, f"Invalid code format: {df.iloc[0]['code']}"
        print("\nFormat verification PASSED!")
    except Exception as e:
        print(f"\nFormat verification FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_sina_format()
