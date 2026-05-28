"""
Tests for the decision-loop API surfaces added around review, watchlist, and templates.
These stay DB-light so they can run in local environments without PostgreSQL.
"""

import pytest
from fastapi import HTTPException

from core.models import Base
from routers import review, watchlist, strategy_templates


def test_decision_tables_are_registered():
    table_names = set(Base.metadata.tables.keys())
    assert "watchlist" in table_names
    assert "strategy_templates" in table_names


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


def test_strategy_template_requires_name():
    with pytest.raises(HTTPException) as exc:
        strategy_templates.save_strategy_template({"params": {"strategy_type": "squeeze"}})

    assert exc.value.status_code == 400
