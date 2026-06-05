import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routers.stock import _generate_ai_suggestion


def test_ai_suggestion_uses_live_current_price_for_open_position():
    df = pd.DataFrame({
        "收盘": [30.0] * 25,
        "RSI": [51.7] * 25,
        "MACD_HIST": [0.2] * 24 + [0.1],
        "EMA5": [31.0] * 25,
        "EMA20": [30.0] * 25,
        "EMA60": [29.0] * 25,
    })
    stock_info = {
        "is_paper_trade": True,
        "buy_price": 17.53,
        "current_price": 16.70,
        "stop_price": 16.48,
        "take_profit_price": 19.63,
    }

    suggestion = _generate_ai_suggestion(df, stock_info, {"market_regime": "OFFENSIVE"})

    assert suggestion["action"] == "CLOSE"
    assert any("当前盈亏：-4.73%" in reason for reason in suggestion["reasoning"])
    assert any("距止损 1.3%" in reason for reason in suggestion["reasoning"])


def test_ai_suggestion_gives_trade_plan_for_watch_candidate():
    df = pd.DataFrame({
        "收盘": [10.0] * 25,
        "RSI": [58.0] * 25,
        "MACD_HIST": [0.1] * 25,
        "EMA5": [10.5] * 25,
        "EMA20": [10.0] * 25,
        "EMA60": [9.5] * 25,
    })
    stock_info = {
        "is_paper_trade": False,
        "current_price": 10.2,
        "trade_bucket": "TRADE",
        "final_trade_score": 82,
        "latest_scan_date": "2026-06-04",
        "latest_scan_strategy": "tv_dual_strict",
    }
    price_action = {
        "pa_trade_plan": {
            "action": "READY",
            "setup": "强势回踩确认",
            "entry_price": 10.3,
            "stop_price": 9.7,
        }
    }

    suggestion = _generate_ai_suggestion(df, stock_info, {"market_regime": "OFFENSIVE"}, price_action)

    assert suggestion["action"] == "ADD"
    assert "尾盘确认" in suggestion["action_label"]
    assert any("最近入选：2026-06-04" in reason for reason in suggestion["reasoning"])


def test_ai_suggestion_avoids_blocked_watch_candidate():
    df = pd.DataFrame({
        "收盘": [10.0] * 25,
        "RSI": [82.0] * 25,
        "MACD_HIST": [0.1] * 25,
        "EMA5": [10.5] * 25,
        "EMA20": [10.0] * 25,
        "EMA60": [9.5] * 25,
    })
    stock_info = {
        "is_paper_trade": False,
        "current_price": 10.2,
        "trade_bucket": "BLOCK",
        "trade_eligible": False,
        "trade_blockers": ["高开风险"],
    }

    suggestion = _generate_ai_suggestion(df, stock_info, {"market_regime": "OFFENSIVE"}, {"pa_trade_plan": {"setup": "冲高风险"}})

    assert suggestion["action"] == "HOLD"
    assert "回避" in suggestion["action_label"]
    assert any("高开风险" in reason for reason in suggestion["reasoning"])
