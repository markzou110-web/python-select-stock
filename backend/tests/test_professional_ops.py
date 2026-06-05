import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.db import save_failure_sample, save_recommendation_events, save_scan_audit_log
from core.models import Base
from core.portfolio_risk import evaluate_portfolio_risk_budget


def test_scan_audit_log_persists_on_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    ok = save_scan_audit_log({
        "scan_date": "2025-05-31",
        "started_at": datetime.now(),
        "finished_at": datetime.now(),
        "duration_sec": 1.2,
        "status": "SUCCESS",
        "strategy_type": "squeeze",
        "params_snapshot": {"threshold": 0.12},
        "version_snapshot": {"backtest": "v7"},
        "total_snapshot": 100,
        "candidate_count": 20,
        "result_count": 3,
        "fail_reasons": {"量能不足": 17},
    }, engine)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM scan_audit_log")).scalar()

    assert ok is True
    assert count == 1


def test_failure_sample_persists_on_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    ok = save_failure_sample({
        "code": "000001",
        "name": "平安银行",
        "sample_date": "2025-05-31",
        "strategy_type": "pine",
        "failure_type": "manual_loss_close",
        "reason": "假突破",
        "pnl_pct": -3.2,
    }, engine)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT code, pnl_pct FROM failure_samples")).fetchone()

    assert ok is True
    assert row[0] == "000001"
    assert row[1] == -3.2


def test_recommendation_event_persists_on_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    ok = save_recommendation_events([{
        "代码": "000001",
        "名称": "平安银行",
        "行业": "银行",
        "现价": 10.5,
        "Score": 82,
        "strategy_type": "tv_dual",
        "trade_bucket": "TRADE",
        "trade_eligible": True,
        "final_trade_score": 88,
        "pa_trade_plan": {"action": "READY", "setup": "回踩确认"},
    }], engine=engine, source="test", event_date="2025-05-31", market_regime="OFFENSIVE")

    with engine.connect() as conn:
        row = conn.execute(text("SELECT code, trade_bucket, market_regime FROM recommendation_events")).fetchone()

    assert ok is True
    assert row[0] == "000001"
    assert row[1] == "TRADE"
    assert row[2] == "OFFENSIVE"


def test_portfolio_risk_budget_warns_on_exposure():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO stock_basic (code, name, industry) VALUES ('000001', '平安银行', '银行')"))
        conn.execute(text("INSERT INTO stock_basic (code, name, industry) VALUES ('000002', '银行二', '银行')"))
        conn.execute(text("""
            INSERT INTO paper_trading (
                code, name, entry_price, entry_date, current_price, status,
                strategy_type, trade_mode, pa_risk_pct
            ) VALUES (
                '000001', '平安银行', 10, '2025-05-30', 10, 'OPEN',
                'squeeze', 'REAL', 2.5
            )
        """))

    result = evaluate_portfolio_risk_budget(
        engine,
        {"code": "000002", "strategy_type": "squeeze", "trade_mode": "REAL", "pa_risk_pct": 4.0},
        budget={"max_sector_positions": 1, "max_total_plan_risk_pct": 5.0},
    )

    assert result["status"] == "warning"
    assert any("行业" in item for item in result["warnings"])
    assert any("组合计划风险" in item for item in result["warnings"])
