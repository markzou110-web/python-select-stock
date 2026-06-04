import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routers.stock import _generate_ai_suggestion


def test_ai_suggestion_uses_live_current_price_for_open_position():
    df = pd.DataFrame({
        "收盘": [30.0] * 25,
        "RSI": [51.7] * 25,
        "MACD_HIST": [0.2] * 24 + [0.1],
        "EMA5": [31.0] * 25,
        "EMA20": [30.0] * 25,
        "EMA60": [29.0] * 25,
    })
    stock_info = {
        "is_paper_trade": True,
        "buy_price": 17.53,
        "current_price": 16.70,
        "stop_price": 16.48,
        "take_profit_price": 19.63,
    }

    suggestion = _generate_ai_suggestion(df, stock_info, {"market_regime": "OFFENSIVE"})

    assert suggestion["action"] == "CLOSE"
    assert any("当前盈亏：-4.73%" in reason for reason in suggestion["reasoning"])
    assert any("距止损 1.3%" in reason for reason in suggestion["reasoning"])
