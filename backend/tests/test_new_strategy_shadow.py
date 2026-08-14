import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.new_strategy_shadow import (
    build_live_shadow_report,
    build_shadow_market_context,
    classify_shadow_candidates,
    load_previous_route_a_candidate_permission,
    record_new_strategy_shadow_report,
)


def _regime(sh_trend="BULL", cyb_trend="BULL"):
    return {
        "status": "OFFENSIVE",
        "indices": {
            "上证": {"trend": sh_trend, "close": 3800, "ema20": 3750},
            "创业": {"trend": cyb_trend, "close": 3500, "ema20": 3400},
        },
    }


def _candidate(code, *, pct_5d, dual=True, pa_score=60, action="WAIT"):
    return {
        "代码": code,
        "名称": f"股票{code}",
        "strategy_type": "tv_dual",
        "tv_match": "双命中" if dual else "单命中",
        "tv_ma_signal": "B共振" if dual else "无",
        "tv_zp_signal": "long",
        "pct_5d": pct_5d,
        "price_action_score": pa_score,
        "pa_trade_plan": {"action": action},
        "pa_entry_price": 10.5,
        "pa_stop_price": 9.8,
        "market_sentiment_stage": "ADVANCE",
        "data_date": "2026-07-27",
        "trade_eligible": True,
        "trade_bucket": "TRADE",
    }


def test_shadow_market_context_uses_dual_index_and_completed_day_hysteresis():
    first_day = build_shadow_market_context(
        _regime(),
        "ADVANCE",
        previous_route_a_candidate_permission=None,
    )
    second_day = build_shadow_market_context(
        _regime(),
        "ADVANCE",
        previous_route_a_candidate_permission="CONFIRM",
    )

    assert first_day["index_axis"] == "T2"
    assert first_day["breadth_axis"] == "B2"
    assert first_day["route_a_permission_1d"] == "CONFIRM"
    assert first_day["route_a_permission_2d"] == "OBSERVE"
    assert second_day["route_a_permission_2d"] == "CONFIRM"


def test_intraday_shadow_permission_stays_pending_until_close():
    context = build_shadow_market_context(
        _regime(),
        "ADVANCE",
        previous_route_a_candidate_permission="CONFIRM",
        completed_day=False,
    )

    assert context["route_a_candidate_permission"] == "CONFIRM"
    assert context["route_a_permission_1d"] == "PENDING_CLOSE"
    assert context["route_a_permission_2d"] == "PENDING_CLOSE"


def test_previous_permission_requires_adjacent_completed_trading_day(monkeypatch):
    payloads = pd.DataFrame(
        {
            "payload": [
                '{"data_date":"2026-07-24","completed_day":false,'
                '"route_a_candidate_permission":"CONFIRM"}',
                '{"data_date":"2026-07-23","completed_day":true,'
                '"route_a_candidate_permission":"CONFIRM"}',
            ]
        }
    )
    monkeypatch.setattr(pd, "read_sql", lambda *args, **kwargs: payloads)
    monkeypatch.setattr(
        "core.trading_calendar.previous_a_share_trading_date",
        lambda value: "2026-07-24",
    )

    assert load_previous_route_a_candidate_permission(
        object(),
        data_date="2026-07-27",
    ) is None

    payloads.loc[0, "payload"] = (
        '{"data_date":"2026-07-24","completed_day":true,'
        '"route_a_candidate_permission":"CONFIRM"}'
    )
    assert load_previous_route_a_candidate_permission(
        object(),
        data_date="2026-07-27",
    ) == "CONFIRM"


def test_shadow_classification_keeps_routes_independent_and_never_tradable():
    context = build_shadow_market_context(
        _regime(),
        "ADVANCE",
        previous_route_a_candidate_permission="CONFIRM",
    )

    result = classify_shadow_candidates(
        [
            _candidate("000001", pct_5d=10),
            _candidate("000002", pct_5d=20),
            _candidate("000003", pct_5d=5, dual=False),
        ],
        context,
    )
    by_code = {item["代码"]: item for item in result}

    assert by_code["000001"]["shadow_route"] == "A"
    assert by_code["000001"]["shadow_state"] == "A_CONFIRMATION_WATCH"
    assert by_code["000002"]["shadow_route"] == "B"
    assert by_code["000002"]["shadow_state"] == "OVEREXTENDED_WATCH"
    assert "禁止立即追价" in "；".join(by_code["000002"]["shadow_blockers"])
    assert by_code["000003"]["shadow_route"] == "DISCOVERY"
    assert by_code["000003"]["shadow_state"] == "WAIT_STRICT_DUAL"

    assert all(item["trade_eligible"] is False for item in result)
    assert all(item["trade_bucket"] == "SHADOW" for item in result)
    assert all(item["source_trade_eligible"] is True for item in result)
    assert all(item["trade_execution_policy"] == "NEW_STRATEGY_SHADOW_ONLY" for item in result)


def test_shadow_route_a_fails_closed_when_market_or_plan_data_is_invalid():
    context = build_shadow_market_context(
        _regime(sh_trend="BEAR", cyb_trend="BEAR"),
        "RETREAT",
        previous_route_a_candidate_permission="CONFIRM",
    )
    candidate = _candidate("000001", pct_5d=10, pa_score=50, action="AVOID")
    candidate["pa_stop_price"] = 11

    result = classify_shadow_candidates([candidate], context)[0]

    assert result["shadow_route"] == "A"
    assert result["shadow_state"] == "BLOCKED_SHADOW"
    blocker_text = "；".join(result["shadow_blockers"])
    assert "PA行动为AVOID" in blocker_text
    assert "PA分数低于55" in blocker_text
    assert "确认价或止损价无效" in blocker_text
    assert "市场双轴未允许路线A确认" in blocker_text
    assert result["trade_eligible"] is False


def test_repair_state_is_market_level_route_c_watch_not_route_migration():
    context = build_shadow_market_context(
        _regime(sh_trend="BEAR", cyb_trend="BEAR"),
        "V_REPAIR",
        previous_route_a_candidate_permission=None,
    )
    result = classify_shadow_candidates([_candidate("000001", pct_5d=8)], context)[0]

    assert context["route_c_market_watch"] is True
    assert result["shadow_route"] == "A"
    assert result["shadow_route"] != "C"
    assert result["trade_eligible"] is False


def test_live_report_and_audit_never_create_execution_permission(monkeypatch):
    source = _candidate("000001", pct_5d=8)
    report = build_live_shadow_report(
        [source],
        regime=_regime(),
        previous_route_a_candidate_permission="CONFIRM",
        completed_day=True,
    )
    recorded = []
    monkeypatch.setattr(
        "core.audit_log.record_lifecycle_event",
        lambda event_type, **values: recorded.append((event_type, values)) or True,
    )

    record_new_strategy_shadow_report(report)

    assert report["trade_eligible"] is False
    assert report["candidates"][0]["trade_eligible"] is False
    assert source["trade_eligible"] is True
    assert recorded[0][0] == "NEW_STRATEGY_SHADOW_MARKET"
    observed = next(values for event, values in recorded if event == "NEW_STRATEGY_SHADOW_OBSERVED")
    assert observed["payload"]["trade_eligible"] is False
