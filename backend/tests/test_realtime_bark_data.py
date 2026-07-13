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


def test_intraday_bark_groups_only_confirmed_trades_as_executable():
    from core.sentinel import _candidate_push_bucket, _select_intraday_push_stocks

    plain_a = {
        "代码": "000001",
        "sop_grade": "A",
        "trade_bucket": "OBSERVE",
        "trade_eligible": False,
        "final_rank_score": 90,
    }
    early = {
        "代码": "000002",
        "sop_grade": "A",
        "trade_bucket": "EARLY",
        "early_trade_candidate": True,
        "final_rank_score": 80,
    }
    trade = {
        "代码": "000003",
        "sop_grade": "A",
        "trade_bucket": "TRADE",
        "trade_eligible": True,
        "final_rank_score": 70,
    }

    selected = _select_intraday_push_stocks([plain_a, early, trade], executable_limit=3)

    assert _candidate_push_bucket(plain_a) == "禁止追买"
    assert _candidate_push_bucket(early) == "观察"
    assert [item["代码"] for item in selected] == ["000003", "000002", "000001"]


def test_bark_requires_bucket_and_eligibility_for_clear_trade_instruction():
    from core.sentinel import _candidate_brief_lines, _candidate_push_bucket

    inconsistent = {
        "代码": "000004", "名称": "门禁不一致", "sop_grade": "A",
        "trade_bucket": "TRADE", "trade_eligible": False,
    }
    confirmed = {
        "代码": "000005", "名称": "门禁通过", "sop_grade": "A",
        "trade_bucket": "TRADE", "trade_eligible": True,
    }

    assert _candidate_push_bucket(inconsistent) == "禁止追买"
    assert _candidate_brief_lines(inconsistent)[0].startswith("指令：不可交易")
    assert _candidate_push_bucket(confirmed) == "可交易"
    assert _candidate_brief_lines(confirmed)[0].startswith("指令：可交易")


def test_bark_scan_fetches_live_snapshot_even_when_local_only(monkeypatch):
    from core import scanner

    called = {"force_refresh": None}
    snapshot = pd.DataFrame({
        "code": ["000001"],
        "name": ["平安银行"],
        "price": [10.0],
        "open": [9.9],
        "high": [10.2],
        "low": [9.8],
        "pct_chg": [1.0],
        "vol": [1000],
        "turnover": [3.0],
        "mkt_cap": [10000000000],
    })

    def fake_snapshot(force_refresh=False):
        called["force_refresh"] = force_refresh
        return snapshot

    monkeypatch.setattr(scanner, "get_market_regime", lambda: {"status": "UNKNOWN"})
    monkeypatch.setattr(scanner, "get_db_engine", lambda: object())
    monkeypatch.setattr(scanner, "build_scan_preflight", lambda *args, **kwargs: {"blocking": False})
    monkeypatch.setattr(scanner, "get_market_snapshot", fake_snapshot)
    monkeypatch.setattr(scanner, "get_sector_map", lambda: {"000001": "银行"})
    monkeypatch.setattr(scanner, "get_sector_trends", lambda: {})
    monkeypatch.setattr(scanner, "get_suspected_adjustment_gap_codes", lambda *args, **kwargs: set())

    try:
        result = scanner.perform_market_scan(
            local_only=True,
            require_live_snapshot=True,
            turnover_min=0,
            mkt_cap_min=0,
        )
    except Exception:
        result = []

    assert called["force_refresh"] is True
    assert result == []


def test_bark_scan_rejects_stale_snapshot(monkeypatch):
    from datetime import datetime, timedelta
    from core import scanner
    from core.risk_constants import STALE_SNAPSHOT_WARN

    stale = pd.DataFrame({"code": ["000001"], "price": [10.0]})
    stale.attrs = {"fetched_at": datetime.now() - timedelta(minutes=30), "source": STALE_SNAPSHOT_WARN}

    monkeypatch.setattr(scanner, "get_market_regime", lambda: {"status": "UNKNOWN"})
    monkeypatch.setattr(scanner, "get_db_engine", lambda: object())
    monkeypatch.setattr(scanner, "build_scan_preflight", lambda *args, **kwargs: {"blocking": False})
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: stale)

    with pytest.raises(HTTPException) as exc:
        scanner.perform_market_scan(
            local_only=False,
            require_live_snapshot=True,
        )

    assert exc.value.status_code == 503
    assert "过期" in exc.value.detail


