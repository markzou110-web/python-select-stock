import sys
import os
import pytest
from unittest.mock import Mock, patch, MagicMock
import pandas as pd
from datetime import datetime, timedelta

# Add parent directory of 'backend' to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.scan_service import single_stock_task

class TestScanService:
    @patch('services.scan_service.load_from_db')
    @patch('services.scan_service.calculate_indicators')
    @patch('services.scan_service.check_strategy')
    def test_single_stock_task_success(self, mock_check, mock_calc, mock_load):
        # Setup mocks
        # Need at least 120 rows for history check
        mock_df = pd.DataFrame({
            '日期': [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(150)],
            '收盘': [10.0] * 150
        })
        mock_load.return_value = mock_df
        mock_calc.return_value = mock_df
        mock_check.return_value = (True, {'Score': 85.0, 'reason': 'Matching'})
        
        # Run task
        # code, name, price, vol, open_price, threshold, vol_multiplier, rsi_min, ...
        result = single_stock_task(
            '600000', '浦发银行', 10.0, 100000, 9.9, 
            0.12, 1.5, 55, True, False, 10, True, 
            local_only=True
        )
        
        # Verify
        assert result['Score'] == 85.0
        assert result['代码'] == '600000'
        assert result['名称'] == '浦发银行'

    @patch('services.scan_service.load_from_db')
    def test_single_stock_task_data_missing(self, mock_load):
        # Setup mock for empty data
        mock_load.return_value = pd.DataFrame()
        
        # Run task
        result = single_stock_task(
            '600000', '浦发银行', 10.0, 100000, 9.9, 
            0.12, 1.5, 55, True, False, 10, True, 
            local_only=True
        )
        
        # Verify
        assert 'reason' in result
        assert '数据缺失' in result['reason']
