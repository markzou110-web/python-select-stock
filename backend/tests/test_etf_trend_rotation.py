import json
import os
import sys
from argparse import Namespace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.etf_trend_rotation import RotationConfig, build_rotation_decisions, run_etf_rotation
from scripts import backtest_etf_trend_rotation


def _frame(start: str, periods: int, daily_return: float, start_price: float = 100.0) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=periods)
    close = start_price * np.power(1 + daily_return, np.arange(periods))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close * 1.001,
            "close": close,
        }
    )


def _config(**overrides) -> RotationConfig:
    values = {
        "risk_assets": ("RISK_A", "RISK_B", "RISK_C"),
        "defensive_asset": "BOND",
        "trend_days": 40,
        "momentum_short_days": 20,
        "momentum_long_days": 40,
        "volatility_days": 20,
        "top_n": 2,
        "initial_capital": 1_000_000.0,
    }
    values.update(overrides)
    return RotationConfig(**values)


def _volatile_frame(start: str, periods: int) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=periods)
    returns = np.array([0.031 if index % 2 == 0 else -0.029 for index in range(periods)])
    close = 100 * np.cumprod(1 + returns)
    return pd.DataFrame({"date": dates, "open": close, "close": close})


def test_rotation_decisions_are_point_in_time_and_execute_next_session():
    prices = {
        "RISK_A": _frame("2024-01-02", 180, 0.0020),
        "RISK_B": _frame("2024-01-02", 180, 0.0010),
        "RISK_C": _frame("2024-01-02", 180, -0.0010),
        "BOND": _frame("2024-01-02", 180, 0.0001),
    }
    config = _config()

    full = build_rotation_decisions(prices, config)
    cutoff = full[-3].signal_date
    truncated = {
        code: frame[frame["date"] <= cutoff].copy()
        for code, frame in prices.items()
    }
    historical = [item for item in full if item.signal_date < cutoff]

    assert build_rotation_decisions(truncated, config) == historical
    assert all(item.execution_date > item.signal_date for item in full)


def test_rotation_uses_defensive_asset_when_no_risk_asset_has_absolute_trend():
    prices = {
        "RISK_A": _frame("2024-01-02", 150, -0.0010),
        "RISK_B": _frame("2024-01-02", 150, -0.0005),
        "RISK_C": _frame("2024-01-02", 150, -0.0020),
        "BOND": _frame("2024-01-02", 150, 0.0001),
    }

    decisions = build_rotation_decisions(prices, _config())

    assert decisions
    assert all(item.target_weights == {"BOND": 1.0} for item in decisions)


def test_rotation_selects_only_the_two_strongest_eligible_assets():
    prices = {
        "RISK_A": _frame("2024-01-02", 150, 0.0020),
        "RISK_B": _frame("2024-01-02", 150, 0.0015),
        "RISK_C": _frame("2024-01-02", 150, 0.0005),
        "BOND": _frame("2024-01-02", 150, 0.0001),
    }

    decisions = build_rotation_decisions(prices, _config())

    assert decisions
    assert set(decisions[-1].target_weights) == {"RISK_A", "RISK_B"}
    assert decisions[-1].target_weights == {"RISK_A": 0.5, "RISK_B": 0.5}


def test_rotation_scales_risk_assets_to_the_target_portfolio_volatility():
    prices = {
        "RISK_A": _volatile_frame("2024-01-02", 180),
        "RISK_B": _volatile_frame("2024-01-02", 180),
        "RISK_C": _frame("2024-01-02", 180, -0.0010),
        "BOND": _frame("2024-01-02", 180, 0.0001),
    }

    decisions = build_rotation_decisions(prices, _config(target_volatility=0.10))

    assert decisions
    assert decisions[-1].target_weights["BOND"] > 0
    assert sum(
        weight
        for code, weight in decisions[-1].target_weights.items()
        if code.startswith("RISK")
    ) < 1


def test_short_momentum_window_can_be_the_longest_required_history():
    prices = {
        "RISK_A": _frame("2024-01-02", 180, 0.0020),
        "RISK_B": _frame("2024-01-02", 180, 0.0010),
        "RISK_C": _frame("2024-01-02", 180, -0.0010),
        "BOND": _frame("2024-01-02", 180, 0.0001),
    }

    decisions = build_rotation_decisions(
        prices,
        _config(momentum_short_days=100, momentum_long_days=40),
    )

    assert decisions


def test_transaction_costs_reduce_final_equity_and_are_reported():
    prices = {
        "RISK_A": _frame("2024-01-02", 180, 0.0020),
        "RISK_B": _frame("2024-01-02", 180, 0.0015),
        "RISK_C": _frame("2024-01-02", 180, -0.0010),
        "BOND": _frame("2024-01-02", 180, 0.0001),
    }
    free = run_etf_rotation(
        prices,
        _config(commission_rate=0.0, minimum_commission=0.0, slippage_bps=0.0),
    )
    realistic = run_etf_rotation(prices, _config())

    assert realistic["metrics"]["final_equity"] < free["metrics"]["final_equity"]
    assert realistic["metrics"]["transaction_cost"] > 0
    assert realistic["trades"]
    assert all(trade["execution_date"] > trade["signal_date"] for trade in realistic["trades"])
    assert all(row["cash"] >= 0 for row in realistic["equity_curve"])


def test_report_returns_insufficient_data_for_a_short_date_range(monkeypatch, tmp_path):
    short_history = _frame("2026-01-02", 20, 0.001)
    monkeypatch.setattr(
        backtest_etf_trend_rotation,
        "fetch_etf_prices",
        lambda *args, **kwargs: short_history.copy(),
    )
    args = Namespace(
        start_date="2026-01-01",
        end_date="2026-02-01",
        initial_capital=1_000_000.0,
        cache_dir=str(tmp_path / "cache"),
        output=str(tmp_path / "report.json"),
    )

    report = backtest_etf_trend_rotation.run(args)

    assert report["strategy"]["metrics"]["status"] == "INSUFFICIENT_DATA"
    assert Path(args.output).exists()
    saved = json.loads(Path(args.output).read_text(encoding="utf-8"))
    assert saved["strategy"]["metrics"]["status"] == "INSUFFICIENT_DATA"


def test_corporate_action_factor_preserves_position_value_across_unit_split():
    risk = _frame("2024-01-02", 100, 0.001)
    split_day = 60
    risk["raw_open"] = risk["open"]
    risk["raw_close"] = risk["close"]
    risk["adjustment_factor"] = 1.0
    risk.loc[split_day:, ["raw_open", "raw_close"]] /= 2
    risk.loc[split_day:, "adjustment_factor"] = 2.0
    prices = {
        "RISK_A": risk,
        "BOND": _frame("2024-01-02", 100, 0.0001),
    }
    config = RotationConfig(
        risk_assets=("RISK_A",),
        defensive_asset="BOND",
        trend_days=10,
        momentum_short_days=5,
        momentum_long_days=10,
        volatility_days=5,
        top_n=1,
        target_volatility=0.0,
        commission_rate=0.0,
        minimum_commission=0.0,
        slippage_bps=0.0,
    )

    result = run_etf_rotation(prices, config)

    assert result["metrics"]["max_drawdown_pct"] > -1
