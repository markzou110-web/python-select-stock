
import sys
import os
import math

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from backend.api import sanitize_float, sanitize_recursive

def verify_json_fix():
    print("Step 1: Testing sanitize_float...")
    test_cases = [
        (float('nan'), 0.0),
        (float('inf'), 0.0),
        (float('-inf'), 0.0),
        (12.34, 12.34),
        (0, 0)
    ]
    
    for val, expected in test_cases:
        res = sanitize_float(val)
        if res == expected or (math.isnan(val) and res == 0.0):
            print(f"✅ Input: {val} -> Output: {res}")
        else:
            print(f"❌ Input: {val} -> Output: {res} (Expected: {expected})")

    print("\nStep 2: Testing sanitize_recursive...")
    complex_data = {
        "score": 88.5,
        "metrics": {
            "pe": float('nan'),
            "turnover": float('inf')
        },
        "history": [10.0, float('-inf'), 20.0]
    }
    
    sanitized = sanitize_recursive(complex_data)
    print(f"Sanitized: {sanitized}")
    
    # Check values
    assert sanitized['metrics']['pe'] == 0.0
    assert sanitized['metrics']['turnover'] == 0.0
    assert sanitized['history'][1] == 0.0
    print("✅ Recursive sanitization works.")

if __name__ == "__main__":
    verify_json_fix()
