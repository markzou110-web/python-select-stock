import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.candidate_evidence import apply_candidate_evidence, build_candidate_evidence


def _candidate(**overrides):
    row = {
        "代码": "000001",
        "名称": "平安银行",
        "strategy_type": "tv_dual_strict",
        "现价": 10.5,
        "涨幅%": 2.1,
        "pa_entry_price": 10.2,
        "pa_stop_price": 9.6,
        "pa_target_price": 12.0,
        "pa_risk_reward": 3.0,
        "price_action_score": 75,
        "price_action_regime": "多头趋势",
        "price_action_signal": "强多头趋势K",
        "pa_volume_confirmed": True,
        "market_regime": "OFFENSIVE",
        "sector_trend": "LEAD",
        "sector_alignment_score": 82,
        "sector_mainline": "ACTIVE",
        "共振": "🔥 核心热点",
        "trade_eligible": True,
        "trade_bucket": "TRADE",
        "trade_blockers": [],
    }
    row.update(overrides)
    return row


def _research(**overrides):
    payload = {
        "updated_at": "2026-07-13T10:29:00",
        "errors": {},
        "money_flow": {},
        "intraday_fund_flow": [],
        "dragon_tiger": {},
        "daily_dragon_tiger": {},
        "lockup": {},
        "holder_count": [],
        "block_trade": [],
        "reports": [],
        "dividends": [],
        "news": [],
        "announcements": [],
        "summary": {"opportunity_flags": ["板块保持强势"], "risk_flags": []},
    }
    payload.update(overrides)
    return payload


def test_complete_candidate_evidence_is_deterministic_grade_a():
    as_of = datetime(2026, 7, 13, 10, 31)
    first = build_candidate_evidence(_candidate(), _research(), as_of)
    second = build_candidate_evidence(_candidate(), _research(), as_of)

    assert first["evidence_id"] == second["evidence_id"]
    assert first["quality"]["grade"] == "A"
    assert first["quality"]["status"] == "PASS"
    assert first["decision_memo"]["bull_case"]


def test_optional_research_missing_only_lowers_grade_to_b():
    bundle = build_candidate_evidence(_candidate(), None, "2026-07-13")

    assert bundle["quality"]["grade"] == "B"
    assert bundle["quality"]["status"] == "PASS"
    assert any(code.startswith("OPTIONAL_") for code in bundle["quality"]["reason_codes"])


def test_missing_market_context_is_degraded_not_promoted():
    row = _candidate(market_regime=None)
    bundle = build_candidate_evidence(row, _research(), "2026-07-13")

    assert bundle["quality"]["grade"] == "C"
    assert bundle["quality"]["status"] == "DEGRADED"


def test_invalid_quote_is_grade_f():
    bundle = build_candidate_evidence(_candidate(**{"现价": 0}), _research(), "2026-07-13")

    assert bundle["quality"]["grade"] == "F"
    assert bundle["quality"]["status"] == "BLOCKED"


def test_unverified_event_is_required_and_blocked():
    row = _candidate(
        event_driven_candidate=True,
        event_catalyst={
            "title": "业绩预增",
            "published_at": "2026-07-13T09:00:00",
            "source_url": "https://example.invalid/notice",
            "verified": False,
        },
    )
    bundle = build_candidate_evidence(row, _research(), "2026-07-13T10:31:00")

    assert bundle["quality"]["grade"] == "D"
    assert "REQUIRED_EVENT_CATALYST_UNAVAILABLE" in bundle["quality"]["reason_codes"]


def test_future_event_is_rejected():
    row = _candidate(
        event_driven_candidate=True,
        event_catalyst={
            "title": "业绩预增",
            "published_at": "2026-07-13T11:00:00",
            "source_url": "https://example.invalid/notice",
            "verified": True,
        },
    )
    bundle = build_candidate_evidence(row, _research(), "2026-07-13T10:31:00")

    assert bundle["domains"]["event_catalyst"]["status"] == "FUTURE_DATA"
    assert bundle["quality"]["grade"] == "D"


def test_shadow_never_changes_trade_permission():
    row = _candidate(**{"现价": 0})

    summary = apply_candidate_evidence([row], as_of="2026-07-13", mode="SHADOW")

    assert summary["downgraded"] == 0
    assert row["evidence_grade"] == "F"
    assert row["trade_eligible"] is True
    assert row["trade_bucket"] == "TRADE"


def test_enforced_can_only_lower_trade_permission():
    bad = _candidate(**{"现价": 0})
    already_blocked = _candidate(trade_eligible=False, trade_bucket="OBSERVE")

    summary = apply_candidate_evidence([bad, already_blocked], as_of="2026-07-13", mode="ENFORCED")

    assert summary["downgraded"] == 1
    assert bad["trade_eligible"] is False
    assert bad["trade_bucket"] == "BLOCK"
    assert already_blocked["trade_eligible"] is False


def test_unknown_mode_falls_back_to_shadow():
    row = _candidate(**{"现价": 0})

    summary = apply_candidate_evidence([row], as_of="2026-07-13", mode="unexpected")

    assert summary["mode"] == "SHADOW"
    assert row["trade_eligible"] is True
