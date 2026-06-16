import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import watchlist


def test_build_morning_status_body_has_explicit_price_instructions():
    body = watchlist._build_watchlist_status_body(
        [
            {
                "code": "002378",
                "name": "章源钨业",
                "current_price": 33.10,
                "pl_pct": 1.2,
                "computed_decision": "NEAR_TRIGGER",
                "computed_action": "接近触发：只等放量站稳，不提前追",
                "trigger_price": 33.64,
                "guard_price": 28.51,
                "operation_instruction": "超过33.64且放量站稳后再考虑小仓",
                "market_sentiment_label": "修复",
                "market_sentiment_score": 52.0,
                "portfolio_position_cap_pct": 40,
            }
        ],
        "morning",
    )

    assert "观察池晨间" not in body
    assert "不抢开盘" in body
    assert "站稳 >33.64" in body
    assert "跌破 <28.51" in body
    assert "超过33.64且放量站稳后再考虑小仓" in body


def test_build_status_body_blocks_new_position_during_retreat():
    body = watchlist._build_watchlist_status_body(
        [
            {
                "code": "300481",
                "name": "濮阳惠成",
                "current_price": 17.03,
                "computed_decision": "READY_WAIT",
                "computed_action": "结构就绪：仍需站稳触发价，未触发不买",
                "trigger_price": 17.04,
                "guard_price": 13.08,
                "market_sentiment_stage": "RETREAT",
                "market_sentiment_label": "退潮",
                "market_sentiment_score": 24.5,
                "portfolio_position_cap_pct": 10,
            }
        ],
        "late",
    )

    assert "市场：退潮 24.5分" in body
    assert "市场退潮，禁止新增仓位" in body
    assert "今日只观察，不买入" in body


def test_ready_setup_still_waits_for_price_trigger():
    decision = watchlist._watch_decision(
        {
            "pa_trade_action": "READY",
            "current_price": 17.03,
            "target_price": 17.04,
            "target_hit": False,
        }
    )

    assert decision["decision"] == "NEAR_TRIGGER"


def test_send_watchlist_status_report_skips_empty_watchlist(monkeypatch):
    monkeypatch.setattr(watchlist, "list_watchlist", lambda status="WATCHING": {"items": []})

    result = watchlist.send_watchlist_status_report("late")

    assert result == {"bark": False, "count": 0, "reason": "empty watchlist"}
