import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.execution_replay import build_execution_replay_report, run_historical_execution_replay


def _candidate(code: str, blockers_ready: bool = True):
    detail = {
        "research_eligible": True,
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


def test_replay_reports_negative_hard_only_shadow_without_changing_current_policy():
    candidate = _candidate("000002", blockers_ready=False)
    candidates = pd.DataFrame([candidate])
    outcomes = pd.DataFrame([{
        "code": "000002", "signal_date": "2026-01-05", "strategy_type": "tv_dual_strict",
        "ret_5d": -8.0,
    }])
    report = build_execution_replay_report(candidates, outcomes)
    current = next(item for item in report["policies"] if item["policy"] == "current_policy")
    assert current["selected"] == 0
    assert report["summary"]["point_in_time_candidates"] == 1


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
