import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import scanner


def test_single_stock_task_backfills_missing_pine_indicators(monkeypatch):
    n = 130
    close = np.linspace(10, 13, n)
    df = pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="D"),
        "开盘": close - 0.3,
        "收盘": close,
        "最高": close + 0.1,
        "最低": close - 0.1,
        "成交量": np.full(n, 200000.0),
        "Vol_MA20": np.full(n, 100000.0),
        "RSI": np.full(n, 60.0),
        "MACD_DIF": np.full(n, 0.2),
        "MACD_DEA": np.full(n, 0.1),
        "BB_Width": np.full(n, 0.08),
        "Sqz_Ratio": np.full(n, 0.08),
        "EMA5": close - 0.2,
        "EMA10": close - 0.3,
        "EMA20": close - 0.4,
        "EMA60": close - 0.5,
    })

    def fake_calculate_pine_indicators(input_df):
        enriched = input_df.copy()
        enriched["RF_Upward"] = True
        enriched["RF_Downward"] = False
        enriched["ST_Signal"] = True
        enriched["RQK_Up"] = True
        enriched["HalfTrend_Up"] = True
        enriched["QQE_Long"] = True
        return enriched

    def fake_check_pine_strategy(input_df, min_signals=3, fund_data=None):
        assert {"RF_Upward", "RQK_Up", "HalfTrend_Up", "QQE_Long"}.issubset(input_df.columns)
        return True, {"Score": 88, "信号数": "5/5"}

    monkeypatch.setattr(scanner, "calculate_pine_indicators", fake_calculate_pine_indicators)
    monkeypatch.setattr(scanner, "check_pine_strategy", fake_check_pine_strategy)

    result = scanner.single_stock_task(
        "000001",
        "测试股票",
        price=13,
        vol=200000,
        open_price=12.7,
        threshold=0.12,
        vol_multiplier=1.5,
        rsi_min=55,
        use_macd_filter=True,
        use_bb_sqz=True,
        sqz_lookback=10,
        use_weekly=False,
        preloaded_df=df,
        strategy_type="pine",
        pine_min_signals=5,
    )

    assert result["Score"] == 88
    assert result["strategy_type"] == "pine"
