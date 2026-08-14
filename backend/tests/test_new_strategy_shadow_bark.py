import os
import sys
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.sentinel as sentinel


def test_new_strategy_shadow_bark_is_explicitly_non_tradable(monkeypatch):
    sent = {}
    monkeypatch.setattr(sentinel, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr(
        sentinel,
        "_send_bark_message",
        lambda title, body, **kwargs: sent.update({"title": title, "body": body}) or True,
    )
    monkeypatch.setattr(sentinel, "_last_new_strategy_shadow_fingerprint", None)
    report = {
        "mode": "SHADOW",
        "data_date": "2026-07-27",
        "completed_day": False,
        "market": {
            "index_axis": "T2",
            "breadth_axis": "B2",
            "breadth_stage": "ADVANCE",
            "route_a_permission_1d": "CONFIRM",
            "route_a_permission_2d": "OBSERVE",
            "route_b_permission": "SHADOW_PULLBACK",
            "route_c_permission": "RESEARCH",
            "route_c_market_watch": False,
        },
        "candidates": [
            {
                "代码": "000001",
                "名称": "路线A样本",
                "shadow_route": "A",
                "shadow_state": "A_CONFIRMATION_WATCH",
                "pct_5d": 8.0,
                "price_action_score": 62,
                "pa_entry_price": 10.5,
                "pa_stop_price": 9.8,
                "shadow_instruction": "观察T+1确认价触发；当前不可交易",
                "shadow_blockers": ["新策略历史证据未达到E3"],
            },
            {
                "代码": "000002",
                "名称": "路线B样本",
                "shadow_route": "B",
                "shadow_state": "OVEREXTENDED_WATCH",
                "pct_5d": 18.0,
                "price_action_score": 65,
                "pa_entry_price": 20.0,
                "pa_stop_price": 18.0,
                "shadow_instruction": "等待1～3日回踩确认；禁止立即追价",
                "shadow_blockers": ["5日涨幅>15%，禁止立即追价"],
            },
        ],
    }

    body = sentinel.send_new_strategy_shadow_notification(report)

    assert body == sent["body"]
    assert "新策略影子观察｜盘中预览｜不可交易" in sent["title"]
    assert "盘中预览（待收盘确认）" in body
    assert "所有标的均不可下单" in body
    assert "路线A样本" in body
    assert "路线B样本" in body
    assert "禁止立即追价" in body
    assert "指令：可交易" not in body

    assert sentinel.send_new_strategy_shadow_notification(report) == ""


def test_completed_shadow_bark_uses_after_close_guard(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        sentinel,
        "is_a_share_intraday_session",
        lambda now=None: False,
    )
    monkeypatch.setattr(
        sentinel,
        "is_a_share_after_close_sync_window",
        lambda now=None: True,
    )
    monkeypatch.setattr(
        sentinel,
        "_send_bark_message",
        lambda title, body, **kwargs: sent.update({"title": title, "body": body}) or True,
    )
    monkeypatch.setattr(sentinel, "_last_new_strategy_shadow_fingerprint", None)
    report = deepcopy(
        {
            "mode": "SHADOW",
            "data_date": "2026-07-27",
            "completed_day": True,
            "market": {
                "index_axis": "T2",
                "breadth_axis": "B2",
                "breadth_stage": "ADVANCE",
                "route_a_permission_1d": "CONFIRM",
                "route_a_permission_2d": "OBSERVE",
                "route_b_permission": "SHADOW_PULLBACK",
                "route_c_permission": "RESEARCH",
            },
            "candidates": [],
        }
    )

    assert sentinel.send_new_strategy_shadow_notification(report)
    assert "收盘确认｜不可交易" in sent["title"]
    assert "时点：收盘确认" in sent["body"]
