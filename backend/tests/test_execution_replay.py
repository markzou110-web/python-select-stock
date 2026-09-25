import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.execution_replay import (
    _soft_blocker_shadow_eligible,
    build_bark_instruction_evidence,
    build_execution_replay_report,
    build_operation_advice_validation,
    run_historical_execution_replay,
)


def _candidate(code: str, blockers_ready: bool = True):
    detail = {
        "research_eligible": True, "sop_quality_score": 65,
        "raw_score": 90,
        "trade_opportunity_score": 75,
        "market_sentiment_stage": "REPAIR",
        "sector_mainline": "MAINLINE",
        "sector_alignment_score": 85,
        "sector_strength_score": 85,
        "stock_sector_fit_score": 80,
        "sector_role": "LEADER",
        "pa_volume_confirmed": blockers_ready,
        "pa_volume_pattern": "放量突破" if blockers_ready else "量能不足",
        "pa_close_position": 0.8,
        "pa_upper_shadow_pct": 1,
        "pa_pullback_status": "CONFIRMED",
        "execution_plan_frozen": True,
        "frozen_plan_date": "2026-01-02",
        "frozen_confirmation_price": 9.9,
        "frozen_stop_price": 9.0,
        "pct_5d": 5,
        "mkt_cap_yi": 100,
        "evidence_grade": "A",
        "evidence_status": "PASS",
        "evidence_reason_codes": [],
    }
    return {
        "code": code, "name": "测试", "signal_date": "2026-01-05", "price": 10,
        "pct": 2, "score": 90, "industry": "测试", "resonance": "🔥 核心热点",
        "strategy_type": "tv_dual_strict", "sop_grade": "A", "pa_entry_price": 9.9,
        "pa_stop_price": 9, "pa_target_price": 12, "pa_risk_reward": 2,
        "pa_trade_action": "READY", "pa_trade_setup": "强多头趋势K", "pa_risk_pct": 9,
        "price_action_score": 80, "price_action_regime": "多头趋势",
        "price_action_signal": "强多头趋势K", "price_action_pattern": "强多头趋势K",
        "price_action_entry_quality": "高质量", "price_action_detail": detail,
    }


def test_replay_selects_only_current_double_gate_and_keeps_future_outcome_separate():
    candidates = pd.DataFrame([_candidate("000001")])
    outcomes = pd.DataFrame([{
        "code": "000001", "signal_date": "2026-01-05", "strategy_type": "tv_dual_strict",
        "ret_5d": 3.0, "exec_filled": True, "exec_return_pct": 2.4,
    }])
    report = build_execution_replay_report(candidates, outcomes)
    current = next(item for item in report["policies"] if item["policy"] == "current_policy")
    assert current["selected"] == 1
    assert current["metrics_5d"]["avg_return"] == 3.0
    assert current["realistic_execution"]["filled"] == 1
    assert current["realistic_execution"]["metrics"]["avg_return"] == 2.4
    evidence = next(item for item in report["policies"] if item["policy"] == "evidence_enforced_simulation")
    assert evidence["selected"] == 1
    assert report["evidence_quality"]["grade_distribution"] == {"A": 1}
    assert report["verdict"] == "NOT_VALIDATED"


def test_replay_v2_keeps_missing_volume_as_cautious_current_candidate():
    candidate = _candidate("000002", blockers_ready=False)
    candidates = pd.DataFrame([candidate])
    outcomes = pd.DataFrame([{
        "code": "000002", "signal_date": "2026-01-05", "strategy_type": "tv_dual_strict",
        "ret_5d": -8.0,
    }])
    report = build_execution_replay_report(candidates, outcomes)
    current = next(item for item in report["policies"] if item["policy"] == "current_policy")
    assert current["selected"] == 1
    assert current["metrics_5d"]["avg_return"] == -8.0
    assert report["summary"]["point_in_time_candidates"] == 1


def test_soft_blocker_shadow_removes_only_reviewed_explanation_rules():
    soft = {"hard": [], "wait": [], "other": []}
    hard = {"hard": ["风险收益比不足"], "wait": [], "other": []}

    assert _soft_blocker_shadow_eligible(
        {"trade_blockers": ["交易计划未确认"], "pa_trade_action": "READY"}, soft, 70,
    ) is True
    assert _soft_blocker_shadow_eligible(
        {"trade_blockers": ["风险收益比不足"], "pa_trade_action": "READY"}, hard, 70,
    ) is False


