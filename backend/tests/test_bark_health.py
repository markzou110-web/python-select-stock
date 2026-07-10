import os
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.bark_health import build_bark_self_check
from core.models import Base


def test_bark_self_check_reports_ready_snapshot(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO system_settings (key, value) VALUES ('bark_key', 'demo')"))
        conn.execute(text("""
            INSERT INTO watchlist (code, name, watch_price, status, created_at, updated_at)
            VALUES ('000001', '平安银行', 10, 'WATCHING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))
        conn.execute(text("""
            INSERT INTO paper_trading (code, name, entry_price, entry_date, status, trade_mode)
            VALUES ('000001', '平安银行', 10, '2026-06-26', 'OPEN', 'REAL')
        """))

    snapshot = pd.DataFrame({"code": ["000001"], "price": [10.2], "high": [10.5]})
    snapshot.attrs = {"fetched_at": datetime.now(), "source": "测试源"}
    monkeypatch.setattr("core.bark_health.get_market_snapshot", lambda: snapshot)
    monkeypatch.setattr("core.bark_health._bark_key", lambda: "demo")

    payload = build_bark_self_check(engine)

    assert payload["status"] == "ok"
    assert payload["summary"]["snapshot_rows"] == 1
    assert payload["summary"]["watching_count"] == 1
    assert payload["summary"]["open_real_count"] == 1
    assert "Bark：已配置" in payload["body"]
    assert "行情：" in payload["body"]


def test_bark_self_check_warns_when_snapshot_missing(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("core.bark_health.get_market_snapshot", lambda: pd.DataFrame())
    monkeypatch.setattr("core.bark_health._bark_key", lambda: "")

    payload = build_bark_self_check(engine)

    assert payload["status"] == "warn"
    assert payload["summary"]["bark_configured"] is False
    assert payload["summary"]["snapshot_status"] == "error"
    assert "先处理配置或行情快照问题" in payload["body"]
