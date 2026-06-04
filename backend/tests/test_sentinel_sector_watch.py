import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.sentinel import _candidate_action_label, _select_intraday_push_stocks


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
