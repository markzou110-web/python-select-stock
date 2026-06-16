import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routers.stock import _json_safe_response


def test_full_analysis_response_replaces_non_finite_indicator_values():
    payload = {
        "kline": [
            {"RSI": float("nan"), "MACD": float("inf")},
            {"RSI": np.float64(52.5), "MACD": -float("inf")},
        ],
        "risk_reward": np.float64(1.8),
    }

    result = _json_safe_response(payload)

    assert result["kline"][0] == {"RSI": None, "MACD": None}
    assert result["kline"][1] == {"RSI": 52.5, "MACD": None}
    assert result["risk_reward"] == 1.8
    assert all(
        not isinstance(value, float) or math.isfinite(value)
        for row in result["kline"]
        for value in row.values()
        if value is not None
    )
