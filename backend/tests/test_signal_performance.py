import os
import sys
from datetime import date, datetime, time, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.models import Base
from core.signal_performance import build_signal_performance_report, save_intraday_signal_snapshots


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE intraday_minute_bars (
                id INTEGER PRIMARY KEY,
                code VARCHAR(20) NOT NULL,
                bar_time VARCHAR(19) NOT NULL,
                open FLOAT,
                close FLOAT,
                high FLOAT,
                low FLOAT,
                volume FLOAT,
                amount FLOAT,
                average_price FLOAT,
                created_at TIMESTAMP,
                UNIQUE(code, bar_time)
            )
        """))
    return engine


def _candidate():
    return {
        "代码": "000001", "名称": "测试股", "strategy_type": "tv_dual_strict",
        "sop_grade": "M", "现价": 10.0, "pa_entry_price": 10.2, "pa_stop_price": 9.2,
        "trade_bucket": "OBSERVE", "trade_eligible": False,
        "execution_review_state": "STRONG_WATCH", "confirmation_reachability": "REACHABLE_TODAY",
        "strong_exception_shadow": True, "trade_blockers": ["市场退潮，暂停新增仓位"],
        "sop_base_grade": "A", "sop_quality_score": 72.0, "sop_quality_gap_to_a": 0,
        "sop_grade_reason": "质量分72.0，已达到A级分数线；基础A→最终M：强势观察",
        "sop_grade_transition_reasons": ["强势观察"],
        "sop_checks": ["盈亏比2.0"], "sop_bonuses": ["板块强动量"],
        "sop_risks": ["禁止追高"], "sop_vetoes": [],
    }


def test_signal_snapshot_is_append_only_and_idempotent_per_minute():
    engine = _engine()
    observed = datetime.combine(date.today() - timedelta(days=10), time(10, 0, 30))

    first = save_intraday_signal_snapshots([_candidate()], engine, "bark", observed)
    duplicate = save_intraday_signal_snapshots([{**_candidate(), "现价": 99}], engine, "bark", observed)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT signal_price FROM intraday_signal_snapshots")).scalar()
    assert first == 1
    assert duplicate == 0
    assert row == 10.0


def test_signal_snapshot_persists_frozen_plan_as_active_confirmation():
    engine = _engine()
    observed = datetime.combine(date.today() - timedelta(days=10), time(10, 0, 30))
    candidate = {
        **_candidate(), "execution_plan_frozen": True,
        "frozen_confirmation_price": 9.8, "frozen_stop_price": 9.0,
    }
    save_intraday_signal_snapshots([candidate], engine, "bark", observed)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT confirmation_price, stop_price FROM intraday_signal_snapshots"
        )).first()
    assert tuple(row) == (9.8, 9.0)


def test_signal_snapshot_persists_price_action_execution_tier():
    engine = _engine()
    observed = datetime.combine(date.today() - timedelta(days=10), time(15, 0))
    candidate = {
        **_candidate(),
        "pa_execution_policy_version": "pa-execution-tier-v1",
        "pa_execution_tier": "T1_CONFIRM",
        "pa_execution_tier_label": "次日确认",
        "pa_execution_hard_blocked": False,
    }

    save_intraday_signal_snapshots([candidate], engine, "bark_next_day", observed)
    with engine.connect() as conn:
        payload = conn.execute(text("SELECT snapshot_payload FROM intraday_signal_snapshots")).scalar()
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)

    assert payload["pa_execution_policy_version"] == "pa-execution-tier-v1"
    assert payload["pa_execution_tier"] == "T1_CONFIRM"


def test_signal_report_separates_timing_confirmation_and_gate_attribution():
    engine = _engine()
    signal_date = date.today() - timedelta(days=10)
    observed = datetime.combine(signal_date, time(10, 0))
    save_intraday_signal_snapshots([_candidate()], engine, "bark", observed)
    with engine.begin() as conn:
        bars = [
            (signal_date - timedelta(days=1), 9.5),
            (signal_date, 10.5),
            *[(signal_date + timedelta(days=i), 10.5 + i * 0.1) for i in range(1, 6)],
        ]
        for bar_date, close in bars:
            conn.execute(text("""
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                VALUES ('000001', :date, :close, :close, :close, :close, 100000)
            """), {"date": bar_date, "close": close})
            conn.execute(text("""
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                VALUES ('000002', :date, 10, 10, 10, 10, 100000)
            """), {"date": bar_date})
        conn.execute(text("""
            INSERT INTO intraday_minute_bars (code, bar_time, open, high, low, close, volume)
            VALUES ('000001', :bar_time, 10.0, 10.3, 9.9, 10.25, 10000)
        """), {"bar_time": observed + timedelta(minutes=5)})

    report = build_signal_performance_report(engine, days=30)
    selection = next(item for item in report["cohorts"] if item["cohort"] == "selection")

    assert report["summary"]["snapshot_events"] == 1
    assert report["validation"]["selection"] == {
        "status": "INSUFFICIENT_DATA", "mature_5d": 1, "required": 30,
    }
    assert report["validation"]["execution"]["mature_5d"] == 0
    assert report["validation"]["confirmation"]["triggered_samples"] == 1
    assert report["status"] == "INSUFFICIENT_DATA"
    assert selection["alert_to_close"]["avg_return"] == 5.0
    assert report["gate_attribution"]["CONFIRMATION_TRIGGERED"] == 1
    assert report["gate_attribution"]["RISK_GATE_MISSED_WINNER"] == 1
    assert report["strong_stock_coverage"]["top20_hits"] == 1
    assert report["items"][0]["sop_base_grade"] == "A"
    assert report["items"][0]["sop_quality_score"] == 72.0
    assert "基础A→最终M" in report["items"][0]["sop_grade_reason"]
    assert report["items"][0]["sop_risks"] == ["禁止追高"]
