"""Router wiring tests for POST /api/ai/analyze-stock."""
import os
import sys

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routers import ai_analysis


def _scan_candidate():
    return {
        "代码": "000001",
        "名称": "平安银行",
        "行业": "银行",
        "trade_bucket": "TRADE",
        "trade_eligible": True,
        "trade_blockers": [],
        "display_signal_score": 78,
    }


def _configure(monkeypatch, *, configured=True):
    monkeypatch.setattr("core.config.config.is_ai_analysis_configured", lambda: configured)


def test_analyze_stock_success_passes_candidate_and_research(monkeypatch):
    _configure(monkeypatch)
    calls = {}

    def fake_research(code, trade_date=None, force_refresh=False):
        calls.update(
            research_code=code,
            research_trade_date=trade_date,
            research_force_refresh=force_refresh,
        )
        return {
            "summary": {},
            "money_flow": {
                "status": "ok",
                "source": "eastmoney_akshare",
                "latest": {"main_net_inflow_yi": 1.2, "main_net_ratio": 3.4},
            },
        }

    def fake_analyze(candidate, research_snapshot=None):
        calls["candidate"] = candidate
        calls["research"] = research_snapshot
        return {"status": "success", "analysis": {"code": "000001"}}

    monkeypatch.setattr(ai_analysis, "_load_scan_candidate", lambda code, signal_date: _scan_candidate())
    monkeypatch.setattr(
        ai_analysis,
        "_fetch_financials",
        lambda code: {"roe": 12.5, "net_profit_yoy": 18.6, "mkt_cap_yi": 850.0},
    )
    monkeypatch.setattr(ai_analysis, "build_stock_research_signals", fake_research)
    monkeypatch.setattr(ai_analysis, "analyze_single_stock", fake_analyze)

    result = ai_analysis.analyze_stock({"code": "000001", "force_refresh": True})

    assert result["status"] == "success"
    assert calls["candidate"]["代码"] == "000001"
    assert calls["research_code"] == "000001"
    assert calls["research_trade_date"] is None
    assert calls["research_force_refresh"] is True
    assert calls["candidate"]["ROE"] == 12.5
    assert calls["candidate"]["净利YOY"] == 18.6
    assert calls["candidate"]["mkt_cap_yi"] == 850.0
    assert calls["candidate"]["money_flow"]["main_net_inflow_yi"] == 1.2
    assert calls["candidate"]["money_flow_status"] == "ok"
    assert calls["research"]["money_flow"]["source"] == "eastmoney_akshare"


def test_load_scan_candidate_restores_fundamentals_and_history(monkeypatch):
    from core.models import Base
    from routers import stock as stock_router

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO scan_history (
                code, name, date, data_date, scanned_at, price, pct, industry,
                strategy_type, roe, net_profit_yoy, win_rate, signal_count,
                price_action_detail
            ) VALUES (
                '000001', '平安银行', '2026-09-11', '2026-09-11', '2026-09-11 15:05:00',
                12.3, 1.2, '银行', 'tv_dual', 10.5, 16.8, '55.6%', 18, '{}'
            )
        """))
    monkeypatch.setattr(stock_router, "get_db_engine", lambda: engine)

    candidate = ai_analysis._load_scan_candidate("000001", None)

    assert candidate["ROE"] == 10.5
    assert candidate["净利YOY"] == 16.8
    assert candidate["历史胜率"] == "55.6%"
    assert candidate["信号次数"] == 18


def test_analyze_stock_without_snapshot_returns_404(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ai_analysis, "_load_scan_candidate", lambda code, signal_date: None)

    with pytest.raises(HTTPException) as exc_info:
        ai_analysis.analyze_stock({"code": "000001"})
    assert exc_info.value.status_code == 404


def test_analyze_stock_rejects_invalid_code(monkeypatch):
    with pytest.raises(HTTPException) as exc_info:
        ai_analysis.analyze_stock({"code": "abc"})
    assert exc_info.value.status_code == 400


def test_analyze_stock_unconfigured_skips_research_fetch(monkeypatch):
    _configure(monkeypatch, configured=False)
    fetched = []
    monkeypatch.setattr(ai_analysis, "_load_scan_candidate", lambda code, signal_date: _scan_candidate())
    monkeypatch.setattr(
        ai_analysis,
        "build_stock_research_signals",
        lambda *args, **kwargs: fetched.append(args) or {},
    )
    monkeypatch.setattr(
        ai_analysis,
        "analyze_single_stock",
        lambda candidate, research_snapshot=None: {"status": "disabled", "analysis": None},
    )

    result = ai_analysis.analyze_stock({"code": "000001"})

    assert result["status"] == "disabled"
    assert fetched == []  # 未配置时不发研究请求


def test_ai_performance_endpoint_uses_bounded_days(monkeypatch):
    calls = {}
    monkeypatch.setattr(ai_analysis, "get_db_engine", lambda: "engine")

    def fake_report(engine, days):
        calls.update(engine=engine, days=days)
        return {"status": "available", "horizons": {}}

    monkeypatch.setattr(ai_analysis, "build_ai_review_performance", fake_report)

    result = ai_analysis.get_ai_performance(days=99999)

    assert result["status"] == "available"
    assert calls == {"engine": "engine", "days": 3650}
