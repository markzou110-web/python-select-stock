
import sys
import os
import pandas as pd
import numpy as np
import math

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from core.indicators import calculate_indicators

def test_indicators_with_nan():
    data = {
        '日期': ['2024-01-01', '2024-01-02'],
        '开盘': [10.0, 11.0],
        '最高': [12.0, 13.0],
        '最低': [8.0, 9.0],
        '收盘': [11.0, 12.0],
        '成交量': [1000, 2000]
    }
    df = pd.DataFrame(data)
    
    # Simulate some empty/NaN data
    df.loc[0, '开盘'] = None
    
    print("Testing calculate_indicators with missing data...")
    try:
        df = calculate_indicators(df)
        print("Success!")
        print(df)
    except Exception as e:
        print(f"Failed: {e}")

def test_detail_logic_with_nan():
    data = {
        '日期': ['2024-01-01'],
        '开盘': [np.nan],
        '最高': [12.0],
        '最低': [8.0],
        '收盘': [11.0],
        '成交量': [1000]
    }
    df = pd.DataFrame(data)
    
    print("\nTesting detail records building with NaN...")
    records = []
    try:
        for _, row in df.iterrows():
            records.append({
                "time": row['日期'],
                "open": float(row['开盘']),
                "high": float(row['最高']),
            })
        print(f"Records: {records}")
    except Exception as e:
        print(f"Failed: {e}")

def test_math_isnan_with_none():
    print("\nTesting math.isnan(None)...")
    try:
        math.isnan(None)
        print("Success!")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    test_indicators_with_nan()
    test_detail_logic_with_nan()
    test_math_isnan_with_none()
