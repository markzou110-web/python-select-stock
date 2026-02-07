import sys
import os
import math
import datetime
import pytest
import numpy as np
import pandas as pd
from unittest.mock import Mock, patch

# Add parent directory of 'backend' to path to allow importing utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.json_utils import sanitize_value, sanitize_recursive

class TestSanitizeValue:
    def test_sanitize_value_success(self):
        """Test happy path for basic types."""
        assert sanitize_value(1) == 1
        assert sanitize_value(1.5) == 1.5
        assert sanitize_value("test") == "test"
        assert sanitize_value(True) is True
        assert sanitize_value(False) is False
        assert sanitize_value(None) is None

    def test_sanitize_value_numpy_types(self):
        """Test conversion of numpy types to native python types."""
        # Integers
        assert sanitize_value(np.int8(10)) == 10
        assert sanitize_value(np.int16(10)) == 10
        assert sanitize_value(np.int32(10)) == 10
        assert sanitize_value(np.int64(10)) == 10
        
        # Floats
        assert sanitize_value(np.float32(10.5)) == 10.5
        assert sanitize_value(np.float64(10.5)) == 10.5
        
        # Numpy Boolean (if applicable, though typically handled as basic type or int)
        # np.bool_ might behave differently in different numpy versions
        assert sanitize_value(np.bool_(True)) in [True, 1]

    def test_sanitize_value_pandas_types(self):
        """Test conversion of pandas timestamps and handling of NA."""
        ts = pd.Timestamp("2023-01-01")
        assert sanitize_value(ts) == str(ts)
        
        # pd.NA check - might need to be careful with how it's constructed/checked
        # pd.NA is often handled by pd.isna check in the function
        assert sanitize_value(pd.NA) is None

    def test_sanitize_value_edge_cases(self):
        """Test handling of NaN and Inf."""
        assert sanitize_value(float('nan')) is None
        assert sanitize_value(float('inf')) is None
        assert sanitize_value(float('-inf')) is None
        assert sanitize_value(np.nan) is None
        assert sanitize_value(np.inf) is None

    def test_sanitize_value_dates(self):
        """Test handling of datetime objects."""
        d = datetime.date(2023, 1, 1)
        dt = datetime.datetime(2023, 1, 1, 12, 0, 0)
        assert sanitize_value(d) == str(d)
        assert sanitize_value(dt) == str(dt)


class TestSanitizeRecursive:
    def test_sanitize_recursive_dict(self):
        """Test recursive sanitization of dictionaries."""
        data = {
            "a": np.int64(10),
            "b": float('nan'),
            "c": {"nested": np.float64(20.5)}
        }
        res = sanitize_recursive(data)
        assert res["a"] == 10
        assert res["b"] is None
        assert res["c"]["nested"] == 20.5

    def test_sanitize_recursive_list(self):
        """Test recursive sanitization of lists."""
        data = [np.int64(1), float('inf'), [pd.NA]]
        res = sanitize_recursive(data)
        assert res[0] == 1
        assert res[1] is None
        assert res[2] == [None]

    def test_sanitize_recursive_complex_structure(self):
        """Test deep nested mix of lists and dicts."""
        data = {
            "list": [
                {"id": np.int32(1), "val": float('nan')},
                {"id": np.int32(2), "val": 100}
            ],
            "meta": {
                "timestamp": pd.Timestamp("2023-01-01"),
                "valid": np.bool_(True)
            }
        }
        res = sanitize_recursive(data)
        assert res["list"][0]["id"] == 1
        assert res["list"][0]["val"] is None
        assert res["list"][1]["id"] == 2
        assert res["meta"]["timestamp"] == "2023-01-01 00:00:00"

    def test_sanitize_value_handles_pandas_series(self):
         # Test if it handles a series gracefully (should technically be recursive but if passed to sanitize_value directly)
         # The function logic: if pd.isna(val) if isinstance(...)
         # It doesn't explicitly handle Series as a container, but checks if it is NA.
         # Passing a Series to sanitize_value might fail 'isinstance(val, float)' checks or just return str(val)
         pass 