def test_watchlist_status_requires_live_snapshot(monkeypatch):
    from routers import watchlist

    monkeypatch.setattr("core.data.get_market_snapshot", lambda: pd.DataFrame())

    items = [{"code": "000001", "name": "平安银行", "watch_price": 10.0}]

    assert watchlist._refresh_items_with_snapshot(items, require_live_snapshot=True) is None


def test_watchlist_status_rejects_stale_snapshot(monkeypatch):
    from datetime import datetime, timedelta
    from routers import watchlist
    from core.risk_constants import STALE_SNAPSHOT_WARN

    stale = pd.DataFrame({"code": ["000001"], "price": [10.0]})
    stale.attrs = {"fetched_at": datetime.now() - timedelta(minutes=30), "source": STALE_SNAPSHOT_WARN}
    monkeypatch.setattr("core.data.get_market_snapshot", lambda: stale)

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


def test_operation_trigger_skips_bark_with_stale_snapshot(monkeypatch):
    from datetime import datetime, timedelta
    from routers import paper_trade
    from core.risk_constants import STALE_SNAPSHOT_WARN

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
    stale = pd.DataFrame({"code": ["000001"], "price": [9.0], "high": [9.2]})
    stale.attrs = {"fetched_at": datetime.now() - timedelta(minutes=30), "source": STALE_SNAPSHOT_WARN}

    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: object())
    monkeypatch.setattr(paper_trade.pd, "read_sql", lambda *args, **kwargs: open_trades)
    monkeypatch.setattr(paper_trade, "get_market_snapshot", lambda: stale)

    result = paper_trade.check_operation_triggers(notify=True, trade_mode="REAL")

    assert result["alerts"] == []
    assert result["notification"] is False
    assert result["reason"] == "live_snapshot_unavailable"


def test_realtime_alert_task_rejects_stale_snapshot(monkeypatch):
    from datetime import datetime, timedelta
    from core import tasks
    from core.risk_constants import STALE_SNAPSHOT_WARN

    open_trades = pd.DataFrame([
        {
            "code": "000001",
            "name": "平安银行",
            "entry_price": 10.0,
            "high_since_entry": 11.0,
            "trade_mode": "REAL",
        }
    ])
    stale = pd.DataFrame({"code": ["000001"], "price": [9.0], "high": [9.2]})
    stale.attrs = {"fetched_at": datetime.now() - timedelta(minutes=30), "source": STALE_SNAPSHOT_WARN}

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(tasks, "get_db_engine", lambda: object())
    monkeypatch.setattr(tasks.pd, "read_sql", lambda *args, **kwargs: open_trades)
    monkeypatch.setattr(tasks, "get_market_snapshot", lambda: stale)

    assert tasks.check_realtime_alerts() == "Failed to fetch fresh snapshot"


def test_wind_control_rejects_stale_snapshot(monkeypatch):
    from datetime import datetime, timedelta
    from routers import paper_trade
    from core.risk_constants import STALE_SNAPSHOT_WARN

    open_trades = pd.DataFrame([
        {
            "id": 1,
            "code": "000001",
            "name": "平安银行",
            "entry_price": 10.0,
            "high_since_entry": 11.0,
            "entry_date": "2026-06-16",
            "trade_mode": "REAL",
        }
    ])
    stale = pd.DataFrame({"code": ["000001"], "price": [9.0], "high": [9.2]})
    stale.attrs = {"fetched_at": datetime.now() - timedelta(minutes=30), "source": STALE_SNAPSHOT_WARN}

    monkeypatch.setattr(paper_trade, "get_db_engine", lambda: object())
    monkeypatch.setattr(paper_trade.pd, "read_sql", lambda *args, **kwargs: open_trades)
    monkeypatch.setattr("core.data.get_market_snapshot", lambda: stale)

    result = paper_trade.run_wind_control()

    assert result["status"] == "error"
    assert "过期" in result["detail"]
