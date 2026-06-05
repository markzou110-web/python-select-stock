"""
Tests for the decision-loop API surfaces added around review, watchlist, and templates.
These stay DB-light so they can run in local environments without PostgreSQL.
"""

import pytest
import os
import sys
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from routers import review, watchlist, strategy_templates


def test_decision_tables_are_registered():
    table_names = set(Base.metadata.tables.keys())
    assert "watchlist" in table_names
    assert "strategy_templates" in table_names
    assert "recommendation_events" in table_names


def test_review_returns_empty_payload_without_database(monkeypatch):
    monkeypatch.setattr(review, "get_db_engine", lambda: None)

    payload = review.get_scan_performance()

    assert payload["summary"]["signals"] == 0
    assert payload["horizons"] == []
    assert payload["by_strategy"] == []


def test_watchlist_rejects_invalid_stock_code():
    with pytest.raises(HTTPException) as exc:
        watchlist.add_watchlist_item({"code": "abc", "watch_price": 10})

    assert exc.value.status_code == 400


def test_watchlist_decision_promotes_ready_candidate():
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

    assert result["decision"] == "PROMOTE"
    assert "转可交易" in result["action"]


def test_strategy_template_requires_name():
    with pytest.raises(HTTPException) as exc:
        strategy_templates.save_strategy_template({"params": {"strategy_type": "squeeze"}})

    assert exc.value.status_code == 400
