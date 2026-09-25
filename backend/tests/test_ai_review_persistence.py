"""Persistence round-trip for ai_candidate_reviews + scheduled task wiring."""
import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.db import get_latest_ai_candidate_reviews, save_ai_candidate_reviews


def _make_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ai_candidate_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_date VARCHAR(10) NOT NULL,
                code VARCHAR(10) NOT NULL,
                name VARCHAR(40),
                action VARCHAR(10) NOT NULL,
                confidence INTEGER,
                summary TEXT,
                positive_factors TEXT,
                risk_factors TEXT,
                data_limitations TEXT,
                guardrail_adjusted INTEGER DEFAULT 0,
                entry_price FLOAT,
                stop_price FLOAT,
                target_price FLOAT,
                market_summary TEXT,
                model VARCHAR(80),
                source VARCHAR(20) NOT NULL,
                batch_id VARCHAR(48) NOT NULL,
                created_at TIMESTAMP,
                UNIQUE(review_date, code, source)
            )
        """))
        conn.commit()
    return engine


def _analyses():
    return [
        {
            "code": "000001",
            "name": "平安银行",
            "action": "BUY",
            "confidence": 82,
            "summary": "可进入人工复核",
            "positive_factors": ["策略共振"],
            "risk_factors": ["波动"],
            "data_limitations": ["仅为缓存研究快照摘要，未实时核验"],
            "guardrail_adjusted": False,
            "system_levels": {"entry_price": 12.5, "stop_price": 11.4, "target_price": 14.5},
        },
        {
            "code": "600519",
            "name": "贵州茅台",
            "action": "AVOID",
            "confidence": 30,
            "summary": "存在阻断项",
            "positive_factors": [],
            "risk_factors": ["跌破失效位"],
            "data_limitations": [],
            "guardrail_adjusted": True,
            "system_levels": {"entry_price": None, "stop_price": None, "target_price": None},
        },
    ]


def test_save_and_load_latest_batch_roundtrip():
    engine = _make_engine()

    saved = save_ai_candidate_reviews(
        _analyses(),
        review_date="2026-09-11",
        model="test-model",
        market_summary="候选结构偏强",
        source="scheduled",
        engine=engine,
    )
    assert saved["count"] == 2

    review = get_latest_ai_candidate_reviews("2026-09-11", engine=engine)
    assert review["model"] == "test-model"
    assert review["source"] == "scheduled"
    assert review["market_summary"] == "候选结构偏强"
    # BUY 排在最前，AVOID 在后
    assert [item["action"] for item in review["analyses"]] == ["BUY", "AVOID"]
    buy = review["analyses"][0]
    assert buy["positive_factors"] == ["策略共振"]
    assert buy["guardrail_adjusted"] is False
    assert buy["system_levels"]["stop_price"] == 11.4
    avoid = review["analyses"][1]
    assert avoid["guardrail_adjusted"] is True


def test_resave_same_source_upserts_without_duplicates():
    engine = _make_engine()
    save_ai_candidate_reviews(_analyses(), review_date="2026-09-11", source="manual", engine=engine)
    updated = [_analyses()[0], {**_analyses()[1], "confidence": 10, "summary": "维持回避"}]
    save_ai_candidate_reviews(updated, review_date="2026-09-11", source="manual", engine=engine)

    with engine.connect() as conn:
        count = conn.execute(text(
            "SELECT COUNT(*) FROM ai_candidate_reviews WHERE review_date='2026-09-11'"
        )).scalar()
    assert count == 2
    review = get_latest_ai_candidate_reviews("2026-09-11", engine=engine)
    assert review["analyses"][1]["confidence"] == 10


def test_latest_batch_does_not_include_omitted_rows_from_previous_batch():
    engine = _make_engine()
    first = save_ai_candidate_reviews(
        _analyses(), review_date="2026-09-11", source="manual", engine=engine,
    )
    second = save_ai_candidate_reviews(
        [_analyses()[0]], review_date="2026-09-11", source="manual", engine=engine,
    )

    review = get_latest_ai_candidate_reviews("2026-09-11", engine=engine)

    assert first["batch_id"] != second["batch_id"]
    assert review["batch_id"] == second["batch_id"]
    assert [item["code"] for item in review["analyses"]] == ["000001"]


def test_latest_batch_wins_across_sources():
    engine = _make_engine()
    save_ai_candidate_reviews(_analyses(), review_date="2026-09-11", source="manual", engine=engine)
    save_ai_candidate_reviews(
        [_analyses()[0]], review_date="2026-09-11", source="scheduled", engine=engine,
    )

    review = get_latest_ai_candidate_reviews("2026-09-11", engine=engine)
    assert review["source"] == "scheduled"
    assert len(review["analyses"]) == 1


def test_missing_date_returns_none():
    engine = _make_engine()
    assert get_latest_ai_candidate_reviews("2020-01-01", engine=engine) is None
