import pandas as pd

from core.price_action import analyze_price_action, build_price_action_annotations


def _ohlc_from_closes(closes):
    rows = []
    for i, close in enumerate(closes):
        open_price = closes[i - 1] if i > 0 else close * 0.99
        high = max(open_price, close) * 1.01
        low = min(open_price, close) * 0.99
        rows.append({
            "日期": f"2026-05-{(i % 28) + 1:02d}",
            "开盘": round(open_price, 2),
            "最高": round(high, 2),
            "最低": round(low, 2),
            "收盘": round(close, 2),
            "成交量": 1000000 + i * 1000,
        })
    return pd.DataFrame(rows)


def test_price_action_detects_bull_context_and_outputs_risk_levels():
    closes = [10 + i * 0.12 for i in range(45)] + [15.5, 15.1, 15.7]
    df = _ohlc_from_closes(closes)
    df.loc[df.index[-1], "开盘"] = 15.0
    df.loc[df.index[-1], "收盘"] = 16.2
    df.loc[df.index[-1], "最高"] = 16.3
    df.loc[df.index[-1], "最低"] = 14.9

    result = analyze_price_action(df)

    assert result["price_action_score"] > 0
    assert result["price_action_regime"] in {"多头趋势", "向上突破"}
    assert result["pa_entry_price"] > result["pa_stop_price"]
    assert result["price_action_summary"]
    assert result["pa_market_cycle"]
    assert result["pa_range_location"] in {"区间上沿", "区间中部", "区间下沿"}
    assert result["pa_trade_plan"]["action"] in {"READY", "WATCH"}
    assert result["pa_trade_plan"]["entry_condition"]
    assert result["pa_trade_plan"]["invalidation"]


def test_price_action_handles_short_data():
    result = analyze_price_action(_ohlc_from_closes([10, 10.1, 10.2]))

    assert result["price_action_score"] == 0
    assert result["price_action_regime"] == "数据不足"
    assert result["pa_trade_plan"]["action"] == "AVOID"


def test_price_action_trade_plan_avoids_range_failed_breakout():
    closes = [10, 10.2, 10.1, 10.3, 10.15, 10.35, 10.2, 10.4] * 4
    df = _ohlc_from_closes(closes)
    df.loc[df.index[-2], "最高"] = 11.2
    df.loc[df.index[-2], "收盘"] = 10.9
    df.loc[df.index[-1], "开盘"] = 10.85
    df.loc[df.index[-1], "最高"] = 10.95
    df.loc[df.index[-1], "最低"] = 10.1
    df.loc[df.index[-1], "收盘"] = 10.25

    result = analyze_price_action(df)

    if result["price_action_pattern"] == "交易区间假突破":
        assert result["pa_trade_plan"]["action"] == "AVOID"
        assert result["pa_trade_plan"]["avoid_reasons"]


def test_price_action_annotations_include_summary_and_lines():
    closes = [10 + i * 0.08 for i in range(60)]
    df = _ohlc_from_closes(closes)

    annotations = build_price_action_annotations(df)

    assert annotations["summary"]["price_action_score"] > 0
    assert isinstance(annotations["markers"], list)
    assert any(line["kind"] in {"entry", "stop"} for line in annotations["lines"])
