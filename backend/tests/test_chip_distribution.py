from datetime import date, timedelta
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _chip_frame(rows: int = 120) -> pd.DataFrame:
    start = date(2026, 1, 1)
    records = []
    for index in range(rows):
        close = 10 + index * 0.03
        records.append({
            "日期": str(start + timedelta(days=index)),
            "开盘": close - 0.08,
            "最高": close + 0.20,
            "最低": close - 0.20,
            "收盘": close,
            "成交量": 100_000 + index,
            "换手率": 4.0,
        })
    return pd.DataFrame(records)


def test_chip_distribution_returns_normalized_peak_and_cost_ranges():
    from core.chip_distribution import build_chip_distribution

    result = build_chip_distribution(_chip_frame())

    assert result["available"] is True
    assert result["model"] == "turnover_decay_v1"
    assert result["lookback_days"] == 120
    assert len(result["bars"]) >= 50
    assert abs(sum(row["weight"] for row in result["bars"]) - 1.0) < 0.01
    assert result["price_min"] <= result["peak_price"] <= result["price_max"]
    assert result["cost_90_low"] <= result["cost_70_low"] <= result["average_cost"]
    assert result["average_cost"] <= result["cost_70_high"] <= result["cost_90_high"]
    assert 0 <= result["profit_ratio"] <= 1
    assert result["as_of"] == _chip_frame().iloc[-1]["日期"]
    assert result["buy_impact"] in {"确认加分", "等待回踩", "中性观察", "抑制买入", "数据不足"}
    assert result["holding_impact"] in {"持有", "收紧风控", "止损复核", "观察"}
    assert -8 <= result["score_delta"] <= 6


def test_chip_migration_impact_blocks_adverse_entry_and_tightens_holding():
    from core.chip_distribution import _derive_impacts

    result = _derive_impacts(
        current_price=8.8,
        average_cost=10.0,
        cost_70_low=9.4,
        cost_70_high=10.6,
        cost_90_low=9.0,
        migration="筹码下移",
        concentration_70=0.2,
    )

    assert result["buy_impact"] == "抑制买入"
    assert result["holding_impact"] == "止损复核"
    assert result["score_delta"] == -8


def test_chip_distribution_reports_missing_turnover_without_guessing():
    from core.chip_distribution import build_chip_distribution

    frame = _chip_frame().drop(columns=["换手率"])
    result = build_chip_distribution(frame)

    assert result == {
        "available": False,
        "reason": "historical_turnover_unavailable",
        "model": "turnover_decay_v1",
    }


def test_init_db_adds_turnover_to_legacy_daily_k_table():
    from core.db import init_db

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE daily_k (
                code VARCHAR(20) NOT NULL,
                date DATE NOT NULL,
                open FLOAT,
                high FLOAT,
                low FLOAT,
                close FLOAT,
                vol FLOAT,
                PRIMARY KEY (code, date)
            )
        """))

    init_db(engine)

    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(daily_k)"))}
    assert "turnover" in columns


def test_turnover_history_is_backfilled_once_without_overwriting_fallback(monkeypatch):
    from core import data

    fallback = _chip_frame().drop(columns=["换手率"])
    fetched = _chip_frame()
    saved = []
    data.CACHE.pop("chip_turnover_fetch:000001", None)
    monkeypatch.setattr(data, "resilient_fetch", lambda *args, **kwargs: fetched)
    monkeypatch.setattr(data, "save_to_db", lambda frame, code, engine=None: saved.append((frame, code)))

    result = data.ensure_turnover_history("000001", fallback)

    assert result is fetched
    assert saved == [(fetched, "000001")]
    assert data.get_cached_data("chip_turnover_fetch:000001", 3600) is fetched
