"""
Pytest fixtures and configuration for Alpha Vision backend tests.
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta


@pytest.fixture
def sample_ohlcv_df():
    """
    Standard 200-day OHLCV DataFrame for strategy tests.
    Simulates a typical A-share stock trending upward with some volatility.
    """
    np.random.seed(42)
    n = 200
    dates = pd.date_range(end=datetime.now(), periods=n, freq='B')
    
    # Generate a trending price series with noise
    base_price = 10.0
    returns = np.random.normal(0.001, 0.02, n)
    prices = base_price * np.cumprod(1 + returns)
    
    # Generate OHLCV from close prices
    opens = prices * (1 + np.random.uniform(-0.01, 0.01, n))
    highs = np.maximum(prices, opens) * (1 + np.random.uniform(0, 0.025, n))
    lows = np.minimum(prices, opens) * (1 - np.random.uniform(0, 0.025, n))
    volumes = np.random.randint(50000, 500000, n).astype(float)
    
    df = pd.DataFrame({
        '日期': dates.strftime('%Y-%m-%d'),
        '开盘': np.round(opens, 2),
        '最高': np.round(highs, 2),
        '最低': np.round(lows, 2),
        '收盘': np.round(prices, 2),
        '成交量': volumes,
    })
    return df


@pytest.fixture
def sample_signal_indices():
    """Standard signal indices for backtest tests."""
    return [30, 60, 90, 120, 150]


@pytest.fixture
def sample_trades():
    """Sample trade list for analytics tests."""
    return [
        {"code": "000001", "name": "平安银行", "entry_price": 10.0, "close_price": 10.5, 
         "entry_date": "2025-01-10", "close_date": "2025-01-15", "pl_pct": 5.0,
         "status": "CLOSED", "industry": "银行", "strategy_type": "squeeze"},
        {"code": "000002", "name": "万科A", "entry_price": 15.0, "close_price": 14.2,
         "entry_date": "2025-01-20", "close_date": "2025-01-25", "pl_pct": -5.33,
         "status": "CLOSED", "industry": "房地产", "strategy_type": "pine"},
        {"code": "600519", "name": "贵州茅台", "entry_price": 1700.0, "close_price": 1800.0,
         "entry_date": "2025-02-01", "close_date": "2025-02-10", "pl_pct": 5.88,
         "status": "CLOSED", "industry": "白酒", "strategy_type": "squeeze"},
        {"code": "300750", "name": "宁德时代", "entry_price": 200.0, "close_price": 190.0,
         "entry_date": "2025-02-15", "close_date": "2025-02-20", "pl_pct": -5.0,
         "status": "CLOSED", "industry": "新能源", "strategy_type": "pine"},
        {"code": "601318", "name": "中国平安", "entry_price": 45.0, "close_price": 47.0,
         "entry_date": "2025-03-01", "close_date": "2025-03-05", "pl_pct": 4.44,
         "status": "CLOSED", "industry": "保险", "strategy_type": "squeeze"},
        {"code": "000858", "name": "五粮液", "entry_price": 150.0, "close_price": 148.0,
         "entry_date": "2025-03-10", "close_date": "2025-03-15", "pl_pct": -1.33,
         "status": "CLOSED", "industry": "白酒", "strategy_type": "consensus"},
    ]
