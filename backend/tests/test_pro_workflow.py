import os
import sys
from datetime import date

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.portfolio_risk import build_portfolio_exposure
from core.pro_workflow import (
    build_alert_priority,
    build_live_strategy_recommendation,
    build_premarket_checklist,
    recommend_strategy_template,
)


def test_recommend_strategy_template_turns_defensive_on_weak_quality():
    result = recommend_strategy_template(market_regime="DEFENSIVE", risk_status="warning", recent_win_rate=40)

    assert result["profile"] == "防守精选"
    assert result["params"]["pine_min_signals"] == 4
    assert result["params"]["max_open_gap_pct"] == 2.0


def test_recommend_strategy_template_skips_paused_strategy_when_health_is_available():
    result = recommend_strategy_template(
        market_regime="DEFENSIVE",
        risk_status="warning",
        recent_win_rate=40,
        strategy_health={
            "pine": {"status": "PAUSED", "signals": 100, "expected_return": -1.55},
            "tv_zp": {"status": "ACTIVE", "signals": 77, "expected_return": 1.26},
        },
    )

    assert result["profile"] == "防守精选"
    assert result["strategy_type"] == "tv_zp"
    assert result["params"]["strategy_type"] == "tv_zp"
    assert result["strategy_health"]["status"] == "ACTIVE"


def test_strategy_template_route_uses_live_context_by_default(monkeypatch):
    from routers import strategy_templates

    engine = object()
    captured = {}
    monkeypatch.setattr(strategy_templates, "get_db_engine", lambda: engine)
    monkeypatch.setattr(
        strategy_templates,
        "build_live_strategy_recommendation",
        lambda selected_engine, **kwargs: captured.update({
            "engine": selected_engine,
            **kwargs,
        }) or {"profile": "测试"},
    )

    result = strategy_templates.get_strategy_template_recommendation()

    assert result["recommendation"] == {"profile": "测试"}
    assert captured == {
        "engine": engine,
        "market_regime": "UNKNOWN",
        "risk_status": None,
        "recent_win_rate": None,
    }


def test_live_strategy_recommendation_reads_market_portfolio_and_health(monkeypatch):
    engine = object()
    monkeypatch.setattr("core.data.get_market_regime", lambda: {"status": "DEFENSIVE"})
    monkeypatch.setattr(
        "core.portfolio_risk.build_portfolio_exposure",
        lambda selected_engine: {"status": "warning", "engine": selected_engine},
    )
    monkeypatch.setattr(
        "core.strategy_health.build_strategy_health",
        lambda selected_engine: {
            "selection": {
                "strategies": {
                    "pine": {"status": "PAUSED", "expected_return": -1.55},
                    "tv_zp": {"status": "ACTIVE", "expected_return": 1.26},
                }
            }
        },
    )

    result = build_live_strategy_recommendation(engine)

    assert result["profile"] == "防守精选"
    assert result["strategy_type"] == "tv_zp"
    assert result["strategy_health"]["status"] == "ACTIVE"


def test_build_alert_priority_maps_stop_to_p0():
    result = build_alert_priority("critical", -9.2, ["触及止损"])

    assert result["priority"] == "P0"
    assert "立即" in result["label"]
    assert result["dedup_minutes"] == 20


def test_build_premarket_checklist_blocks_on_health_error():
    checklist = build_premarket_checklist(
        health={"status": "error"},
        exposure={"status": "ok"},
        data_quality={"status": "ok", "local_data": {"summary": {}}},
        template_recommendation={"profile": "防守精选", "reason": "测试"},
    )

    assert checklist["status"] == "blocked"
    assert checklist["blockers"] == ["系统健康检查失败"]
    assert checklist["checks"][-1]["label"] == "今日模板"


def test_portfolio_exposure_flags_sector_concentration():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO stock_basic (code, name, industry)
            VALUES ('000001', 'A', '汽车'), ('000002', 'B', '汽车'), ('000003', 'C', '汽车')
        """))
        conn.execute(text("""
            INSERT INTO paper_trading
                (code, name, entry_price, entry_date, status, strategy_type, trade_mode, pa_risk_pct)
            VALUES
                ('000001', 'A', 10, :entry_date, 'OPEN', 'pine', 'REAL', 1.5),
                ('000002', 'B', 10, :entry_date, 'OPEN', 'pine', 'REAL', 1.5),
                ('000003', 'C', 10, :entry_date, 'OPEN', 'pine', 'SIMULATED', 1.5)
        """), {"entry_date": date(2026, 6, 1)})
        conn.commit()

    exposure = build_portfolio_exposure(engine)

    assert exposure["status"] == "warning"
    assert exposure["summary"]["open_positions"] == 3
    assert any(item["kind"] == "sector" and item["name"] == "汽车" and item["status"] == "warning" for item in exposure["items"])
