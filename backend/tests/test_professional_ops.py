import os
import sys
import json
from datetime import datetime

import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.db import (
    _price_action_detail_snapshot,
    get_scan_history_by_date,
    init_db,
    save_failure_sample,
    save_recommendation_events,
    save_point_in_time_snapshot,
    save_event_catalyst,
    load_active_event_catalysts,
    save_scan_audit_log,
    save_scan_results,
)
from core.models import Base
from core.portfolio_risk import calculate_capital_risk, evaluate_portfolio_risk_budget
from routers import market, paper_trade


def test_init_db_adds_theme_fields_to_legacy_sqlite_tables():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE paper_trading (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE watchlist (id INTEGER PRIMARY KEY)"))

    init_db(engine)

    inspector = inspect(engine)
    assert {"theme", "rise_logic"} <= {column["name"] for column in inspector.get_columns("paper_trading")}
    assert {"theme", "rise_logic"} <= {column["name"] for column in inspector.get_columns("watchlist")}


def test_init_db_adds_tv_execution_state_to_legacy_paper_trades():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE paper_trading (id INTEGER PRIMARY KEY)"))

    init_db(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("paper_trading")}
    assert {
        "signal_sources", "execution_tier", "risk_unit", "source_upgraded_at",
        "pending_exit_reason", "pending_exit_signal_date",
    } <= columns


def test_scan_history_replaces_nan_with_json_safe_null():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO scan_history (
                code, date, name, price, pct, score, industry, strategy_type,
                roe, net_profit_yoy
            ) VALUES (
                '000001', '2026-06-12', '平安银行', 10, 1.2, 80, '银行', 'squeeze',
                NULL, NULL
            )
        """))

    result = get_scan_history_by_date("2026-06-12", engine)

    assert result[0]["ROE"] is None
    assert result[0]["净利YOY"] is None


def test_scan_history_keeps_multiple_strategies_for_same_data_date():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    results = [
        {"代码": "000001", "名称": "平安银行", "现价": 10, "涨幅%": 1, "Score": 70, "strategy_type": "pine"},
        {"代码": "000001", "名称": "平安银行", "现价": 10, "涨幅%": 1, "Score": 75, "strategy_type": "squeeze"},
    ]

    assert save_scan_results(results, engine, data_date="2026-06-12") is True

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT date, data_date, strategy_type FROM scan_history ORDER BY strategy_type")).fetchall()
    assert len(rows) == 2
    assert {str(row[0]) for row in rows} == {"2026-06-12"}
    assert {str(row[1]) for row in rows} == {"2026-06-12"}

    assert save_scan_results(
        [{"代码": "000002", "名称": "万科A", "现价": 8, "涨幅%": 0.5, "Score": 68, "strategy_type": "pine"}],
        engine,
        data_date="2026-06-12",
    ) is True
    with engine.connect() as conn:
        strategies = conn.execute(text("SELECT code, strategy_type FROM scan_history ORDER BY strategy_type")).fetchall()
    assert strategies == [("000002", "pine"), ("000001", "squeeze")]


def test_scan_history_preserves_result_group_snapshot():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    results = [
        {
            "代码": "000001", "名称": "正式信号", "现价": 10, "涨幅%": 1,
            "Score": 70, "strategy_type": "tv_dual_strict", "result_group": "FORMAL",
        },
        {
            "代码": "000002", "名称": "历史复活", "现价": 11, "涨幅%": 2,
            "Score": 68, "strategy_type": "tv_dual_strict", "result_group": "HISTORICAL_REVIVAL",
        },
        {
            "代码": "000003", "名称": "动量观察", "现价": 12, "涨幅%": 3,
            "Score": 66, "strategy_type": "tv_dual_strict", "result_group": "MOMENTUM_WATCH",
        },
    ]

    assert save_scan_results(results, engine, data_date="2026-06-12") is True

    history = get_scan_history_by_date("2026-06-12", engine)
    assert {row["代码"]: row.get("result_group") for row in history} == {
        "000001": "FORMAL",
        "000002": "HISTORICAL_REVIVAL",
        "000003": "MOMENTUM_WATCH",
    }


def test_scan_snapshot_detail_keeps_market_data_timestamp():
    detail = json.loads(_price_action_detail_snapshot({
        "data_mode": "LIVE_SNAPSHOT",
        "as_of": "2026-06-12 10:30:00",
    }))

    assert detail == {
        "data_mode": "LIVE_SNAPSHOT",
        "as_of": "2026-06-12 10:30:00",
    }


def test_scan_snapshot_preserves_ai_evidence_fields():
    detail = json.loads(_price_action_detail_snapshot({
        "mkt_cap_yi": 123.4,
        "money_flow_status": "ok",
        "money_flow": {
            "main_net_inflow_yi": 1.25,
            "main_net_ratio": 3.6,
            "source": "eastmoney_akshare",
        },
        "回测统计": {
            "profit_factor": 1.72,
            "expectancy": 2.1,
            "signal_count": 18,
        },
    }))

    assert detail["mkt_cap_yi"] == 123.4
    assert detail["money_flow"]["main_net_inflow_yi"] == 1.25
    assert detail["money_flow_status"] == "ok"
    assert detail["回测统计"]["expectancy"] == 2.1


def test_result_group_migration_does_not_reclassify_explicit_formal_record():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO scan_history (
                code, date, data_date, name, strategy_type, result_group, sop_checks
            ) VALUES (
                '000001', '2026-06-12', '2026-06-12', '正式信号',
                'tv_dual_strict', 'FORMAL', '["复活条件仅作说明"]'
            )
        """))

    init_db(engine)

    with engine.connect() as conn:
        result_group = conn.execute(text(
            "SELECT result_group FROM scan_history WHERE code = '000001'"
        )).scalar_one()
    assert result_group == "FORMAL"


