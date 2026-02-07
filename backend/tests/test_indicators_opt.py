import pytest
import pandas as pd
import numpy as np
import sys
import os

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from core.indicators import calculate_indicators

def test_kdj_vectorized():
    # Create sample data
    data = {
        '收盘': [10.0, 10.5, 11.0, 10.8, 10.2, 10.0, 9.8, 10.1, 10.5, 11.0],
        '最高': [10.2, 10.6, 11.2, 11.0, 10.5, 10.2, 10.0, 10.3, 10.8, 11.2],
        '最低': [9.8, 10.0, 10.5, 10.5, 10.0, 9.8, 9.5, 9.8, 10.2, 10.5],
        '成交量': [100, 150, 200, 120, 100, 80, 50, 100, 150, 200],
        '开盘': [9.9, 10.0, 10.5, 11.0, 10.5, 10.0, 9.6, 9.8, 10.2, 10.8]
    }
    df = pd.DataFrame(data)
    
    # Run calculation
    df_res = calculate_indicators(df)
    
    # Assert KDJ columns exist
    assert 'KDJ_K' in df_res.columns
    assert 'KDJ_D' in df_res.columns
    assert 'KDJ_J' in df_res.columns
    
    # Check for NaN (initially there will be NaNs due to rolling window, but verify tail)
    assert not df_res['KDJ_K'].iloc[-1:].isna().any()
    
    # Basic value check - J should be 3K - 2D
    last_row = df_res.iloc[-1]
    expected_j = 3 * last_row['KDJ_K'] - 2 * last_row['KDJ_D']
    assert np.isclose(last_row['KDJ_J'], expected_j)

def test_kdj_division_by_zero():
    # Create flat data where high == low
    data = {
        '收盘': [10.0] * 120,
        '最高': [10.0] * 120,
        '最低': [10.0] * 120,
        '成交量': [100] * 120,
        '开盘': [10.0] * 120
    }
    df = pd.DataFrame(data)
    
    df_res = calculate_indicators(df)
    
    # Should not crash and RSV should be 50 (middle)
    # Note: EWM might smooth this, but RSV input should be 50
    assert not df_res['KDJ_K'].isna().all()
    # Check if we handled the division by zero gracefully
    # Should not crash and RSV should be 50 (middle) after warmup
    assert not df_res['KDJ_RSV'].iloc[9:].isna().any()

def test_obv_vectorized():
    data = {
        '收盘': [10, 11, 11, 10, 12],
        '成交量': [100, 200, 150, 100, 300],
        '最高': [12]*5, '最低': [8]*5, '开盘': [10]*5 # Dummy for other indicators
    }
    df = pd.DataFrame(data)
    
    df_res = calculate_indicators(df)
    
    # Expected OBV:
    # 0: First row, change NaN -> 0 -> OBV=0 (as code uses cumsum on diff)
    # 1: 11>10 (+1) * 200 = 200. Cumsum = 200 (if start 0) 
    # Actually code logic:
    # OBV_Change: [NaN, 1, 0, -1, 2]
    # OBV_Direction: [0, 1, 0, -1, 1] (NaN fillna 0)
    # Vol flow: [0, 200, 0, -100, 300]
    # Cumsum: [0, 200, 200, 100, 400]
    
    expected_obv = [0, 200, 200, 100, 400]
    np.testing.assert_array_equal(df_res['OBV'].values, expected_obv)

def test_empty_df():
    df = pd.DataFrame()
    res = calculate_indicators(df)
    assert res.empty
