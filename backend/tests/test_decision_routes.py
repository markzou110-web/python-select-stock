"""
Tests for the decision-loop API surfaces added around review, watchlist, and templates.
These stay DB-light so they can run in local environments without PostgreSQL.
"""

import pytest
import os
import sys
import asyncio
import numpy as np
import pandas as pd
from fastapi import HTTPException
from kombu.utils.json import dumps
from sqlalchemy import create_engine
from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from routers import review, scan, watchlist, strategy_templates


def test_decision_tables_are_registered():
    table_names = set(Base.metadata.tables.keys())
    assert "watchlist" in table_names
    assert "strategy_templates" in table_names
    assert "recommendation_events" in table_names
    assert {"theme", "rise_logic"} <= set(Base.metadata.tables["watchlist"].columns.keys())
    assert {"theme", "rise_logic"} <= set(Base.metadata.tables["paper_trading"].columns.keys())


def test_review_returns_empty_payload_without_database(monkeypatch):
    monkeypatch.setattr(review, "get_db_engine", lambda: None)

    payload = review.get_scan_performance()

    assert payload["summary"]["signals"] == 0
    assert payload["horizons"] == []
    assert payload["by_strategy"] == []

    dashboard = review.get_profitability_dashboard()
    assert dashboard["summary"]["layers"] == 0
    assert "数据库未连接" in dashboard["notes"]


def test_profitability_layers_include_early_and_bark():
    scan_df = pd.DataFrame([
        {
            "signal_date": "2026-07-01",
            "performance_eligible": True,
            "trade_eligible": "false",
            "trade_bucket": "OBSERVE",
            "early_trade_candidate": True,
            "early_trade_grade": "A-",
            "strategy_type": "tv_dual_strict",
            "sector_alignment_score": 88,
            "ret_1d": 1.0,
            "ret_3d": 2.0,
            "ret_5d": 3.0,
            "ret_10d": 4.0,
        },
        {
            "signal_date": "2026-07-01",
            "performance_eligible": True,
            "trade_eligible": "true",
            "trade_bucket": "TRADE",
            "early_trade_candidate": False,
            "early_trade_grade": "",
            "strategy_type": "tv_dual",
            "sector_alignment_score": 20,
            "ret_1d": -1.0,
            "ret_3d": -2.0,
            "ret_5d": -3.0,
            "ret_10d": -4.0,
        },
    ])
    event_df = pd.DataFrame([
        {
            "source": "bark",
            "event_date": "2026-07-01",
            "ret_1d": 0.5,
            "ret_3d": 1.5,
            "ret_5d": 2.5,
            "ret_10d": 3.5,
        }
    ])

    layers = {row["key"]: row for row in review._build_profitability_layers(scan_df, event_df)}

    assert layers["early_a_minus"]["metrics"]["5d"]["signals"] == 1
    assert layers["early_a_minus"]["metrics"]["5d"]["avg_return"] == 3.0
    assert layers["trade_a"]["metrics"]["5d"]["avg_return"] == -3.0
    assert layers["bark"]["source"] == "recommendation_events"
    assert layers["bark"]["metrics"]["5d"]["avg_return"] == 2.5


def test_bark_success_profile_extracts_common_winning_features():
    event_df = pd.DataFrame([
        {
            "source": "bark",
            "strategy_type": "tv_dual_strict",
            "trade_bucket": "TRADE",
            "pa_trade_setup": "H2二次入场",
            "sector_phase": "SECTOR_CONFIRM",
            "market_regime": "OFFENSIVE",
            "ret_5d": 4.0,
        },
        {
            "source": "bark_intraday",
            "strategy_type": "tv_dual_strict",
            "trade_bucket": "TRADE",
            "pa_trade_setup": "H2二次入场",
            "sector_phase": "SECTOR_CONFIRM",
            "market_regime": "OFFENSIVE",
            "ret_5d": 2.0,
        },
        {
            "source": "scan",
            "strategy_type": "tv_dual",
            "trade_bucket": "OBSERVE",
            "pa_trade_setup": "震荡观察",
            "sector_phase": "SECTOR_FADE",
            "market_regime": "DEFENSIVE",
            "ret_5d": -3.0,
        },
    ])

    profile = review._build_bark_success_profile(event_df)

    assert profile["sample"] == 2
    assert profile["win_rate_5d"] == 100.0
    values = {(row["feature"], row["value"]) for row in profile["features"]}
    assert ("strategy_type", "tv_dual_strict") in values
    assert ("pa_trade_setup", "H2二次入场") in values


def test_high_open_buyability_waits_for_pullback_or_confirms_stand():
    waiting = review._high_open_buyability(5.0, 10.0, 9.4, 9.8, 10.2, 10.4, 6.1)
    confirmed = review._high_open_buyability(5.0, 10.0, 9.4, 9.8, 9.95, 10.15, 3.6)
    failed = review._high_open_buyability(5.0, 10.0, 9.4, 9.8, 9.2, 9.6, -2.0)

    assert waiting["state"] == "WAIT_PULLBACK"
    assert confirmed["state"] == "CONFIRMED"
    assert failed["state"] == "FAILED"