def test_result_group_migration_defaults_legacy_sqlite_rows_without_sop_columns():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE scan_history (
                id INTEGER PRIMARY KEY,
                code VARCHAR(20) NOT NULL,
                date DATE NOT NULL,
                name VARCHAR(100)
            )
        """))
        conn.execute(text("""
            INSERT INTO scan_history (code, date, name)
            VALUES ('000001', '2026-06-12', '旧版记录')
        """))

    init_db(engine)

    with engine.connect() as conn:
        result_group = conn.execute(text(
            "SELECT result_group FROM scan_history WHERE code = '000001'"
        )).scalar_one()
    assert result_group == "FORMAL"


def test_empty_scan_replaces_requested_strategy_snapshot_only():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    existing = [
        {"代码": "000001", "名称": "平安银行", "现价": 10, "涨幅%": 1, "Score": 70, "strategy_type": "pine"},
        {"代码": "000002", "名称": "万科A", "现价": 8, "涨幅%": 0.5, "Score": 68, "strategy_type": "squeeze"},
    ]
    assert save_scan_results(existing, engine, data_date="2026-06-12") is True

    assert save_scan_results(
        [],
        engine,
        data_date="2026-06-12",
        replace_strategy_types=["pine"],
    ) is True

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT code, strategy_type FROM scan_history")).fetchall()
    assert rows == [("000002", "squeeze")]


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
        "as_of": "2025-05-31 15:00:00",
        "data_mode": "LOCAL_DB",
        "field_coverage": {"price": 1.0, "turnover": 0.0},
        "effective_filters": ["target_market", "positive_pct_change"],
        "research_only": True,
        "degradation_reasons": ["换手率字段不完整"],
    }, engine)

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT data_mode, research_only, field_coverage, effective_filters
            FROM scan_audit_log
        """)).mappings().one()

    assert ok is True
    assert row["data_mode"] == "LOCAL_DB"
    assert row["research_only"] == 1
    assert "turnover" in row["field_coverage"]
    assert "positive_pct_change" in row["effective_filters"]


def test_init_db_adds_point_in_time_fields_to_legacy_scan_audit_table():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE scan_audit_log (id INTEGER PRIMARY KEY)"))

    init_db(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("scan_audit_log")}
    assert {
        "as_of", "data_mode", "field_coverage", "effective_filters",
        "research_only", "degradation_reasons",
    } <= columns


