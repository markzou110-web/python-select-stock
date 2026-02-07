import math
import datetime
import numpy as np
import pandas as pd

def sanitize_value(val):
    """
    Sanitizes values to be JSON compliant.
    Converts NaN, Inf to None.
    Converts numpy/pandas numeric types to native Python types.
    Preserves types like strings, booleans, and integers where possible.
    """
    if val is None:
        return None
    
    # Handle pandas <NA> and numpy NaN
    # Handle pandas <NA> and numpy NaN
    # Check explicitly for pd.NA which is not caught by checks below easily
    if val is pd.NA:
        return None
        
    try:
        if pd.isna(val):
             # Ensure we don't return True for things that aren't NA but pd.isna thinks are (unlikely for scalar, but good to be safe)
             # Also prevent returning True for list/dict if they somehow got here (sanitize_recursive handles them)
             if not isinstance(val, (list, dict)):
                 return None
    except:
        pass
        
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    
    # Convert numpy types to native types
    if isinstance(val, (np.integer, np.int64, np.int32)):
        return int(val)
    if isinstance(val, (np.floating, np.float64, np.float32)):
        res = float(val)
        return None if math.isnan(res) or math.isinf(res) else res
    if isinstance(val, np.bool_):
        return bool(val)
    
    if isinstance(val, (pd.Timestamp, datetime.date, datetime.datetime)):
        return str(val)
    
    # Pass through basic types
    if isinstance(val, (str, bool, int)):
        return val
        
    # If it's something else, try to check if it's numeric float
    try:
        if isinstance(val, (float, int)):
            return val
    except:
        pass
        
    return str(val)

def sanitize_recursive(data):
    """
    Recursively sanitizes a dictionary or list for JSON compliance.
    """
    if isinstance(data, dict):
        return {str(k): sanitize_recursive(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_recursive(v) for v in data]
    return sanitize_value(data)
