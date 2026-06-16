import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.sentinel import (
    _position_breakout_confirmation,
    _build_after_close_watchlist_body,
    _candidate_action_label,
    _real_position_action,
    _select_after_close_watchlist,
    _select_intraday_push_stocks,
    send_after_close_watchlist,
)


def test_intraday_push_keeps_sector_watch_quota():
    stocks = [
        {"代码": "000001", "名称": "一号", "sop_grade": "A", "Score": 90},
        {"代码": "000002", "名称": "二号", "sop_grade": "B", "Score": 80},
        {"代码": "000003", "名称": "三号", "sop_grade": "C", "Score": 70, "sector_watch_only": True},
        {"代码": "000004", "名称": "四号", "sop_grade": "D", "Score": 95, "sector_watch_only": True},
    ]

    selected = _select_intraday_push_stocks(stocks, executable_limit=1, sector_watch_limit=1)

    assert [s["代码"] for s in selected] == ["000001", "000003"]


def test_sector_watch_action_is_observation_only():
    stock = {"sector_watch_only": True, "涨幅%": 8.2, "sop_grade": "C"}

    label = _candidate_action_label(stock)

    assert "不追" in label
    assert "买点" in label


def test_blocked_candidate_pushes_as_no_chase_sample():
    stock = {
        "代码": "000005",
        "名称": "五号",
        "sop_grade": "A",
        "trade_bucket": "BLOCK",
        "trade_eligible": False,
        "trade_blockers": ["高开风险"],
    }

    selected = _select_intraday_push_stocks([stock], executable_limit=1, sector_watch_limit=1)
    label = _candidate_action_label(stock)

    assert selected == [stock]
    assert "禁止追买" in label
    assert "高开风险" in label


def test_after_close_watchlist_keeps_observe_candidate_and_excludes_block():
    stocks = [
        {
            "代码": "300145",
            "名称": "南方泵业",
            "sop_grade": "M",
            "trade_bucket": "OBSERVE",
            "trade_eligible": False,
            "final_trade_score": 72,
            "现价": 5.62,
            "entry_price": 5.72,
            "plan_stop_price": 4.31,
            "trade_blockers": ["涨停/近涨停，等待隔日确认"],
        },
        {
            "代码": "000001",
            "名称": "风险样本",
            "sop_grade": "A",
            "trade_bucket": "BLOCK",
            "final_trade_score": 95,
        },
    ]

    selected = _select_after_close_watchlist(stocks)
    body = _build_after_close_watchlist_body(selected, "2026-06-09")

    assert [stock["代码"] for stock in selected] == ["300145"]
    assert "站稳 >5.72" in body
    assert "跌破 <4.31" in body
    assert "涨停/近涨停，等待隔日确认" in body
    assert "不是买入指令" in body


def test_after_close_watchlist_keeps_high_opportunity_d_grade_as_observation_only():
    stock = {
        "代码": "300263",
        "名称": "隆华科技",
        "sop_grade": "D",
        "trade_bucket": "OBSERVE",
        "trade_opportunity_score": 68,
        "trade_opportunity_label": "试错仓",
        "execution_instruction": "仅观察；站稳 8.20 且量能确认后复核",
    }

    selected = _select_after_close_watchlist([stock])
    body = _build_after_close_watchlist_body(selected, "2026-06-11")

    assert selected == [stock]
    assert "D级观察" in body
    assert "仅观察" in body