def test_point_in_time_snapshot_is_versioned_and_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    snapshot = pd.DataFrame([{
        "code": "000001", "name": "平安银行", "industry": "银行",
        "turnover": 2.5, "mkt_cap": 100000000000,
    }])
    assert save_point_in_time_snapshot(snapshot, "LOCAL_DB:2026-07-11", "2026-07-11", "LOCAL_DB", engine) == 1
    snapshot.loc[0, "turnover"] = 3.0
    assert save_point_in_time_snapshot(snapshot, "LOCAL_DB:2026-07-11", "2026-07-11", "LOCAL_DB", engine) == 1
    with engine.connect() as conn:
        row = conn.execute(text("SELECT COUNT(*), MAX(turnover) FROM point_in_time_stock_snapshots")).one()
    assert row == (1, 2.5)  # frozen dataset versions are immutable on repeat scans


def test_verified_event_catalyst_round_trip():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    assert save_event_catalyst({
        "code": "000977", "event_type": "EARNINGS_SURPRISE",
        "published_at": "2026-07-08 08:00:00", "title": "半年度业绩预告",
        "profit_growth_low": 226, "profit_growth_high": 288,
        "source_url": "https://static.cninfo.com.cn/example.pdf", "verified": True,
    }, engine)
    items = load_active_event_catalysts(engine, as_of="2026-07-10", days=10)
    assert items["000977"]["profit_growth_low"] == 226


def test_sector_push_gaps_endpoint_explains_hot_sector_without_db(monkeypatch):
    monkeypatch.setattr(market, "get_scan_dates", lambda: ["2026-07-07"])
    monkeypatch.setattr(market, "get_scan_history_by_date", lambda date: [{
        "代码": "000002",
        "名称": "后排B",
        "行业": "机器人",
        "trade_bucket": "OBSERVE",
        "sector_role": "FOLLOWER",
        "trade_blockers": ["强板块后排角色，等待转强为核心股"],
    }])
    monkeypatch.setattr(market, "get_sector_strength", lambda limit=20, force=False: {
        "items": [{
            "industry": "机器人",
            "sector_phase": "SECTOR_CONFIRM",
            "sector_momentum_score": 82,
            "sector_breadth": 75,
        }],
        "cache_hit": False,
    })

    result = market.get_sector_push_gaps(limit=5)

    assert result["scan_date"] == "2026-07-07"
    assert result["scan_result_count"] == 1
    assert result["items"][0]["primary_reason"] == "REAR_ROLE"


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


