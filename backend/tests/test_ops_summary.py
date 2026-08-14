import os
import sys
from datetime import datetime

from sqlalchemy import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.db import save_failure_sample, save_scan_audit_log
from core.models import Base
from core.ops_summary import build_ops_summary


def test_ops_summary_aggregates_scans_and_failures():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    save_scan_audit_log({
        "started_at": datetime.now(),
        "finished_at": datetime.now(),
        "duration_sec": 2.0,
        "status": "SUCCESS",
        "strategy_type": "pine",
        "version_snapshot": {"strategy": "v1"},
        "params_snapshot": {"performance_phases_sec": {"market_snapshot_load": 0.5, "indicator_batch": 1.2}},
        "candidate_count": 20,
        "result_count": 4,
        "fail_reasons": {"量能不足": 6, "趋势不符": 2},
    }, engine)
    save_scan_audit_log({
        "started_at": datetime.now(),
        "finished_at": datetime.now(),
        "duration_sec": 4.0,
        "status": "FAILED",
        "strategy_type": "pine",
        "version_snapshot": {"strategy": "v1"},
        "candidate_count": 10,
        "result_count": 0,
        "fail_reasons": {"量能不足": 3},
    }, engine)
    save_scan_audit_log({
        "started_at": datetime.now(),
        "finished_at": datetime.now(),
        "duration_sec": 3.0,
        "status": "RESEARCH_ONLY",
        "strategy_type": "pine",
        "version_snapshot": {"strategy": "v1"},
        "candidate_count": 15,
        "result_count": 2,
        "fail_reasons": {},
    }, engine)
    save_failure_sample({
        "code": "000001",
        "sample_date": "2026-05-31",
        "strategy_type": "pine",
        "pnl_pct": -4.0,
        "reason": "假突破",
    }, engine)

    summary = build_ops_summary(engine, limit=20)

    assert summary["status"] == "ok"
    assert summary["scan_quality"]["total_scans"] == 3
    assert summary["scan_quality"]["success_rate"] == 33.3
    assert summary["scan_quality"]["completion_rate"] == 66.7
    assert summary["scan_quality"]["research_only_rate"] == 33.3
    assert summary["scan_quality"]["failure_rate"] == 33.3
    assert summary["scan_quality"]["status_distribution"] == {
        "FAILED": 1,
        "RESEARCH_ONLY": 1,
        "SUCCESS": 1,
    }
    assert summary["scan_quality"]["avg_duration_sec"] == 3.0
    assert summary["failure_reason_top"][0] == {"reason": "量能不足", "count": 9}
    assert summary["strategy_distribution"][0]["strategy_type"] == "pine"
    assert summary["strategy_distribution"][0]["avg_results"] == 2.0
    assert summary["failure_sample_by_strategy"][0]["avg_pnl_pct"] == -4.0
    assert summary["version_distribution"][0] == {"version": "strategy:v1", "count": 3}
    assert summary["performance_phases"][0] == {"phase": "indicator_batch", "avg_duration_sec": 1.2, "samples": 1}


def test_ops_summary_handles_empty_database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    summary = build_ops_summary(engine)

    assert summary["status"] == "ok"
    assert summary["scan_quality"]["total_scans"] == 0
    assert summary["failure_reason_top"] == []
    assert summary["strategy_distribution"] == []
