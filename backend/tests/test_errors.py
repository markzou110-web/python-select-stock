"""
Unit tests for the error handling system and analytics module.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.errors import (
    AlphaVisionError, DatabaseError, ScanError,
    DataSourceError, ValidationError, error_response, success_response
)
from core.analytics import calculate_risk_metrics, calculate_pnl_attribution


class TestErrorHierarchy:
    """Test custom exception class hierarchy."""

    def test_base_error(self):
        err = AlphaVisionError("test error", code="TEST")
        assert str(err) == "test error"
        assert err.code == "TEST"
        assert err.message == "test error"

    def test_database_error(self):
        err = DatabaseError("connection failed")
        assert err.code == "DB_ERROR"
        assert isinstance(err, AlphaVisionError)

    def test_scan_error(self):
        err = ScanError("data insufficient")
        assert err.code == "SCAN_ERROR"
        assert isinstance(err, AlphaVisionError)

    def test_data_source_error(self):
        err = DataSourceError()
        assert err.code == "DATA_SOURCE_ERROR"
        assert "数据源" in err.message

    def test_validation_error(self):
        err = ValidationError("bad stock code")
        assert err.code == "VALIDATION_ERROR"

    def test_error_repr(self):
        err = DatabaseError("test")
        assert "DatabaseError" in repr(err)
        assert "DB_ERROR" in repr(err)


class TestResponseHelpers:
    """Test standard response format helpers."""

    def test_error_response(self):
        resp = error_response("TEST", "something failed")
        assert resp["status"] == "error"
        assert resp["code"] == "TEST"
        assert resp["message"] == "something failed"
        assert "detail" not in resp

    def test_error_response_with_detail(self):
        resp = error_response("TEST", "failed", detail={"extra": 1})
        assert resp["detail"]["extra"] == 1

    def test_success_response(self):
        resp = success_response(data={"count": 5})
        assert resp["status"] == "success"
        assert resp["data"]["count"] == 5


class TestRiskMetrics:
    """Test risk analytics calculations."""

    def test_empty_trades(self):
        result = calculate_risk_metrics([])
        assert result["sharpe_ratio"] == 0
        assert result["equity_curve"] == []

    def test_all_open_trades(self):
        trades = [{"status": "OPEN", "pl_pct": 5.0, "entry_date": "2025-01-01"}]
        result = calculate_risk_metrics(trades)
        assert result["sharpe_ratio"] == 0  # no closed trades

    def test_basic_closed_trades(self, sample_trades):
        result = calculate_risk_metrics(sample_trades)
        assert isinstance(result["sharpe_ratio"], float)
        assert len(result["equity_curve"]) > 0
        assert result["max_consecutive_losses"] >= 0
        assert result["avg_win"] > 0
        assert result["avg_loss"] < 0

    def test_equity_curve_starts_at_100(self, sample_trades):
        result = calculate_risk_metrics(sample_trades)
        assert result["equity_curve"][0]["equity"] == 100.0

    def test_consecutive_losses(self):
        trades = [
            {"status": "CLOSED", "pl_pct": -3.0, "entry_date": "2025-01-01"},
            {"status": "CLOSED", "pl_pct": -2.0, "entry_date": "2025-01-05"},
            {"status": "CLOSED", "pl_pct": -4.0, "entry_date": "2025-01-10"},
            {"status": "CLOSED", "pl_pct": 5.0, "entry_date": "2025-01-15"},
        ]
        result = calculate_risk_metrics(trades)
        assert result["max_consecutive_losses"] == 3


class TestPnlAttribution:
    """Test PnL attribution analysis."""

    def test_empty_trades(self):
        result = calculate_pnl_attribution([])
        assert result["by_industry"] == []
        assert result["by_strategy"] == []

    def test_industry_attribution(self, sample_trades):
        result = calculate_pnl_attribution(sample_trades)
        industries = {item["name"] for item in result["by_industry"]}
        assert "银行" in industries
        assert "白酒" in industries

    def test_strategy_attribution(self, sample_trades):
        result = calculate_pnl_attribution(sample_trades)
        strategies = {item["name"] for item in result["by_strategy"]}
        assert "均线粘合（单策略）" in strategies
        assert "五指标投票共振" in strategies

    def test_attribution_sorted_by_pnl(self, sample_trades):
        result = calculate_pnl_attribution(sample_trades)
        pnls = [item["total_pnl"] for item in result["by_industry"]]
        assert pnls == sorted(pnls, reverse=True)