def test_historical_replay_supports_sqlite_end_to_end():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE scan_history (
                signal_id INTEGER PRIMARY KEY, code TEXT, name TEXT, data_date TEXT, date TEXT,
                price REAL, pct REAL, score REAL, industry TEXT, resonance TEXT, strategy_type TEXT,
                sop_grade TEXT, sop_quality_score REAL, sop_vetoes TEXT, scanned_at TEXT,
                pa_entry_price REAL, pa_stop_price REAL, pa_target_price REAL, pa_risk_reward REAL,
                pa_trade_action TEXT, pa_trade_setup TEXT, pa_risk_pct REAL, price_action_score REAL,
                price_action_regime TEXT, price_action_signal TEXT, price_action_pattern TEXT,
                price_action_entry_quality TEXT, price_action_detail TEXT
            )
        """))
        conn.execute(text("CREATE TABLE daily_k (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, vol REAL)"))
        detail = '{"research_eligible":true,"trade_opportunity_score":75}'
        conn.execute(text("""
            INSERT INTO scan_history (
                signal_id, code, name, data_date, date, price, strategy_type, sop_grade,
                scanned_at, price_action_detail
            ) VALUES (1, '000001', '测试', date('now', '-6 days'), date('now', '-6 days'),
                      10, 'tv_dual_strict', 'A', datetime('now'), :detail)
        """), {"detail": detail})
        for offset, close in enumerate((10, 10.2, 10.4, 10.6, 10.8, 11.0)):
            conn.execute(text("""
                INSERT INTO daily_k VALUES ('000001', date('now', :offset), :close, :high, :low, :close, 100000)
            """), {"offset": f"{-6 + offset} days", "close": close, "high": close + 0.2, "low": close - 0.2})
    report = run_historical_execution_replay(engine, days=30)
    assert report["summary"]["point_in_time_candidates"] == 1
    assert report["days"] == 30
    assert report["operation_advice_validation"]["production_logic_changed"] is False


def test_operation_advice_validation_never_changes_selection_when_data_is_insufficient():
    report = build_operation_advice_validation(pd.DataFrame(), pd.DataFrame())

    assert report["verdict"] == "INSUFFICIENT_DATA"
    assert report["production_logic_changed"] is False


def test_bark_instruction_evidence_excludes_observe_events_and_flags_missing_intent_audit():
    events = pd.DataFrame([
        {
            "signal_date": "2026-01-05", "event_time": "2026-01-05 15:00:00",
            "source": "bark", "code": "000001", "name": "测试",
            "strategy_type": "tv_dual_strict", "signal_close": 10.0,
            "trade_bucket": "TRADE", "trade_eligible": 1,
        },
        {
            "signal_date": "2026-01-05", "event_time": "2026-01-05 15:00:00",
            "source": "bark", "code": "000002", "name": "观察",
            "strategy_type": "tv_dual_strict", "signal_close": 10.0,
            "trade_bucket": "OBSERVE", "trade_eligible": 0,
        },
        {
            "signal_date": "2026-01-05", "event_time": "2026-01-05 15:05:00",
            "source": "bark_next_day", "code": "000001", "name": "测试",
            "strategy_type": "tv_dual_strict", "signal_close": 10.0,
            "trade_bucket": "TRADE", "trade_eligible": 1,
        },
    ])
    daily = pd.DataFrame([
        {
            "code": "000001", "日期": f"2026-01-{day:02d}",
            "开盘": price, "最高": price + 0.2, "最低": price - 0.2,
            "收盘": price, "成交量": 100000,
        }
        for day, price in zip(range(6, 11), (10.1, 10.2, 10.3, 10.4, 10.5))
    ])

    report = build_bark_instruction_evidence(
        events, daily, {"issued": 0, "states": {}, "items": []},
    )

    assert report["persisted_candidates"] == 3
    assert report["tradable_instructions"] == 1
    assert report["audited_delivered_instructions"] == 0
    assert report["filled"] == 0
    assert report["generated_instruction_shadow"]["filled"] == 1
    assert report["audit_gap"] is True
    assert report["status"] == "INSUFFICIENT_DATA"

    audited = build_bark_instruction_evidence(events, daily, {
        "issued": 1, "states": {"ISSUED": 1},
        "items": [{
            "signal_date": "2026-01-05", "event_time": "2026-01-05 15:00:00",
            "source": "bark", "code": "000001", "name": "测试",
            "strategy_type": "tv_dual_strict", "instruction": "可交易", "state": "ISSUED",
            "signal_close": 10.0, "pa_entry_price": 10.0, "pa_stop_price": 9.0,
        }],
    })
    assert audited["audited_delivered_instructions"] == 1
    assert audited["filled"] == 1
    assert audited["audit_gap"] is False

    next_day_only = build_bark_instruction_evidence(pd.DataFrame(), daily, {
        "issued": 1, "states": {"ISSUED": 1}, "items": [{
            "signal_date": "2026-01-05", "event_time": "2026-01-05 10:30:00",
            "source": "bark", "code": "000001", "name": "测试",
            "strategy_type": "tv_dual_strict", "instruction": "可交易", "state": "ISSUED",
            "signal_close": 10.0, "pa_entry_price": 10.0, "pa_stop_price": 9.0,
        }],
    })
    assert next_day_only["tradable_instructions"] == 0
    assert next_day_only["audited_delivered_instructions"] == 1
    assert next_day_only["filled"] == 1

    missing_price_audit = build_bark_instruction_evidence(pd.DataFrame(), daily, {
        "issued": 1, "states": {"ISSUED": 1}, "items": [{
            "signal_date": "2026-01-05", "event_time": "2026-01-05 10:30:00",
            "source": "bark", "code": "000002", "name": "缺计划价",
            "strategy_type": "tv_dual_strict", "instruction": "可交易", "state": "ISSUED",
            "signal_close": None, "pa_entry_price": None, "pa_stop_price": 9.0,
        }],
    })
    assert missing_price_audit["audited_delivered_instructions"] == 1
    assert missing_price_audit["missing_planned_entry"] == 1
    assert missing_price_audit["filled"] == 0

    actual_fill = build_bark_instruction_evidence(pd.DataFrame(), daily, {
        "issued": 1, "states": {"FILLED": 1}, "items": [{
            "signal_date": "2026-01-05", "event_time": "2026-01-05 10:30:00",
            "source": "bark", "code": "000001", "name": "真实成交",
            "strategy_type": "tv_dual_strict", "instruction": "可交易", "state": "FILLED",
            "signal_close": 10.0, "pa_entry_price": 10.0, "pa_stop_price": 9.0,
            "actual_price": 10.0, "filled_shares": 100,
        }],
    })
    assert actual_fill["actual_fill_evidence"]["fills"] == 1
    assert actual_fill["actual_fill_evidence"]["mature"] == 1
    assert actual_fill["actual_fill_evidence"]["metrics"]["avg_return"] > 0
    assert actual_fill["actual_fill_evidence"]["verdict"] == "INSUFFICIENT_DATA"

    adjustment_daily = daily.copy()
    adjustment_daily.loc[:, ["开盘", "最高", "最低", "收盘"]] = [30.0, 30.2, 29.8, 30.0]
    adjustment_gap = build_bark_instruction_evidence(pd.DataFrame(), adjustment_daily, {
        "issued": 1, "states": {"FILLED": 1}, "items": [{
            "signal_date": "2026-01-05", "event_time": "2026-01-05 10:30:00",
            "source": "bark", "code": "000001", "name": "复权断点",
            "strategy_type": "tv_dual_strict", "instruction": "可交易", "state": "FILLED",
            "signal_close": 10.0, "pa_entry_price": 10.0, "pa_stop_price": 9.0,
            "actual_price": 10.0, "filled_shares": 100,
        }],
    })
    assert adjustment_gap["actual_fill_evidence"]["mature"] == 0
    assert adjustment_gap["actual_fill_evidence"]["excluded_adjustment_gaps"] == 1


def test_evidence_shadow_reports_missed_winner_without_changing_current():
    candidate = _candidate("000003")
    candidate["price_action_detail"]["evidence_grade"] = "D"
    candidate["price_action_detail"]["evidence_status"] = "BLOCKED"
    candidates = pd.DataFrame([candidate])
    outcomes = pd.DataFrame([{
        "code": "000003", "signal_date": "2026-01-05", "strategy_type": "tv_dual_strict",
        "ret_5d": 6.0, "exec_filled": True, "exec_return_pct": 5.0,
    }])

    report = build_execution_replay_report(candidates, outcomes)
    current = next(item for item in report["policies"] if item["policy"] == "current_policy")
    evidence = next(item for item in report["policies"] if item["policy"] == "evidence_enforced_simulation")

    assert current["selected"] == 1
    assert evidence["selected"] == 0
    assert report["evidence_quality"]["attribution"]["RISK_GATE_MISSED_WINNER"] == 1


def test_persistent_b_shadow_requires_repeated_point_in_time_pushes_and_never_changes_current():
    first = _candidate("000004")
    second = _candidate("000004")
    for item, signal_date in ((first, "2026-01-05"), (second, "2026-01-06")):
        item["signal_date"] = signal_date
        item["sop_grade"] = "B"
        item["pct"] = 4.0
        # Keep this cohort outside the independent A-EOD production route;
        # this test isolates repeated-push shadow semantics only.
        item["price_action_score"] = 55
    outcomes = pd.DataFrame([{
        "code": "000004", "signal_date": "2026-01-06", "strategy_type": "tv_dual_strict",
        "ret_5d": 3.0,
    }])

    report = build_execution_replay_report(pd.DataFrame([first, second]), outcomes)
    persistent = next(item for item in report["policies"] if item["policy"] == "persistent_b_shadow")
    current = next(item for item in report["policies"] if item["policy"] == "current_policy")

    assert persistent["selected"] == 1
    assert current["selected"] == 0
    assert report["persistent_b_shadow"]["strict_b_candidates"] == 2
    assert report["persistent_b_shadow"]["failed_checks"]["repeated_push"] == 1