def test_failure_sample_deduplicates_same_trade_outcome():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    sample = {
        "code": "000001",
        "name": "平安银行",
        "sample_date": "2025-05-31",
        "strategy_type": "pine",
        "failure_type": "manual_loss_close",
        "reason": "假突破",
        "pnl_pct": -3.2,
        "source": "paper_trade_close",
    }

    assert save_failure_sample(sample, engine)
    assert save_failure_sample(sample, engine)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM failure_samples")).scalar()

    assert count == 1


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
        "sector_strength_score": 82,
        "stock_sector_fit_score": 76,
        "sector_alignment_score": 80,
        "pa_trade_plan": {"action": "READY", "setup": "回踩确认"},
    }], engine=engine, source="test", event_date="2025-05-31", market_regime="OFFENSIVE")

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT code, trade_bucket, market_regime, sector_strength_score, stock_sector_fit_score, sector_alignment_score
            FROM recommendation_events
        """)).fetchone()

    assert ok is True
    assert row[0] == "000001"
    assert row[1] == "TRADE"
    assert row[2] == "OFFENSIVE"
    assert row[3] == 82
    assert row[4] == 76
    assert row[5] == 80


def test_recommendation_event_upserts_same_daily_identity_on_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    event = {
        "代码": "000001",
        "名称": "平安银行",
        "现价": 10.5,
        "strategy_type": "tv_dual",
        "trade_bucket": "OBSERVE",
        "final_trade_score": 70,
    }

    assert save_recommendation_events([event], engine=engine, source="bark_next_day", event_date="2026-06-09")
    event["现价"] = 10.8
    assert save_recommendation_events([event], engine=engine, source="bark_next_day", event_date="2026-06-09")

    with engine.connect() as conn:
        row = conn.execute(text("SELECT COUNT(*), MAX(recommendation_price) FROM recommendation_events")).fetchone()

    assert row[0] == 1
    assert row[1] == 10.8


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


def test_portfolio_risk_budget_accepts_empty_optional_position_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    result = evaluate_portfolio_risk_budget(
        engine,
        {
            "code": "002192",
            "price": 146.8,
            "position_pct": None,
            "capital_used": None,
        },
    )

    assert result["status"] == "ok"
    assert result["warnings"] == []


def test_capital_risk_uses_position_amount_and_stop_distance():
    assert calculate_capital_risk(100000, 2.5) == 2500


def test_portfolio_stats_derives_pl_pct_from_trade_prices(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO paper_trading (
                code, name, entry_price, entry_date, current_price, close_price,
                close_date, status, strategy_type, trade_mode
            ) VALUES (
                '000001', '平安银行', 10, '2025-05-30', 11, 11,
                '2025-06-02', 'CLOSED', 'squeeze', 'SIMULATED'
            )
        """))

    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: engine)
    result = paper_trade.get_portfolio_stats()

    assert "error" not in result
    assert result["risk_metrics"]["equity_curve"][-1]["equity"] == 100.5
    assert result["measurement"]["default_position_pct"] == 5.0
    assert result["attribution"]["by_industry"][0]["total_pnl"] == 10.0


