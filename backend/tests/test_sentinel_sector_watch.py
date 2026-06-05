import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.sentinel import _candidate_action_label, _real_position_action, _select_intraday_push_stocks


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