def test_after_close_watchlist_pushes_once_without_real_bark(monkeypatch):
    sent = []
    settings = {}
    stocks = [{
        "代码": "300145",
        "名称": "南方泵业",
        "sop_grade": "M",
        "trade_bucket": "OBSERVE",
        "现价": 5.62,
        "entry_price": 5.72,
        "plan_stop_price": 4.31,
        "strategy_type": "tv_dual_strict",
    }]
    monkeypatch.setattr("core.sentinel.get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr("core.sentinel.save_setting", lambda key, value: settings.update({key: value}) or True)
    monkeypatch.setattr(
        "core.sentinel._send_bark_message",
        lambda title, body: sent.append((title, body)) or True,
    )
    monkeypatch.setattr("core.db.save_recommendation_events", lambda *args, **kwargs: True)

    first = send_after_close_watchlist(
        stocks,
        scan_date="2026-06-09",
        now=datetime(2026, 6, 9, 21, 30),
    )
    second = send_after_close_watchlist(
        stocks,
        scan_date="2026-06-09",
        now=datetime(2026, 6, 9, 21, 31),
    )

    assert first is not None
    assert second is None
    assert len(sent) == 1


def _position_df(last_close: float, last_volume: float) -> pd.DataFrame:
    rows = []
    for idx in range(21):
        rows.append({
            "日期": f"2026-06-{idx + 1:02d}",
            "开盘": 20.0 + idx * 0.05,
            "最高": 21.5 + idx * 0.03,
            "最低": 19.8 + idx * 0.04,
            "收盘": 20.3 + idx * 0.05,
            "成交量": 1000,
        })
    rows[-1].update({
        "开盘": 21.8,
        "最高": max(last_close + 0.1, 22.7),
        "最低": 21.7,
        "收盘": last_close,
        "成交量": last_volume,
    })
    return pd.DataFrame(rows)


def test_real_position_action_explains_breakout_confirmation_before_add():
    suggestion = _real_position_action(
        curr=22.0,
        entry=21.07,
        high_since_entry=22.0,
        risk={"active_stop_price": 21.28, "structure_stop_price": 19.17},
        pa={"pa_entry_price": 22.5},
        signals=[],
        df_hist=_position_df(last_close=22.0, last_volume=1100),
    )

    assert "持有观察" in suggestion
    assert "加仓确认" in suggestion
    assert "站上22.50" in suggestion
    assert "守22.16" in suggestion
    assert "指令" in suggestion
    assert ">22.50: 只确认不追，等价量收齐" in suggestion
    assert "22.16-22.50: 持有观察，不加仓" in suggestion
    assert "<22.16: 撤回加仓计划" in suggestion
    assert "<21.28: 减仓/收紧风控" in suggestion
    assert "<19.17: 结构失效，退出复核" in suggestion


def test_real_position_action_marks_confirmed_volume_breakout():
    suggestion = _real_position_action(
        curr=22.8,
        entry=21.07,
        high_since_entry=22.8,
        risk={"active_stop_price": 21.28, "structure_stop_price": 19.17},
        pa={"pa_entry_price": 22.5, "pa_volume_pattern": "放量突破"},
        signals=[],
        df_hist=_position_df(last_close=22.8, last_volume=2200),
    )

    assert "放量突破已确认" in suggestion
    assert "可小幅加仓" in suggestion
    assert "价✓" in suggestion
    assert "量✓" in suggestion
    assert "收✓" in suggestion
    assert ">22.50: 可小幅加仓" in suggestion
    assert "<22.16: 撤回加仓计划" in suggestion


def test_real_position_action_surfaces_high_confidence_eight_rule_risk():
    suggestion = _real_position_action(
        curr=22.0,
        entry=21.07,
        high_since_entry=22.8,
        risk={"active_stop_price": 21.28, "structure_stop_price": 19.17},
        pa={
            "pa_eight_rule_primary": {
                "label": "高位放量滞涨",
                "direction": "RISK",
                "confidence": 82,
                "invalidation_price": 21.7,
            }
        },
        signals=[],
        df_hist=_position_df(last_close=22.0, last_volume=2200),
    )

    assert "八诀风险触发：高位放量滞涨" in suggestion
    assert "停止加仓并复核减仓" in suggestion
    assert "八诀:高位放量滞涨 <21.7" in suggestion


def test_position_breakout_uses_price_action_dynamic_volume_threshold():
    plan = _position_breakout_confirmation(
        curr=22.5,
        entry=21.0,
        active_stop=20.0,
        pa={"pa_entry_price": 22.0, "pa_breakout_volume_threshold": 1.2},
        df_hist=_position_df(last_close=22.5, last_volume=1300),
    )

    assert plan["volume_ratio_threshold"] == 1.2
    assert plan["volume_ok"] is True