def test_daily_ops_review_groups_execution_decisions(monkeypatch):
    monkeypatch.setattr(review, "get_db_engine", lambda: None)
    monkeypatch.setattr(
        review,
        "get_next_day_followup",
        lambda date=None, limit=80: {
            "date": "2026-07-02",
            "summary": {"tracked": 4},
            "items": [
                {
                    "code": "000001",
                    "name": "确认票",
                    "execution_action": "尾盘确认可试：小仓、贴近入场线",
                    "followup_status": "触发入场线",
                    "entry_line": 10.2,
                    "stop_line": 9.6,
                    "max_gain_pct": 4.2,
                    "score": 88,
                },
                {
                    "code": "000002",
                    "name": "回踩票",
                    "execution_action": "观察：等回踩/放量站稳",
                    "followup_status": "触发观察",
                    "entry_line": 8.1,
                    "stop_line": 7.5,
                    "trade_bucket": "WATCH",
                    "trade_blockers": "涨幅偏高且质量未确认",
                },
                {
                    "code": "000003",
                    "name": "追高票",
                    "execution_action": "不追：高开超过3%，等回踩",
                    "followup_status": "大涨验证",
                    "trade_bucket": "BLOCK",
                },
                {
                    "code": "000004",
                    "name": "失效票",
                    "execution_action": "取消：已触发风控线",
                    "followup_status": "风控触发",
                    "stop_line": 6.6,
                },
            ],
        },
    )

    payload = review.get_daily_ops_review(date="2026-07-02")

    assert payload["summary"]["bucket_counts"]["confirm_candidate"] == 1
    assert payload["summary"]["bucket_counts"]["wait_pullback"] == 1
    assert payload["summary"]["bucket_counts"]["no_chase"] == 1
    assert payload["summary"]["bucket_counts"]["invalidated"] == 1
    assert "站稳确认价 10.2" in payload["groups"]["confirm_candidate"][0]["ops_instruction"]
    assert any("不追高" in item for item in payload["suggestions"])


def test_missed_strong_review_excludes_selected_codes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO stock_basic (code, name, industry)
            VALUES ('000001', '已选强势', '测试'), ('000002', '漏选强势', '测试')
        """))
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close, vol)
            VALUES
            ('000001', '2026-07-01', 10, 10, 10, 10, 100),
            ('000001', '2026-07-02', 11, 11, 11, 11, 100),
            ('000002', '2026-07-01', 10, 10, 10, 10, 100),
            ('000002', '2026-07-02', 10.8, 10.8, 10.8, 10.8, 100)
        """))

    # SQLite path intentionally skips the SQL window query used in production.
    assert review._find_missed_strong_stocks(engine, "2026-07-02", {"000001"}) == []


def test_scan_history_does_not_fetch_market_snapshot_when_cache_is_empty(monkeypatch):
    monkeypatch.setattr(scan, "get_scan_history_by_date", lambda date: [{"代码": "000001", "现价": 10}])
    monkeypatch.setattr(scan, "get_cached_data", lambda *args: None)
    monkeypatch.setattr(scan, "get_stale_cache", lambda *args: None)
    monkeypatch.setattr(
        scan,
        "get_market_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("history initialization must not fetch live snapshot")),
    )

    result = asyncio.run(scan.get_history_results("2026-06-12"))

    assert result == [{"代码": "000001", "现价": 10}]


def test_scan_task_result_is_celery_json_serializable(monkeypatch):
    monkeypatch.setattr(
        "core.scanner.perform_market_scan",
        lambda **kwargs: [{"代码": "000001", "pa_volume_confirmed": np.bool_(True)}],
    )

    result = scan.run_market_scan_task(strategy_type="squeeze")

    assert result[0]["pa_volume_confirmed"] is True
    dumps(result)


def test_watchlist_rejects_invalid_stock_code():
    with pytest.raises(HTTPException) as exc:
        watchlist.add_watchlist_item({"code": "abc", "watch_price": 10})

    assert exc.value.status_code == 400


def test_watchlist_persists_theme_and_rise_logic(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(watchlist, "get_db_engine", lambda: engine)
    monkeypatch.setattr(watchlist, "_latest_prices", lambda *_: {})

    result = watchlist.add_watchlist_item({
        "code": "000001",
        "name": "平安银行",
        "industry": "银行",
        "watch_price": 10,
        "theme": "中特估",
        "rise_logic": "银行板块放量走强",
    })
    payload = watchlist.list_watchlist()

    assert result["status"] == "success"
    assert payload["items"][0]["theme"] == "中特估"
    assert payload["items"][0]["rise_logic"] == "银行板块放量走强"


def test_watchlist_decision_waits_for_price_trigger_when_setup_is_ready():
    item = {
        "target_hit": False,
        "stop_hit": False,
        "pa_trade_action": "READY",
        "pl_pct": 1.2,
        "current_price": 10,
        "target_price": 10.3,
        "stop_price": 9.5,
    }

    result = watchlist._watch_decision(item)

    assert result["decision"] == "READY_WAIT"
    assert "仍需站稳触发价" in result["action"]
    assert "未触发不买" in result["action"]


def test_strategy_template_requires_name():
    with pytest.raises(HTTPException) as exc:
        strategy_templates.save_strategy_template({"params": {"strategy_type": "squeeze"}})

    assert exc.value.status_code == 400
