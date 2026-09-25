import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.scanner import _apply_liquidity_and_new_stock_filters


def _bars(code: str, closes, volumes, *, one_price: bool = False) -> list[dict]:
    rows = []
    for index, (close, volume) in enumerate(zip(closes, volumes)):
        spread = 0 if one_price else 0.1
        rows.append({
            "code": code,
            "日期": pd.Timestamp("2026-08-01") + pd.offsets.BDay(index),
            "开盘": close,
            "最高": close + spread,
            "最低": close - spread,
            "收盘": close,
            "成交量": volume,
        })
    return rows


def test_liquidity_filter_requires_five_day_average_amount_above_two_hundred_million():
    candidates = pd.DataFrame({"code": ["000001", "000002"], "name": ["液态", "低流动"]})
    history = pd.DataFrame(
        _bars("000001", [10] * 6, [300_000] * 6)
        + _bars("000002", [10] * 6, [100_000] * 6)
    )

    filtered, stats = _apply_liquidity_and_new_stock_filters(candidates, history)

    assert filtered["code"].tolist() == ["000001"]
    assert filtered.iloc[0]["avg_amount_5d"] == 300_000_000
    assert bool(filtered.iloc[0]["avg_amount_5d_estimated"]) is True
    assert stats["insufficient_liquidity"] == 1


def test_new_one_price_stock_under_five_sessions_is_excluded():
    candidates = pd.DataFrame({"code": ["000003", "000004"], "name": ["一字新股", "正常新股"]})
    history = pd.DataFrame(
        _bars("000003", [10, 11, 12.1, 13.31], [500_000] * 4, one_price=True)
        + _bars("000004", [10, 10.2, 10.1, 10.3], [500_000] * 4)
    )

    filtered, stats = _apply_liquidity_and_new_stock_filters(candidates, history)

    assert filtered["code"].tolist() == ["000004"]
    assert stats["new_one_price_stock"] == 1
