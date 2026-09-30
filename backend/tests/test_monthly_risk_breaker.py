"""Elder 6% 月度风险熔断测试（monthly-risk-breaker-v1）。

口径与 portfolio_risk 既有函数一致：
  - 已实现盈亏 = (close_price - entry_price) * shares（当月净额，盈亏互抵）
  - 持仓资金风险 = capital_used * pa_risk_pct / 100
  - 分母 = virtual_total_capital（与 evaluate_portfolio_risk_budget 资本风险口径一致）
熔断为硬限制（同日内熔断，不可被 force 绕过），一行回滚：risk_constants.MONTHLY_RISK_BREAKER_ENABLED=false。
"""
import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base, PaperTrading
from core.portfolio_risk import evaluate_monthly_risk_budget
from core import risk_constants

NOW = pd.Timestamp("2026-09-15 10:00:00")  # 固定月中时刻，避免依赖真实日期


def _engine_with_trades(rows):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    for row in rows:
        session.add(PaperTrading(**row))
    session.commit()
    session.close()
    return engine


def test_monthly_realized_loss_plus_open_risk_halts():
    """当月亏损 5.5% + 持仓风险 0.5% = 6.0% → halt。"""
    engine = _engine_with_trades([
        {  # 当月平仓亏损 55000 元（虚拟资金 100 万的 5.5%）
            "code": "600001", "entry_price": 100.0, "close_price": 45.0,
            "shares": 1000, "status": "CLOSED",
            "entry_date": date(2026, 9, 1), "close_date": date(2026, 9, 10),
        },
        {  # OPEN 持仓风险 5000 元（50 万 × 1%）
            "code": "600002", "entry_price": 10.0, "shares": 50000,
            "status": "OPEN", "capital_used": 500000.0, "pa_risk_pct": 1.0,
            "entry_date": date(2026, 9, 12),
        },
    ])
    result = evaluate_monthly_risk_budget(engine, now=NOW)
    assert result["halted"] is True
    assert result["status"] == "halt"
    assert result["monthly_risk_used_pct"] >= 6.0
    assert result["mtd_realized_pnl"] == pytest.approx(-55000.0)
    assert result["open_capital_risk"] == pytest.approx(5000.0)


def test_monthly_gains_offset_losses_within_month():
    """当月盈亏互抵（净额口径）：+2 万 -3 万 = 净亏 1 万 → 1.0% + 持仓风险 0.1% → ok。"""
    engine = _engine_with_trades([
        {
            "code": "600003", "entry_price": 10.0, "close_price": 12.0,
            "shares": 10000, "status": "CLOSED",
            "entry_date": date(2026, 9, 1), "close_date": date(2026, 9, 5),
        },
        {
            "code": "600004", "entry_price": 20.0, "close_price": 17.0,
            "shares": 10000, "status": "CLOSED",
            "entry_date": date(2026, 9, 6), "close_date": date(2026, 9, 8),
        },
        {
            "code": "600005", "entry_price": 10.0, "shares": 10000,
            "status": "OPEN", "capital_used": 100000.0, "pa_risk_pct": 1.0,
            "entry_date": date(2026, 9, 12),
        },
    ])
    result = evaluate_monthly_risk_budget(engine, now=NOW)
    assert result["halted"] is False
    assert result["mtd_realized_pnl"] == pytest.approx(-10000.0)
    assert result["monthly_risk_used_pct"] == pytest.approx(1.1)


def test_last_month_losses_do_not_count():
    """上月平仓亏损不计入本月（月初重置）。"""
    last_month = (NOW - pd.DateOffset(months=1)).date()
    engine = _engine_with_trades([
        {
            "code": "600006", "entry_price": 100.0, "close_price": 45.0,
            "shares": 1000, "status": "CLOSED",
            "entry_date": last_month - timedelta(days=10), "close_date": last_month,
        },
    ])
    result = evaluate_monthly_risk_budget(engine, now=NOW)
    assert result["halted"] is False
    assert result["mtd_realized_pnl"] == 0.0
    assert result["monthly_risk_used_pct"] == 0.0


def test_empty_book_is_ok():
    engine = _engine_with_trades([])
    result = evaluate_monthly_risk_budget(engine, now=NOW)
    assert result["halted"] is False
    assert result["monthly_risk_used_pct"] == 0.0


