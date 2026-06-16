import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import batch_experiment, source_comparison
from core.indicators import batch_calculate_indicators, calculate_indicators
from core.strategy_registry import get_strategy, list_strategies, supported_backtest_strategies


def _price_frame(rows=90):
    close = np.linspace(10, 15, rows) + np.sin(np.arange(rows) / 4)
    return pd.DataFrame({
        "日期": [date(2026, 1, 1) + timedelta(days=i) for i in range(rows)],
        "开盘": close - 0.1,
        "最高": close + 0.3,
        "最低": close - 0.3,
        "收盘": close,
        "成交量": np.linspace(1000, 2000, rows),
    })


def test_strategy_registry_has_structured_backtest_contract():
    strategies = list_strategies()
    assert {"squeeze", "pine", "consensus", "tv_zp"} <= supported_backtest_strategies()
    assert get_strategy("squeeze")["required_indicators"]
    assert all("default_params" in item and "explain_fields" in item for item in strategies)


def test_batch_experiment_aggregates_multiple_codes(monkeypatch):
    monkeypatch.setattr(batch_experiment, "load_from_db", lambda code, start, engine: _price_frame())
    monkeypatch.setattr(batch_experiment, "calculate_indicators", lambda df, enable_pine_indicators=False: df)
    monkeypatch.setattr(batch_experiment, "run_single_stock_backtest", lambda df, strategy_type, params: {
        "summary": {
            "signal_count": 2,
            "win_rate": 50,
            "avg_return": 1,
            "total_return": 2,
            "max_drawdown": -1,
            "profit_factor": 1.5,
            "skipped_adjustment_gap": 0,
        }
    })
    result = batch_experiment.run_batch_experiment(object(), ["000001", "600519"], "squeeze", {})
    assert result["summary"]["tested"] == 2
    assert result["summary"]["effective"] == 2
    assert result["summary"]["avg_total_return"] == 2


def test_single_and_batch_indicators_are_consistent_for_shared_fields():
    first = _price_frame()
    second = _price_frame()
    second["收盘"] += 3
    single = calculate_indicators(first.copy(), bench_df=pd.DataFrame())
    combined = pd.concat([first.assign(code="000001"), second.assign(code="600519")], ignore_index=True)
    batch = batch_calculate_indicators(combined)
    first_batch = batch[batch["code"] == "000001"].reset_index(drop=True)
    for column in ["EMA5", "EMA20", "RSI", "MACD_DIF", "MACD_DEA", "BB_Mid", "BB_Width", "Vol_MA20"]:
        assert np.allclose(single[column].fillna(-999), first_batch[column].fillna(-999), atol=1e-8)


def test_source_comparison_flags_close_spread(monkeypatch):
    class FakeSource:
        def __init__(self, close):
            self.close = close

        def get_hist_data(self, code, start_date):
            return pd.DataFrame([{"日期": "2026-06-12", "收盘": self.close, "成交量": 1000}])

    monkeypatch.setattr(source_comparison, "TencentDataSource", lambda: FakeSource(10))
    monkeypatch.setattr(source_comparison, "SinaDataSource", lambda: FakeSource(10.2))
    monkeypatch.setattr(source_comparison, "EastMoneyDataSource", lambda: FakeSource(10))
    result = source_comparison.compare_history_sources(["000001"])
    assert result["items"][0]["status"] == "WARN"
    assert result["items"][0]["close_spread_pct"] == 2.0