def test_paper_list_uses_local_prices_without_refreshing_snapshot(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO paper_trading (
                code, name, entry_price, entry_date, current_price, status,
                strategy_type, trade_mode, theme, rise_logic
            ) VALUES (
                '000001', '平安银行', 10, '2025-05-30', 11, 'OPEN',
                'squeeze', 'SIMULATED', '中特估', '银行板块放量走强'
            )
        """))

    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: engine)
    monkeypatch.setattr(paper_trade, "get_cached_data", lambda *args: None)
    monkeypatch.setattr(paper_trade, "get_stale_cache", lambda *args: None)
    monkeypatch.setattr(
        paper_trade,
        "get_market_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("snapshot fetch should not run")),
    )

    result = paper_trade.list_paper_trades()

    assert result["trades"][0]["current_price"] == 11.0
    assert result["trades"][0]["theme"] == "中特估"
    assert result["trades"][0]["rise_logic"] == "银行板块放量走强"


def test_paper_list_empty_response_contains_complete_zero_stats(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: engine)

    result = paper_trade.list_paper_trades()

    assert result["stats"]["max_drawdown"] == 0
    assert result["stats"]["wins"] == 0
    assert result["stats"]["profit_factor"] == 0
    assert result["stats_by_mode"]["SIMULATED"]["avg_hold_days"] == 0
    assert result["stats_by_mode"]["REAL"]["total"] == 0
    assert result["stats_by_mode"]["REAL"]["risk_metrics"]["equity_curve"] == []


def test_paper_stats_separate_real_and_simulated_and_use_closed_trades_only():
    trades = [
        {"name": "实盘", "status": "CLOSED", "trade_mode": "REAL", "pl_pct": -10.0,
         "hold_days": 3, "entry_date": "2026-01-01", "industry": "银行", "position_pct": 5},
        {"name": "模拟", "status": "CLOSED", "trade_mode": "SIMULATED", "pl_pct": 20.0,
         "hold_days": 4, "entry_date": "2026-01-02", "industry": "科技", "position_pct": 5},
        {"name": "未平仓", "status": "OPEN", "trade_mode": "REAL", "pl_pct": 99.0,
         "hold_days": 1, "entry_date": "2026-01-03", "industry": "银行", "position_pct": 5},
    ]

    real = paper_trade._build_trade_stats([t for t in trades if t["trade_mode"] == "REAL"], scope="REAL")
    simulated = paper_trade._build_trade_stats(
        [t for t in trades if t["trade_mode"] == "SIMULATED"], scope="SIMULATED",
    )

    assert real["total"] == 1
    assert real["total_pl_pct"] == -10.0
    assert real["risk_metrics"]["equity_curve"][-1]["equity"] == 99.5
    assert simulated["total"] == 1
    assert simulated["total_pl_pct"] == 20.0


def test_paper_list_database_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: None)

    with pytest.raises(HTTPException) as exc:
        paper_trade.list_paper_trades()

    assert exc.value.status_code == 503


def test_paper_optional_integer_fields_treat_nan_as_missing():
    assert paper_trade._optional_int(float("nan")) is None
    assert paper_trade._optional_int(None) is None
    assert paper_trade._optional_int(12.0) == 12


def test_paper_trade_inherits_theme_and_logic_from_watchlist():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO watchlist (
                code, name, industry, status, reason, theme, rise_logic, created_at, updated_at
            ) VALUES (
                '002171', '楚江新材', '有色金属', 'WATCHING',
                '突破信号K高点后触发', '铜加工', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))

    trade = paper_trade.PaperTradeCreate(
        code="002171",
        name="楚江新材",
        price=13.59,
        watchlist_id=1,
    )

    theme, rise_logic = paper_trade._resolve_trade_theme_and_logic(engine, trade, "有色金属")

    assert theme == "铜加工"
    assert rise_logic == "突破信号K高点后触发"


def test_add_paper_trade_does_not_report_success_when_insert_conflicts(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX uq_paper_trade_code_date ON paper_trading (code, entry_date)"))

    lifecycle_events = []
    notifications = []
    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: engine)
    monkeypatch.setattr(paper_trade, "get_sector_map", lambda: {})
    monkeypatch.setattr(
        paper_trade,
        "evaluate_portfolio_risk_budget",
        lambda *_args, **_kwargs: {"status": "ok"},
    )
    monkeypatch.setattr(paper_trade, "record_lifecycle_event", lambda *args, **kwargs: lifecycle_events.append((args, kwargs)))
    monkeypatch.setattr(paper_trade, "send_paper_trade_notification", lambda *args: notifications.append(args))

    trade = paper_trade.PaperTradeCreate(code="000958", name="电投产融", price=6.61, force=True)

    assert paper_trade.add_paper_trade(trade)["status"] == "success"
    conflict = paper_trade.add_paper_trade(trade)

    assert conflict["status"] == "error"
    assert "今天已有拟合实盘记录" in conflict["detail"]
    assert len(lifecycle_events) == 1
    assert len(notifications) == 1


def test_add_paper_trade_accepts_missing_optional_position_fields(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX uq_paper_trade_code_date ON paper_trading (code, entry_date)"))
    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: engine)
    monkeypatch.setattr(paper_trade, "get_sector_map", lambda: {})
    monkeypatch.setattr(paper_trade, "record_lifecycle_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(paper_trade, "send_paper_trade_notification", lambda *_args: None)

    trade = paper_trade.PaperTradeCreate(
        code="002192",
        name="融捷股份",
        price=146.8,
        force=True,
    )

    assert paper_trade.add_paper_trade(trade)["status"] == "success"


def test_new_zp_signal_upgrades_existing_ma_position_without_duplicate(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX uq_paper_trade_code_date ON paper_trading (code, entry_date)"))
    lifecycle_events = []
    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: engine)
    monkeypatch.setattr(paper_trade, "get_sector_map", lambda: {})
    monkeypatch.setattr(
        paper_trade,
        "evaluate_portfolio_risk_budget",
        lambda *_args, **_kwargs: {"status": "ok"},
    )
    monkeypatch.setattr(paper_trade, "record_lifecycle_event", lambda *args, **kwargs: lifecycle_events.append((args, kwargs)))
    monkeypatch.setattr(paper_trade, "send_paper_trade_notification", lambda *_args: None)

    ma_trade = paper_trade.PaperTradeCreate(
        code="603259", name="药明康德", price=148.82, strategy_type="tv_dual",
        signal_sources=["ma"], entry_signal_date="2026-08-05", force=True,
    )
    zp_confirmation = paper_trade.PaperTradeCreate(
        code="603259", name="药明康德", price=154.82, strategy_type="tv_dual",
        signal_sources=["zp"], entry_signal_date="2026-08-07", force=True,
    )

    assert paper_trade.add_paper_trade(ma_trade)["status"] == "success"
    upgraded = paper_trade.add_paper_trade(zp_confirmation)

    assert upgraded["status"] == "upgraded"
    assert upgraded["signal_sources"] == ["ma", "zp"]
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT signal_sources, execution_tier, risk_unit FROM paper_trading")).fetchall()
    assert rows == [("ma+zp", "B", 0.6)]
    assert any(args and args[0] == "POSITION_SIGNAL_UPGRADED" for args, _kwargs in lifecycle_events)


def test_real_trade_requires_execution_context_before_open(monkeypatch):
    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: object())

    trade = paper_trade.PaperTradeCreate(
        code="000001",
        name="平安银行",
        price=10.0,
        trade_mode="REAL",
    )

    result = paper_trade.add_paper_trade(trade)

    assert result["status"] == "warning"
    assert result["execution_quality"] == "INCOMPLETE"
    assert "缺少计划价" in "；".join(result["warnings"])
    assert "计划遵守" in "；".join(result["warnings"])


def test_sector_strength_returns_stale_cache_without_recomputing(monkeypatch):
    stale = {
        "items": [{"industry": "银行"}],
        "data_date": market.datetime.now().strftime("%Y-%m-%d"),
        "updated_at": "2026-06-06T09:30:00",
    }
    monkeypatch.setattr(market, "get_cached_data", lambda *args: None)
    monkeypatch.setattr(market, "get_stale_cache", lambda *args: stale)
    monkeypatch.setattr(
        market,
        "get_market_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("snapshot fetch should not run")),
    )

    result = market.get_sector_strength(limit=30)

    assert result["items"] == stale["items"]
    assert result["cache_hit"] is True
    assert result["cache_stale"] is True


def test_local_market_snapshot_uses_latest_two_trading_days():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO stock_basic (code, name, industry) VALUES ('000001', '平安银行', '银行')"))
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close, vol)
            VALUES
            ('000001', '2026-06-04', 10, 11, 9, 10, 100),
            ('000001', '2026-06-05', 10, 12, 10, 11, 120)
        """))

    result = market._get_local_market_snapshot(engine)

    assert result.iloc[0]["price"] == 11
    assert result.iloc[0]["pct_chg"] == 10
    assert result.attrs["data_date"] == "2026-06-05"


def test_market_sentiment_prefers_live_snapshot(monkeypatch):
    live = pd.DataFrame({"pct_chg": [3.0, 2.0, -1.0]})
    monkeypatch.setattr(market, "get_market_snapshot", lambda: live)
    monkeypatch.setattr(market, "get_stale_cache", lambda *args: (_ for _ in ()).throw(
        AssertionError("stale cache should not be used when live snapshot is available")
    ))
    monkeypatch.setattr(market, "get_db_engine", lambda: None)
    monkeypatch.setattr(
        "core.data.get_tool_trade_date_hist",
        lambda: pd.DataFrame({"trade_date": [pd.Timestamp("2026-06-15")]}),
    )
    monkeypatch.setattr("core.data.get_market_regime", lambda: {"status": "OFFENSIVE"})
    monkeypatch.setattr(market.ak, "stock_zt_pool_em", lambda **kwargs: pd.DataFrame())
    monkeypatch.setattr(market.ak, "stock_zt_pool_dtgc_em", lambda **kwargs: pd.DataFrame())

    result = market.get_market_sentiment()

    assert result["snapshot_source"] == "live"
    assert result["snapshot_count"] == 3
    assert result["market_breadth"]["advance_ratio"] == 66.7
