"""
Tests for the decision-loop API surfaces added around review, watchlist, and templates.
These stay DB-light so they can run in local environments without PostgreSQL.
"""

import pytest
import os
import sys
import asyncio
import numpy as np
from fastapi import HTTPException
from kombu.utils.json import dumps
from sqlalchemy import create_engine

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