def test_engine_none_returns_error():
    result = evaluate_monthly_risk_budget(None, now=NOW)
    assert result["status"] == "error"
    assert result["halted"] is False


def test_budget_override_can_tighten_limit():
    """budget 覆盖可收紧月度上限（与其它风控评估器一致的可注入口径）。"""
    engine = _engine_with_trades([
        {
            "code": "600007", "entry_price": 10.0, "close_price": 8.0,
            "shares": 10000, "status": "CLOSED",
            "entry_date": date(2026, 9, 1), "close_date": date(2026, 9, 10),
        },
    ])
    result = evaluate_monthly_risk_budget(engine, now=NOW, budget={"monthly_risk_limit_pct": 1.0})
    assert result["halted"] is True
    assert result["monthly_risk_limit_pct"] == 1.0


def test_constants_registered_with_kill_switch():
    assert risk_constants.MONTHLY_RISK_LIMIT_PCT == 6.0
    assert risk_constants.MONTHLY_RISK_POLICY_VERSION == "monthly-risk-breaker-v1"
    assert isinstance(risk_constants.MONTHLY_RISK_BREAKER_ENABLED, bool)


def test_add_paper_trade_endpoint_halts_on_monthly_breaker(monkeypatch):
    """API 层验证：月度熔断 halt 时 add_paper_trade 返回 halt 且不落库。

    路由内是延迟导入 `from core.portfolio_risk import ...`，
    故 patch 源模块属性（与现有测试 patch paper_trade.evaluate_portfolio_risk_budget 同理）。"""
    from routers import paper_trade

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: engine)
    monkeypatch.setattr(paper_trade, "get_sector_map", lambda: {})
    monkeypatch.setattr(paper_trade, "evaluate_portfolio_risk_budget", lambda *_a, **_k: {"status": "ok"})
    monkeypatch.setattr(
        "core.portfolio_risk.evaluate_daily_loss_circuit_breaker",
        lambda *_a, **_k: {"status": "ok", "halted": False},
    )
    monkeypatch.setattr(
        "core.portfolio_risk.evaluate_monthly_risk_budget",
        lambda *_a, **_k: {
            "status": "halt", "halted": True,
            "monthly_risk_used_pct": 6.5, "monthly_risk_limit_pct": 6.0,
            "policy_version": "monthly-risk-breaker-v1",
            "message": "月度风险用量 6.50% 达到熔断线 6.0%，本月剩余时间暂停新开仓",
        },
    )

    trade = paper_trade.PaperTradeCreate(code="000958", name="电投产融", price=6.61, force=True)
    result = paper_trade.add_paper_trade(trade)

    assert result["status"] == "halt"
    assert "月度风险用量" in result["detail"]
    assert result["monthly_risk_used_pct"] == 6.5
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM paper_trading")).scalar()
    assert count == 0, "halt 时不得写入任何持仓"


def test_add_paper_trade_endpoint_proceeds_when_monthly_budget_ok(monkeypatch):
    """API 层验证：月度熔断 ok 时不拦截正常开仓（happy path 不受新闸门影响）。"""
    from routers import paper_trade

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX uq_paper_trade_code_date ON paper_trading (code, entry_date)"))

    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: engine)
    monkeypatch.setattr(paper_trade, "get_sector_map", lambda: {})
    monkeypatch.setattr(paper_trade, "evaluate_portfolio_risk_budget", lambda *_a, **_k: {"status": "ok"})
    monkeypatch.setattr(paper_trade, "record_lifecycle_event", lambda *_a, **_k: None)
    monkeypatch.setattr(paper_trade, "send_paper_trade_notification", lambda *_a: None)
    monkeypatch.setattr(
        "core.portfolio_risk.evaluate_daily_loss_circuit_breaker",
        lambda *_a, **_k: {"status": "ok", "halted": False},
    )
    monkeypatch.setattr(
        "core.portfolio_risk.evaluate_monthly_risk_budget",
        lambda *_a, **_k: {"status": "ok", "halted": False, "monthly_risk_used_pct": 0.3, "monthly_risk_limit_pct": 6.0},
    )

    trade = paper_trade.PaperTradeCreate(code="000958", name="电投产融", price=6.61, force=True)
    assert paper_trade.add_paper_trade(trade)["status"] == "success"
