"""
Unit tests for the backtest simulation engine in core/strategy.py.
"""

import pytest
import numpy as np
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.strategy import _simulate_backtest


class TestSimulateBacktestBasic:
    """Test basic backtest engine functionality."""

    def test_empty_signals_returns_zero(self):
        """No signals should return all-zero stats."""
        close = np.array([10.0] * 20)
        high = np.array([10.5] * 20)
        low = np.array([9.5] * 20)
        result = _simulate_backtest(close, high, low, signal_indices=[])
        assert result["signal_count"] == 0
        assert result["win_rate"] == 0
        assert result["vol_skipped"] == 0
        assert result["time_stopped"] == 0

    def test_basic_profitable_trade(self):
        """A clear uptrend should produce positive returns."""
        n = 50
        prices = np.linspace(10, 15, n)  # steady uptrend
        high = prices * 1.01
        low = prices * 0.99
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[5], max_hold_days=10
        )
        assert result["signal_count"] == 1
        assert result["avg_return"] > 0

    def test_stop_loss_triggered(self):
        """A sharp drop should trigger stop loss."""
        n = 30
        prices = np.array([10.0] * 10 + [8.0] * 20)  # drop at day 10
        high = prices + 0.1
        low = prices - 0.1
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[5], stop_loss_pct=-8.0, max_hold_days=15
        )
        assert result["stop_loss_hits"] >= 1

    def test_trailing_stop_triggers(self):
        """Price rises then falls back - trailing stop should trigger."""
        n = 30
        prices = np.concatenate([
            np.linspace(10, 13, 10),   # rise
            np.linspace(13, 10, 20),   # fall back
        ])
        high = prices + 0.2
        low = prices - 0.2
        atr = np.full(n, 0.3)
        
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[3], atr_vals=atr,
            use_trailing_stop=True, trailing_multiplier=2.0,
            max_hold_days=20
        )
        # Should have exited before max_hold_days
        assert result["avg_hold_days"] < 20

    def test_max_hold_days_exit(self):
        """Flat price should exit at max_hold_days."""
        n = 30
        prices = np.array([10.0] * n)
        high = prices + 0.05
        low = prices - 0.05
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[5], max_hold_days=5,
            use_trailing_stop=False
        )
        assert result["avg_hold_days"] == 5


class TestDynamicCommission:
    """Test A-share realistic tax/commission model."""

    def test_friction_reduces_returns(self):
        """Commission + stamp tax should reduce raw returns."""
        n = 30
        prices = np.linspace(10, 10.5, n)  # mild uptrend
        high = prices + 0.1
        low = prices - 0.1
        
        # Without vol constraint
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[3], max_hold_days=5, use_trailing_stop=False
        )
        # Return should be positive but slightly reduced by friction
        assert result["signal_count"] == 1
        # The friction should be small but nonzero
        raw_return = (prices[8] - prices[3]) / prices[3] * 100
        assert result["avg_return"] < raw_return  # friction eaten some profit


class TestVolumeConstraint:
    """Test volume constraint (vol_cap_pct) logic."""

    def test_volume_skipped_on_thin_liquidity(self):
        """Extremely low volume should cause skip."""
        n = 30
        prices = np.array([100.0] * n)
        high = prices + 1
        low = prices - 1
        vol = np.array([10.0] * n)  # very low volume → turnover ~1000
        
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[5, 10, 15],
            vol_vals=vol, vol_cap_pct=0.05,
            max_hold_days=5, use_trailing_stop=False
        )
        assert result["vol_skipped"] >= 1

    def test_no_skip_on_normal_volume(self):
        """Normal volume should not cause skip."""
        n = 30
        prices = np.array([10.0] * n)
        high = prices + 0.5
        low = prices - 0.5
        vol = np.array([1000000.0] * n)  # plenty of volume
        
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[5],
            vol_vals=vol, vol_cap_pct=0.05,
            max_hold_days=5, use_trailing_stop=False
        )
        assert result["vol_skipped"] == 0
        assert result["signal_count"] == 1


class TestTimeStop:
    """Test time-based stop loss."""

    def test_time_stop_triggers_on_flat(self):
        """Flat price for N days should trigger time stop."""
        n = 30
        prices = np.array([10.0] * n)
        high = prices + 0.01
        low = prices - 0.01
        
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[3],
            time_stop_days=5, max_hold_days=15,
            use_trailing_stop=False
        )
        assert result["time_stopped"] >= 1
        assert result["avg_hold_days"] == 5

    def test_time_stop_does_not_trigger_on_profit(self):
        """Profitable position should NOT be time-stopped."""
        n = 30
        prices = np.linspace(10, 12, n)  # uptrend
        high = prices + 0.1
        low = prices - 0.1
        
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[3],
            time_stop_days=5, max_hold_days=15,
            use_trailing_stop=False
        )
        assert result["time_stopped"] == 0
