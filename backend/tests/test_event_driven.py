import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.event_driven import apply_event_catalysts, classify_post_limit_state, finalize_event_trade_state
from core.scanner import _dedupe_trade_blockers
import core.sentinel as sentinel
from core.sentinel import _candidate_brief_action, _candidate_brief_reason, _candidate_push_bucket


CATALYST = {
    "event_type": "EARNINGS_SURPRISE",
    "published_at": "2026-07-08 08:00:00",
    "title": "2026年半年度业绩预告",
    "profit_growth_low": 226,
    "profit_growth_high": 288,
    "source_url": "https://static.cninfo.com.cn/example.pdf",
    "verified": 1,
}


def test_event_limit_up_is_strong_watch_never_trade():
    rows = [{
        "代码": "000977", "涨幅%": 10.0, "limit_up_status": "SEALED",
        "trade_eligible": True, "trade_bucket": "TRADE", "trade_blockers": [],
    }]
    apply_event_catalysts(rows, {"000977": CATALYST})
    row = rows[0]
    assert row["event_post_limit_state"] == "SEALED_UNBUYABLE"
    assert row["event_alert_tier"] == "STRONG_WATCH"
    assert row["trade_eligible"] is False
    assert row["trade_bucket"] == "OBSERVE"
    assert _candidate_push_bucket(row) == "强势异动"
    assert _candidate_brief_action(row) == "强势观察不追高"
    assert "226%" in _candidate_brief_reason(row)


def test_event_first_pullback_state_requires_confirmation_and_volume():
    row = {"pa_pullback_status": "CONFIRMED", "pa_volume_confirmed": True}
    assert classify_post_limit_state(row) == "FIRST_PULLBACK_CONFIRMED"


def test_confirmed_event_pullback_can_upgrade_to_small_trial_trade():
    row = {
        "代码": "000977", "涨幅%": 2.5, "pa_pullback_status": "CONFIRMED",
        "pa_volume_confirmed": True, "pa_risk_reward": 2.4,
        "trade_eligible": False, "trade_bucket": "OBSERVE",
        "trade_blockers": ["市场退潮，暂停新增仓位", "综合机会分<60，暂不交易"],
        "market_sentiment_stage": "RETREAT",
    }
    apply_event_catalysts([row], {"000977": CATALYST})
    finalize_event_trade_state([row])
    assert row["trade_eligible"] is True
    assert row["trade_bucket"] == "TRADE"
    assert row["event_alert_tier"] == "EVENT_TRIAL"
    assert row["position_plan"]["initial_position_pct"] == 2
    assert _candidate_push_bucket(row) == "可交易"
    assert _candidate_brief_action(row) == "事件回踩试仓"


def test_event_trial_never_bypasses_price_or_volume_hard_gate():
    row = {
        "代码": "000977", "涨幅%": 2.0, "pa_pullback_status": "CONFIRMED",
        "pa_volume_confirmed": True, "pa_risk_reward": 2.5,
        "trade_eligible": False, "trade_bucket": "OBSERVE",
        "trade_blockers": ["冲高回落风险"],
    }
    apply_event_catalysts([row], {"000977": CATALYST})
    finalize_event_trade_state([row])
    assert row["trade_eligible"] is False
    assert row["trade_bucket"] == "OBSERVE"


def test_blocker_dedupe_keeps_one_actionable_reason_per_cause():
    blockers = _dedupe_trade_blockers([
        "历史信号复活仅观察，等次日确认",
        "5日涨幅偏高且质量未确认",
        "涨停/近涨停，等待隔日确认",
        "未站稳历史/今日确认价",
        "未站上确认价，等待突破确认",
        "板块联动<70，降级观察",
        "板块强度弱，禁止实盘",
    ])
    assert "涨停/近涨停，等待隔日确认" in blockers
    assert "历史信号复活仅观察，等次日确认" in blockers
    assert "未站上确认价，等待突破确认" in blockers
    assert "板块强度弱，禁止实盘" in blockers
    assert len(blockers) == 4


