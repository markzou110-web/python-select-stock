
import sys
import os
import pandas as pd
import numpy as np
import math

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from api import sanitize_float, sanitize_recursive

def test_new_sanitization():
    print("Testing new sanitization logic...")
    
    test_cases = [
        ("None", None, 0.0),
        ("NaN", float('nan'), 0.0),
        ("Inf", float('inf'), 0.0),
        ("Integer", 10, 10),
        ("Float", 10.5, 10.5),
        ("String Number", "12.34", 12.34),
        ("String Text", "hello", "hello"),
        ("Numpy NaN", np.nan, 0.0),
        ("Pandas NA", pd.NA, 0.0),
    ]
    
    for name, val, expected in test_cases:
        result = sanitize_float(val)
        print(f"  {name}: {val} -> {result} (Expected: {expected})")
        if name != "Pandas NA": # Generic comparison
             assert result == expected or (math.isnan(expected) and math.isnan(result))
    
    print("\nTesting recursive sanitization...")
    complex_data = {
        "a": [1, 2, float('nan'), {"b": float('inf')}],
        "c": None,
        "d": "test"
    }
    sanitized = sanitize_recursive(complex_data)
    print(f"  Input: {complex_data}")
    print(f"  Output: {sanitized}")
    
    assert sanitized["a"][2] == 0.0
    assert sanitized["a"][3]["b"] == 0.0
    assert sanitized["c"] == 0.0
    
    print("\nAll sanitization tests passed!")

if __name__ == "__main__":
    test_new_sanitization()
