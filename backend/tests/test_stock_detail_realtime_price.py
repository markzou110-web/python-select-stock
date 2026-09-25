import os
import sys

import pandas as pd
from pandas.api.types import is_numeric_dtype

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routers import stock
from routers.stock import _generate_ai_suggestion


def test_detail_kline_loader_normalizes_numeric_strings(monkeypatch):
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    source = pd.DataFrame({
        "日期": [today, today],
        "开盘": ["10.00", "10.20"],
        "最高": ["10.50", "10.80"],
        "最低": ["9.90", "10.10"],
        "收盘": ["10.20", "10.60"],
        "成交量": ["100000", "120000"],
    })

    monkeypatch.setattr(stock, "get_db_engine", lambda: object())
    monkeypatch.setattr(stock, "load_from_db", lambda *_args, **_kwargs: source.copy())
    monkeypatch.setattr(
        "core.data.ensure_turnover_history",
        lambda _code, frame, engine=None: frame,
    )
    monkeypatch.setattr(stock, "calculate_indicators", lambda frame, periods: frame)

    result = stock.fetch_stock_data_with_indicators("300292")

    for column in ("开盘", "最高", "最低", "收盘", "成交量"):
        assert is_numeric_dtype(result[column])
    assert result.iloc[-1]["收盘"] == 10.6


def test_detail_kline_loader_keeps_latest_price_when_volume_is_missing(monkeypatch):
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    source = pd.DataFrame({
        "日期": [today, today],
        "开盘": [10.0, 10.2],
        "最高": [10.5, 10.8],
        "最低": [9.9, 10.1],
        "收盘": [10.2, 10.6],
        "成交量": [100000, "--"],
    })

    monkeypatch.setattr(stock, "get_db_engine", lambda: object())
    monkeypatch.setattr(stock, "load_from_db", lambda *_args, **_kwargs: source.copy())
    monkeypatch.setattr(
        "core.data.ensure_turnover_history",
        lambda _code, frame, engine=None: frame,
    )
    monkeypatch.setattr(stock, "calculate_indicators", lambda frame, periods: frame)

    result = stock.fetch_stock_data_with_indicators("300292")

    assert len(result) == 2
    assert result.iloc[-1]["收盘"] == 10.6
    assert result.iloc[-1]["成交量"] == 0.0


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

    assert suggestion["action"] == "REDUCE"
    assert any("当前盈亏：-4.73%" in reason for reason in suggestion["reasoning"])
    assert any("距止损 1.3%" in reason for reason in suggestion["reasoning"])


def test_ai_suggestion_does_not_close_healthy_position_only_for_critical_market():
    df = pd.DataFrame({
        "收盘": [21.99] * 25,
        "RSI": [71.1] * 25,
        "MACD_HIST": [0.1] * 24 + [0.2],
        "EMA5": [22.5] * 25,
        "EMA20": [21.5] * 25,
        "EMA60": [20.5] * 25,
    })
    stock_info = {
        "is_paper_trade": True,
        "buy_price": 21.07,
        "current_price": 21.99,
        "stop_price": 21.28,
        "structure_stop_price": 20.20,
        "take_profit_price": 27.46,
    }

    suggestion = _generate_ai_suggestion(df, stock_info, {"market_regime": "CRITICAL"})

    assert suggestion["action"] == "HOLD"
    assert "保持仓位" in suggestion["action_label"]


def test_ai_suggestion_closes_after_structure_invalidation():
    df = pd.DataFrame({
        "收盘": [20.0] * 25,
        "RSI": [45.0] * 25,
        "MACD_HIST": [-0.1] * 25,
        "EMA5": [20.0] * 25,
        "EMA20": [20.5] * 25,
        "EMA60": [21.0] * 25,
    })
    stock_info = {
        "is_paper_trade": True,
        "buy_price": 21.07,
        "current_price": 20.0,
        "stop_price": 21.28,
        "structure_stop_price": 20.20,
        "take_profit_price": 27.46,
    }

    suggestion = _generate_ai_suggestion(df, stock_info, {"market_regime": "CRITICAL"})

    assert suggestion["action"] == "CLOSE"
    assert "跌破结构失效线 20.20" in suggestion["action_label"]


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
        "trade_eligible": True,
        "pa_execution_stage": "NEXT_SESSION_EXECUTABLE",
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
    assert "次日已确认" in suggestion["action_label"]
    assert any("最近入选：2026-06-04" in reason for reason in suggestion["reasoning"])


def test_ready_plan_without_next_session_confirmation_is_observation_only():
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
        "trade_bucket": "OBSERVE",
        "trade_eligible": False,
        "pa_execution_stage": "EOD_CONFIRMED",
    }

    suggestion = _generate_ai_suggestion(
        df,
        stock_info,
        {"market_regime": "OFFENSIVE"},
        {"pa_trade_plan": {"action": "READY", "setup": "强势回踩确认"}},
    )

    assert suggestion["action"] == "HOLD"
    assert "次日" in suggestion["action_label"]


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