def test_000977_event_path_never_turns_strength_into_chase_signal():
    path = [
        ({"涨幅%": 10.0, "limit_up_status": "SEALED"}, "SEALED_UNBUYABLE"),
        ({"涨幅%": 10.0, "limit_up_status": "SEALED", "limit_up_streak": 2}, "SEALED_UNBUYABLE"),
        ({"涨幅%": 4.11, "limit_up_streak": 2, "pct_5d": 34.9, "pa_pullback_status": "INVALIDATED"}, "EVENT_INVALIDATED"),
    ]
    for market_state, expected_state in path:
        row = {
            "代码": "000977", "trade_eligible": True, "trade_bucket": "TRADE",
            "trade_blockers": [], **market_state,
        }
        apply_event_catalysts([row], {"000977": CATALYST})
        assert row["event_post_limit_state"] == expected_state
        assert row["event_alert_tier"] == "STRONG_WATCH"
        assert row["trade_eligible"] is False
        assert row["trade_bucket"] == "OBSERVE"


def test_000977_event_observation_stays_out_of_interruptive_bark(monkeypatch):
    sent = []
    row = {
        "代码": "000977", "名称": "浪潮信息", "涨幅%": 10.0,
        "limit_up_status": "SEALED", "sop_grade": "A", "Score": 90,
        "trade_eligible": True, "trade_bucket": "TRADE", "trade_blockers": [],
    }
    apply_event_catalysts([row], {"000977": CATALYST})
    monkeypatch.setattr(sentinel, "is_a_share_intraday_session", lambda: True)
    monkeypatch.setattr(sentinel, "_load_recommendation_priority_adjustments", lambda: {})
    monkeypatch.setattr(sentinel, "_format_market_line", lambda *_: "测试行情")
    monkeypatch.setattr(sentinel, "_should_send_intraday_state", lambda *_: True)
    monkeypatch.setattr(
        sentinel, "_send_bark_message",
        lambda title, message, **kwargs: sent.append((title, message, kwargs)) or False,
    )
    monkeypatch.setattr(sentinel, "_append_real_position_status", lambda *_: None)
    monkeypatch.setattr("core.data.get_market_regime", lambda: {"status": "TEST"})
    monkeypatch.setattr("core.data.get_market_snapshot", lambda: {})
    monkeypatch.setattr("core.data.format_freshness", lambda *_: "测试快照")
    monkeypatch.setattr("core.db.save_recommendation_events", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("core.db.get_db_engine", lambda: None)
    monkeypatch.setattr("core.signal_performance.save_intraday_signal_snapshots", lambda *_args, **_kwargs: 0)

    body = sentinel.send_intraday_notification([row])
    assert body is None
    assert sent == []


def test_bottom_discovery_observation_does_not_push_bark(monkeypatch):
    sent = []
    row = {
        "代码": "000001", "名称": "底部票", "strategy_type": "bottom_discovery",
        "bottom_discovery_watch_only": True, "bottom_discovery_stage": "B1_REVERSAL",
        "sop_grade": "C", "trade_eligible": False, "trade_bucket": "OBSERVE",
    }
    monkeypatch.setattr(sentinel, "is_a_share_intraday_session", lambda: True)
    monkeypatch.setattr(sentinel, "_load_recommendation_priority_adjustments", lambda: {})
    monkeypatch.setattr(sentinel, "_format_market_line", lambda *_: "测试行情")
    monkeypatch.setattr(sentinel, "_should_send_intraday_state", lambda *_: True)
    monkeypatch.setattr(sentinel, "_send_bark_message", lambda title, body, **kwargs: sent.append((title, body)) or False)
    monkeypatch.setattr(sentinel, "_append_real_position_status", lambda *_: None)
    monkeypatch.setattr("core.data.get_market_regime", lambda: {"status": "TEST"})
    monkeypatch.setattr("core.data.get_market_snapshot", lambda: {})
    monkeypatch.setattr("core.data.format_freshness", lambda *_: "测试快照")
    monkeypatch.setattr("core.db.save_recommendation_events", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("core.db.get_db_engine", lambda: None)
    monkeypatch.setattr("core.signal_performance.save_intraday_signal_snapshots", lambda *_args, **_kwargs: 0)

    sentinel.send_intraday_notification([row])

    assert sent == []
