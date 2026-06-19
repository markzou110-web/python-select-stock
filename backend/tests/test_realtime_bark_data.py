import os
import sys

import pandas as pd
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_bark_scan_requires_live_snapshot(monkeypatch):
    from core import scanner

    monkeypatch.setattr(scanner, "get_market_regime", lambda: {"status": "UNKNOWN"})
    monkeypatch.setattr(scanner, "get_db_engine", lambda: object())
    monkeypatch.setattr(scanner, "build_scan_preflight", lambda *args, **kwargs: {"blocking": False})
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: pd.DataFrame())

    with pytest.raises(HTTPException) as exc:
        scanner.perform_market_scan(
            local_only=False,
            require_live_snapshot=True,
        )

    assert exc.value.status_code == 503
    assert "实时行情快照不可用" in exc.value.detail


def test_watchlist_status_requires_live_snapshot(monkeypatch):
    from routers import watchlist

    monkeypatch.setattr("core.data.get_market_snapshot", lambda: pd.DataFrame())

    items = [{"code": "000001", "name": "平安银行", "watch_price": 10.0}]

    assert watchlist._refresh_items_with_snapshot(items, require_live_snapshot=True) is None


def test_operation_trigger_skips_bark_without_live_snapshot(monkeypatch):
    from routers import paper_trade

    open_trades = pd.DataFrame([
        {
            "id": 1,
            "code": "000001",
            "name": "平安银行",
            "entry_price": 10.0,
            "current_price": 11.0,
            "high_since_entry": 11.2,
            "entry_date": "2026-06-16",
            "trade_mode": "REAL",
        }
    ])

    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: object())
    monkeypatch.setattr(paper_trade.pd, "read_sql", lambda *args, **kwargs: open_trades)
    monkeypatch.setattr(paper_trade, "get_market_snapshot", lambda: pd.DataFrame())

    result = paper_trade.check_operation_triggers(notify=True, trade_mode="REAL")

    assert result["alerts"] == []
    assert result["notification"] is False
    assert result["reason"] == "live_snapshot_unavailable"
