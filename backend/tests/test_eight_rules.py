import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.eight_rules import detect_eight_rules
from core.price_action import analyze_price_action, build_price_action_annotations


def _base_frame(rows: int = 65) -> pd.DataFrame:
    data = []
    dates = pd.date_range("2026-01-01", periods=rows, freq="D")
    for i in range(rows):
        close = 10 + i * 0.1
        data.append({
            "日期": dates[i].strftime("%Y-%m-%d"),
            "开盘": close - 0.05,
            "最高": close + 0.12,
            "最低": close - 0.12,
            "收盘": close,
            "成交量": 1_000_000,
        })
    return pd.DataFrame(data)


def test_detects_high_volume_stall_as_risk():
    df = _base_frame()
    idx = df.index[-1]
    df.loc[idx, ["开盘", "最高", "最低", "收盘", "成交量"]] = [16.35, 17.2, 16.2, 16.42, 2_200_000]

    result = detect_eight_rules(df)

    assert result["primary"]["rule_id"] == "HIGH_VOLUME_STALL"
    assert result["primary"]["rule_code"] == "8.1"
    assert result["primary"]["direction"] == "RISK"
    assert result["score_delta"] < 0


def test_detects_volume_breakdown_as_high_confidence_risk():
    df = _base_frame()
    idx = df.index[-1]
    df.loc[idx, ["开盘", "最高", "最低", "收盘", "成交量"]] = [16.2, 16.25, 14.8, 14.9, 2_300_000]

    result = detect_eight_rules(df)

    rule_ids = {item["rule_id"] for item in result["signals"]}
    assert "VOLUME_BREAKDOWN" in rule_ids
    assert result["risk_delta"] > 0


def test_detects_supported_pullback_only_after_close_recovers_support():
    df = _base_frame()
    idx = df.index[-1]
    ema20 = df["收盘"].ewm(span=20, adjust=False).mean().iloc[-1]
    df.loc[idx, ["开盘", "最高", "最低", "收盘", "成交量"]] = [
        ema20,
        ema20 + 0.45,
        ema20 - 0.08,
        ema20 + 0.3,
        850_000,
    ]

    result = detect_eight_rules(df)

    rule_ids = {item["rule_id"] for item in result["signals"]}
    assert "SUPPORTED_PULLBACK" in rule_ids


def test_price_action_exposes_eight_rule_and_chart_marker():
    df = _base_frame()
    idx = df.index[-1]
    df.loc[idx, ["开盘", "最高", "最低", "收盘", "成交量"]] = [16.35, 17.2, 16.2, 16.42, 2_200_000]

    result = analyze_price_action(df)
    annotations = build_price_action_annotations(df)

    assert result["pa_eight_rule_primary"]["rule_id"] == "HIGH_VOLUME_STALL"
    assert result["pa_eight_rule_score_delta"] < 0
    marker = next(
        marker
        for marker in annotations["markers"]
        if marker.get("source") == "eight_rule" and marker.get("label") == "高位放量滞涨"
    )
    assert marker["text"] == "8.1"
    assert marker["size"] < 1
    assert marker["position"] == "inBar"
    assert marker["label"] == "高位放量滞涨"
