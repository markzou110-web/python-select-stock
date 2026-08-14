import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.theme_leadership import (
    build_theme_leadership_body,
    detect_technical_seeds,
    extract_market_themes,
    enrich_theme_seeds,
    summarize_theme_leadership_outcomes,
)


def _history() -> pd.DataFrame:
    dates = pd.bdate_range("2026-06-01", periods=25)
    rows = []
    for code, name, industry, closes, volumes in [
        (
            "000779",
            "甘咨询",
            "建筑工程",
            [7.45 + index * 0.01 for index in range(20)] + [7.70, 7.78, 7.94, 8.12, 8.40],
            [90_000] * 20 + [95_000, 100_000, 120_000, 130_000, 190_000],
        ),
        (
            "000001",
            "行业普通股",
            "建筑工程",
            [10.0] * 20 + [9.95, 9.90, 9.85, 9.80, 9.75],
            [100_000] * 25,
        ),
        (
            "000002",
            "行业弱股",
            "建筑工程",
            [9.0] * 20 + [8.95, 8.90, 8.85, 8.80, 8.75],
            [100_000] * 25,
        ),
    ]:
        for date, close, volume in zip(dates, closes, volumes):
            rows.append({
                "code": code,
                "name": name,
                "industry": industry,
                "date": date,
                "close": close,
                "vol": volume,
            })
    return pd.DataFrame(rows)


def test_extract_market_themes_separates_narrative_from_denial():
    themes = extract_market_themes(
        "国企改革+DeepSeek智能评标+东数西算",
        ["人工智能", "算力概念", "甘肃国企改革"],
    )
    assert themes["themes"] == ["AI应用", "算力", "国企改革"]
    assert themes["credibility"] == "MEDIUM"

    denied = extract_market_themes("公司澄清不涉及算力服务，不属于国资云概念")
    assert denied["themes"] == ["算力"]
    assert denied["credibility"] == "LOW"
    assert denied["denial_detected"] is True


def test_detect_technical_seed_is_observation_only():
    snapshot = pd.DataFrame([{
        "code": "000779",
        "name": "甘咨询",
        "price": 8.54,
        "pct_chg": 1.67,
        "turnover": 4.8,
    }])
    seeds = detect_technical_seeds(
        _history(),
        snapshot,
        observed_at=datetime(2026, 7, 6, 9, 35),
        min_industry_members=3,
    )

    assert len(seeds) == 1
    seed = seeds[0]
    assert seed["code"] == "000779"
    assert seed["state"] == "INDEPENDENT_LEADER"
    assert seed["trade_eligible"] is False
    assert seed["trade_bucket"] == "OBSERVE"
    assert seed["instruction_state"] == "THEME_SHADOW"


def test_theme_enrichment_and_bark_never_claim_trade_permission():
    seed = {
        "code": "000779",
        "name": "甘咨询",
        "industry": "建筑工程",
        "price": 8.54,
        "pct_chg": 1.67,
        "ret_5d": 9.66,
        "industry_excess_5d": 6.84,
        "industry_percentile": 88.1,
        "volume_ratio_20d": 1.76,
        "state": "EARLY_WATCH",
        "trade_eligible": False,
        "trade_bucket": "OBSERVE",
        "instruction_state": "THEME_SHADOW",
    }
    items = enrich_theme_seeds(
        [seed],
        hot_reason_map={"000779": "国企改革+DeepSeek智能评标+算力"},
        concept_map={"000779": ["人工智能", "算力概念"]},
    )
    body = build_theme_leadership_body(items, "09:35")

    assert items[0]["market_themes"] == ["AI应用", "算力", "国企改革"]
    assert items[0]["trade_eligible"] is False
    assert "题材独立龙头影子" in body
    assert "不可交易" in body
    assert "不是买入指令" in body
    assert "【可交易" not in body


def test_shadow_review_uses_only_future_bars():
    events = pd.DataFrame([{
        "code": "000779",
        "event_time": "2026-07-03 15:10:00",
        "payload": {"price": 8.40, "state": "INDEPENDENT_LEADER"},
    }])
    daily = pd.DataFrame([
        {"code": "000779", "date": "2026-07-03", "close": 8.40, "high": 8.50, "low": 8.20},
        {"code": "000779", "date": "2026-07-06", "close": 8.67, "high": 8.90, "low": 8.45},
        {"code": "000779", "date": "2026-07-07", "close": 8.74, "high": 9.10, "low": 8.60},
        {"code": "000779", "date": "2026-07-08", "close": 9.10, "high": 9.30, "low": 8.70},
        {"code": "000779", "date": "2026-07-09", "close": 9.30, "high": 9.50, "low": 8.95},
        {"code": "000779", "date": "2026-07-10", "close": 9.66, "high": 9.80, "low": 9.20},
    ])

    review = summarize_theme_leadership_outcomes(events, daily)

    assert review["sample_count"] == 1
    assert review["horizons"]["1d"]["avg_return_pct"] == 3.21
    assert review["horizons"]["5d"]["win_rate_pct"] == 100.0
