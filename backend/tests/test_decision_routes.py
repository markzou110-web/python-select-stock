"""
Tests for the decision-loop API surfaces added around review, watchlist, and templates.
These stay DB-light so they can run in local environments without PostgreSQL.
"""

import pytest
import os
import sys
import asyncio
import json
import numpy as np
import pandas as pd
from fastapi import HTTPException
from kombu.utils.json import dumps
from sqlalchemy import create_engine
from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core import audit_log
from routers import review, scan, watchlist, strategy_templates


def test_decision_tables_are_registered():
    table_names = set(Base.metadata.tables.keys())
    assert "watchlist" in table_names
    assert "strategy_templates" in table_names
    assert "recommendation_events" in table_names
    assert {"theme", "rise_logic"} <= set(Base.metadata.tables["watchlist"].columns.keys())
    assert {"theme", "rise_logic"} <= set(Base.metadata.tables["paper_trading"].columns.keys())


def test_review_returns_empty_payload_without_database(monkeypatch):
    monkeypatch.setattr(review, "get_db_engine", lambda: None)

    payload = review.get_scan_performance()

    assert payload["summary"]["signals"] == 0
    assert payload["horizons"] == []
    assert payload["by_strategy"] == []

    dashboard = review.get_profitability_dashboard()
    assert dashboard["summary"]["layers"] == 0
    assert "数据库未连接" in dashboard["notes"]

    calibration = review.get_strategy_calibration_report()
    assert calibration["summary"]["signals"] == 0
    assert calibration["blocker_analysis"]["summary"]["blockers"] == 0


def test_strategy_calibration_report_exposes_grade_and_blocker_analysis(monkeypatch):
    frame = pd.DataFrame([
        {
            "code": "000001",
            "signal_date": "2026-07-01",
            "strategy_type": "tv_dual_strict",
            "sop_grade": "A",
            "trade_bucket": "TRADE",
            "market_regime": "OFFENSIVE",
            "score_model_version": "v1",
            "ret_1d": 1.0,
            "ret_3d": 2.0,
            "ret_5d": 3.0,
            "ret_10d": None,
            "blockers": [],
        },
        {
            "code": "000002",
            "signal_date": "2026-07-01",
            "strategy_type": "tv_dual",
            "sop_grade": "C",
            "trade_bucket": "OBSERVE",
            "market_regime": "CRITICAL",
            "score_model_version": "v1",
            "ret_1d": -1.0,
            "ret_3d": -2.0,
            "ret_5d": -3.0,
            "ret_10d": None,
            "blockers": ["未站上确认价"],
        },
    ])
    monkeypatch.setattr(review, "get_db_engine", lambda: object())
    monkeypatch.setattr(review, "load_scan_outcomes", lambda *_args, **_kwargs: frame)

    payload = review.get_strategy_calibration_report(days=60, min_grade_samples=1, min_blocker_samples=1)

    assert payload["summary"]["days"] == 60
    assert payload["summary"]["mature_5d"] == 2
    assert {row["value"] for row in payload["by_grade"]} == {"A", "C"}
    assert payload["blocker_analysis"]["summary"]["blockers"] == 1


def test_profitability_layers_include_early_and_bark():
    scan_df = pd.DataFrame([
        {
            "signal_date": "2026-07-01",
            "performance_eligible": True,
            "trade_eligible": "false",
            "trade_bucket": "OBSERVE",
            "early_trade_candidate": True,
            "early_trade_grade": "A-",
            "strategy_type": "tv_dual_strict",
            "sector_alignment_score": 88,
            "ret_1d": 1.0,
            "ret_3d": 2.0,
            "ret_5d": 3.0,
            "ret_10d": 4.0,
        },
        {
            "signal_date": "2026-07-01",
            "performance_eligible": True,
            "trade_eligible": "true",
            "trade_bucket": "TRADE",
            "early_trade_candidate": False,
            "early_trade_grade": "",
            "strategy_type": "tv_dual",
            "sector_alignment_score": 20,
            "ret_1d": -1.0,
            "ret_3d": -2.0,
            "ret_5d": -3.0,
            "ret_10d": -4.0,
        },
    ])
    event_df = pd.DataFrame([
        {
            "source": "bark",
            "event_date": "2026-07-01",
            "ret_1d": 0.5,
            "ret_3d": 1.5,
            "ret_5d": 2.5,
            "ret_10d": 3.5,
        }
    ])

    layers = {row["key"]: row for row in review._build_profitability_layers(scan_df, event_df)}

    assert layers["early_a_minus"]["metrics"]["5d"]["signals"] == 1
    assert layers["early_a_minus"]["metrics"]["5d"]["avg_return"] == 3.0
    assert layers["trade_a"]["metrics"]["5d"]["avg_return"] == -3.0
    assert layers["bark"]["source"] == "recommendation_events"
    assert layers["bark"]["metrics"]["5d"]["avg_return"] == 2.5


def test_bark_success_profile_extracts_common_winning_features():
    event_df = pd.DataFrame([
        {
            "source": "bark",
            "strategy_type": "tv_dual_strict",
            "trade_bucket": "TRADE",
            "pa_trade_setup": "H2二次入场",
            "sector_phase": "SECTOR_CONFIRM",
            "sector_strength_score": 82,
            "stock_sector_fit_score": 75,
            "market_regime": "OFFENSIVE",
            "ret_5d": 4.0,
        },
        {
            "source": "bark_intraday",
            "strategy_type": "tv_dual_strict",
            "trade_bucket": "TRADE",
            "pa_trade_setup": "H2二次入场",
            "sector_phase": "SECTOR_CONFIRM",
            "sector_strength_score": 85,
            "stock_sector_fit_score": 72,
            "market_regime": "OFFENSIVE",
            "ret_5d": 2.0,
        },
        {
            "source": "scan",
            "strategy_type": "tv_dual",
            "trade_bucket": "OBSERVE",
            "pa_trade_setup": "震荡观察",
            "sector_phase": "SECTOR_FADE",
            "market_regime": "DEFENSIVE",
            "ret_5d": -3.0,
        },
    ])

    profile = review._build_bark_success_profile(event_df)

    assert profile["sample"] == 2
    assert profile["win_rate_5d"] == 100.0
    values = {(row["feature"], row["value"]) for row in profile["features"]}
    assert ("strategy_type", "tv_dual_strict") in values
    assert ("pa_trade_setup", "H2二次入场") in values
    assert ("sector_strength_bucket", "强板块>70") in values
    assert ("stock_sector_fit_bucket", "强适配>60") in values


def test_recommendation_outcome_loop_summarizes_sources_and_horizons():
    event_df = pd.DataFrame([
        {
            "source": "bark",
            "signal_date": "2026-07-01",
            "code": "000001",
            "name": "样本A",
            "strategy_type": "tv_dual_strict",
            "price": 10.0,
            "trade_bucket": "TRADE",
            "pa_trade_setup": "H2二次入场",
            "ret_1d": 1.0,
            "ret_3d": 2.0,
            "ret_5d": 3.0,
            "ret_10d": 4.0,
        },
        {
            "source": "scan",
            "signal_date": "2026-07-02",
            "code": "000002",
            "name": "样本B",
            "strategy_type": "tv_dual",
            "price": 20.0,
            "trade_bucket": "WATCH",
            "pa_trade_setup": "震荡观察",
            "ret_1d": -1.0,
            "ret_3d": -2.0,
            "ret_5d": -3.0,
            "ret_10d": -4.0,
        },
    ])

    payload = review._build_recommendation_outcome_loop(event_df)

    assert payload["summary"]["events"] == 2
    assert payload["summary"]["mature_5d"] == 2
    assert payload["summary"]["best_source"] == "bark"
    assert payload["horizons"]["5d"]["avg_return"] == 0.0
    sources = {row["source"]: row for row in payload["by_source"]}
    assert sources["bark"]["metrics"]["5d"]["avg_return"] == 3.0
    assert sources["scan"]["metrics"]["5d"]["avg_return"] == -3.0
    assert payload["recent_events"][0]["code"] == "000002"


def test_recommendation_outcome_loop_builds_adjustment_advice():
    rows = []
    for idx in range(10):
        rows.append({
            "source": "bark",
            "signal_date": f"2026-07-{idx + 1:02d}",
            "code": f"000{idx:03d}",
            "name": "强样本",
            "strategy_type": "tv_dual_strict",
            "price": 10.0,
            "ret_1d": 0.5,
            "ret_3d": 1.0,
            "ret_5d": 1.2,
            "ret_10d": 2.0,
        })
        rows.append({
            "source": "scan",
            "signal_date": f"2026-07-{idx + 1:02d}",
            "code": f"001{idx:03d}",
            "name": "弱样本",
            "strategy_type": "tv_dual",
            "price": 10.0,
            "ret_1d": -0.5,
            "ret_3d": -1.0,
            "ret_5d": -1.2,
            "ret_10d": -2.0,
        })

    payload = review._build_recommendation_outcome_loop(pd.DataFrame(rows))
    source_adjustments = {row["value"]: row for row in payload["adjustments"]["by_source"]}
    strategy_adjustments = {row["value"]: row for row in payload["adjustments"]["by_strategy"]}

    assert source_adjustments["bark"]["action"] == "BOOST"
    assert source_adjustments["scan"]["action"] == "DOWNWEIGHT"
    assert strategy_adjustments["tv_dual"]["score_delta"] < 0
    assert payload["summary"]["boost_count"] >= 1
    assert payload["summary"]["downweight_count"] >= 1


def test_high_open_buyability_waits_for_pullback_or_confirms_stand():
    waiting = review._high_open_buyability(5.0, 10.0, 9.4, 9.8, 10.2, 10.4, 6.1)
    confirmed = review._high_open_buyability(5.0, 10.0, 9.4, 9.8, 9.95, 10.15, 3.6)
    failed = review._high_open_buyability(5.0, 10.0, 9.4, 9.8, 9.2, 9.6, -2.0)
    limit_open = review._high_open_buyability(9.8, 10.0, 9.4, 9.8, 10.2, 10.4, 9.8)
    extended = review._high_open_buyability(6.0, 10.0, 9.4, 9.8, 10.2, 10.4, 6.1)

    assert waiting["state"] == "WAIT_PULLBACK"
    assert waiting["gap_bucket"] == "高开3~5%"
    assert waiting["buyable"] is False
    assert confirmed["state"] == "CONFIRMED"
    assert confirmed["buyable"] is True
    assert failed["state"] == "FAILED"
    assert "破坏结构" in failed["risk"]
    assert limit_open["state"] == "LIMIT_OPEN"
    assert limit_open["gap_bucket"] == "涨停/一字高开"
    assert limit_open["buyable"] is False
    assert extended["gap_bucket"] == "高开5~9.5%"


def test_real_trade_execution_review_reports_execution_gap_and_diagnostics():
    real_df = pd.DataFrame([
        {
            "ret_5d": -4.0,
            "planned_entry_price": 10.0,
            "actual_entry_price": 10.8,
            "entry_slippage_pct": None,
            "plan_adherence": "UNKNOWN",
            "entry_source": "manual_current_price",
            "entry_signal_date": None,
        },
        {
            "ret_5d": 2.0,
            "planned_entry_price": 10.0,
            "actual_entry_price": 9.9,
            "entry_slippage_pct": None,
            "plan_adherence": "FOLLOWED",
            "entry_source": "bark",
            "entry_signal_date": "2026-07-01",
        }
    ])
    event_df = pd.DataFrame([
        {"source": "bark", "ret_5d": 3.0},
        {"source": "bark_next_day", "ret_5d": 5.0},
    ])

    review_payload = review._build_real_trade_execution_review(real_df, event_df)

    assert review_payload["execution_gap_5d"] == -5.0
    assert review_payload["avg_entry_slippage_pct"] == 3.5
    assert review_payload["unknown_plan_adherence"] == 1
    assert review_payload["missing_signal_date"] == 1
    assert review_payload["high_slippage_trades"] == 1
    assert review_payload["execution_quality_score"] < 100
    assert {row["entry_source"] for row in review_payload["by_entry_source"]} == {"manual_current_price", "bark"}
    assert any(row["slippage_bucket"] == "严重滑点>2%" for row in review_payload["by_slippage_bucket"])
    assert any("跑输系统Bark样本" in item for item in review_payload["diagnostics"])


def test_daily_strategy_report_endpoint_builds_summary(monkeypatch):
    monkeypatch.setattr(review, "get_scan_dates", lambda: ["2026-07-07"])
    monkeypatch.setattr(review, "get_scan_history_by_date", lambda date: [
        {"代码": "000001", "名称": "一号", "行业": "机器人", "trade_bucket": "TRADE", "trade_eligible": True, "final_trade_score": 91},
        {"代码": "000002", "名称": "二号", "行业": "机器人", "trade_bucket": "OBSERVE", "final_trade_score": 72},
    ])
    monkeypatch.setattr(review, "_count_recommendation_events_for_date", lambda date: 1)

    from routers import market
    monkeypatch.setattr(market, "get_sector_push_gaps", lambda limit=8, date="": {
        "items": [{
            "industry": "机器人",
            "has_push_candidate": False,
            "primary_reason_label": "候选偏后排，暂不追",
        }]
    })

    payload = review.get_daily_strategy_report()

    assert payload["scan_date"] == "2026-07-07"
    assert payload["summary"]["scan_count"] == 2
    assert payload["summary"]["trade_count"] == 1
    assert payload["summary"]["bark_push_count"] == 1
    assert "机器人：候选偏后排，暂不追" in payload["body"]


def test_daily_ops_review_groups_execution_decisions(monkeypatch):
    monkeypatch.setattr(review, "get_db_engine", lambda: None)
    monkeypatch.setattr(
        review,
        "get_next_day_followup",
        lambda date=None, limit=80: {
            "date": "2026-07-02",
            "summary": {"tracked": 4},
            "items": [
                {
                    "code": "000001",
                    "name": "确认票",
                    "execution_action": "尾盘确认可试：小仓、贴近入场线",
                    "followup_status": "触发入场线",
                    "entry_line": 10.2,
                    "stop_line": 9.6,
                    "max_gain_pct": 4.2,
                    "score": 88,
                },
                {
                    "code": "000002",
                    "name": "回踩票",
                    "execution_action": "观察：等回踩/放量站稳",
                    "followup_status": "触发观察",
                    "entry_line": 8.1,
                    "stop_line": 7.5,
                    "trade_bucket": "WATCH",
                    "trade_blockers": "涨幅偏高且质量未确认",
                },
                {
                    "code": "000003",
                    "name": "追高票",
                    "execution_action": "不追：高开超过3%，等回踩",
                    "followup_status": "大涨验证",
                    "trade_bucket": "BLOCK",
                },
                {
                    "code": "000004",
                    "name": "失效票",
                    "execution_action": "取消：已触发风控线",
                    "followup_status": "风控触发",
                    "stop_line": 6.6,
                },
            ],
        },
    )

    payload = review.get_daily_ops_review(date="2026-07-02")

    assert payload["summary"]["bucket_counts"]["confirm_candidate"] == 1
    assert payload["summary"]["bucket_counts"]["wait_pullback"] == 1
    assert payload["summary"]["bucket_counts"]["no_chase"] == 1
    assert payload["summary"]["bucket_counts"]["invalidated"] == 1
    assert "站稳确认价 10.2" in payload["groups"]["confirm_candidate"][0]["ops_instruction"]
    assert any("不追高" in item for item in payload["suggestions"])


def test_missed_strong_review_excludes_selected_codes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO stock_basic (code, name, industry)
            VALUES ('000001', '已选强势', '测试'), ('000002', '漏选强势', '测试')
        """))
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close, vol)
            VALUES
            ('000001', '2026-07-01', 10, 10, 10, 10, 100),
            ('000001', '2026-07-02', 11, 11, 11, 11, 100),
            ('000002', '2026-07-01', 10, 10, 10, 10, 100),
            ('000002', '2026-07-02', 10.8, 10.8, 10.8, 10.8, 100)
        """))

    # SQLite path intentionally skips the SQL window query used in production.
    assert review._find_missed_strong_stocks(engine, "2026-07-02", {"000001"}) == []


def test_scan_history_does_not_fetch_market_snapshot_when_cache_is_empty(monkeypatch):
    monkeypatch.setattr(scan, "get_scan_history_by_date", lambda date: [{"代码": "000001", "现价": 10}])
    monkeypatch.setattr(scan, "get_cached_data", lambda *args: None)
    monkeypatch.setattr(scan, "get_stale_cache", lambda *args: None)
    monkeypatch.setattr(
        scan,
        "get_market_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("history initialization must not fetch live snapshot")),
    )

    result = asyncio.run(scan.get_history_results("2026-06-12"))

    assert result == [{"代码": "000001", "现价": 10}]


def test_scan_task_result_is_celery_json_serializable(monkeypatch):
    monkeypatch.setattr(
        "core.scanner.perform_market_scan",
        lambda **kwargs: [{"代码": "000001", "pa_volume_confirmed": np.bool_(True)}],
    )

    result = scan.run_market_scan_task(strategy_type="squeeze")

    assert result[0]["pa_volume_confirmed"] is True
    dumps(result)


def test_scan_task_combines_multiple_strategies_and_records_matches(monkeypatch):
    calls = []

    def fake_scan(**kwargs):
        strategy = kwargs["strategy_type"]
        calls.append((strategy, kwargs["publish_to_sentinel"]))
        score = 70 if strategy == "consensus" else 60
        return [{"代码": "000001", "名称": "平安银行", "strategy_type": strategy, "Score": score}]

    monkeypatch.setattr("core.scanner.perform_market_scan", fake_scan)
    monkeypatch.setattr(scan, "record_lifecycle_event", lambda *args, **kwargs: None)

    result = scan.run_market_scan_task(
        strategy_type="h2",
        strategy_types="h2,consensus",
    )

    assert calls == [("h2", False), ("consensus", False)]
    assert len(result) == 1
    assert result[0]["strategy_type"] == "consensus"
    assert result[0]["Score"] == 70
    assert result[0]["matched_strategies"] == ["h2", "consensus"]


def test_watchlist_rejects_invalid_stock_code():
    with pytest.raises(HTTPException) as exc:
        watchlist.add_watchlist_item({"code": "abc", "watch_price": 10})

    assert exc.value.status_code == 400


def test_watchlist_persists_theme_and_rise_logic(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(watchlist, "get_db_engine", lambda: engine)
    monkeypatch.setattr(watchlist, "_latest_prices", lambda *_: {})

    result = watchlist.add_watchlist_item({
        "code": "000001",
        "name": "平安银行",
        "industry": "银行",
        "watch_price": 10,
        "theme": "中特估",
        "rise_logic": "银行板块放量走强",
    })
    payload = watchlist.list_watchlist()

    assert result["status"] == "success"
    assert payload["items"][0]["theme"] == "中特估"
    assert payload["items"][0]["rise_logic"] == "银行板块放量走强"


def test_watchlist_active_includes_watching_and_triggered_only(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO watchlist (
                code, name, source, strategy_type, watch_price, status
            )
            VALUES
                ('000001', '观察中股票', 'manual', 'squeeze', 10, 'WATCHING'),
                ('000002', '已触发股票', 'manual', 'squeeze', 10, 'TRIGGERED'),
                ('000003', '已归档股票', 'manual', 'squeeze', 10, 'ARCHIVED'),
                ('000004', '已失效股票', 'manual', 'squeeze', 10, 'INVALIDATED')
        """))

    monkeypatch.setattr(watchlist, "get_db_engine", lambda: engine)
    monkeypatch.setattr(watchlist, "_latest_prices", lambda *_: {})

    payload = watchlist.list_watchlist(status="ACTIVE")

    assert {item["status"] for item in payload["items"]} == {"WATCHING", "TRIGGERED"}
    assert payload["stats"]["total"] == 2


def test_watchlist_decision_waits_for_price_trigger_when_setup_is_ready():
    item = {
        "target_hit": False,
        "stop_hit": False,
        "pa_trade_action": "READY",
        "pl_pct": 1.2,
        "current_price": 10,
        "target_price": 10.3,
        "stop_price": 9.5,
    }

    result = watchlist._watch_decision(item)

    assert result["decision"] == "READY_WAIT"
    assert "仍需站稳触发价" in result["action"]
    assert "未触发不买" in result["action"]


def test_sector_watch_performance_groups_current_state(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    signal_date = (pd.Timestamp.now().normalize() - pd.Timedelta(days=10)).date()
    created_at = f"{signal_date}T15:10:00"

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO watchlist (
                code, name, industry, source, strategy_type, watch_price, target_price,
                stop_price, status, reason, theme, created_at, updated_at
            )
            VALUES
                ('000001', '触发票', '机器人', 'sector_push_gap', 'sector_watch', 10, 11, 9, 'WATCHING', '题材观察', '机器人', :created_at, :created_at),
                ('000002', '回踩票', '机器人', 'sector_push_gap', 'sector_watch', 10, 12, 9, 'WATCHING', '题材观察', '机器人', :created_at, :created_at)
        """), {"created_at": created_at})
        rows = []
        closes = {
            "000001": [10.2, 10.5, 10.8, 11.0, 11.2],
            "000002": [10.1, 10.2, 10.3, 10.4, 10.5],
        }
        for code, values in closes.items():
            for idx, close in enumerate(values, start=1):
                d = (pd.Timestamp(signal_date) + pd.Timedelta(days=idx)).date()
                rows.append({
                    "code": code,
                    "date": str(d),
                    "open": close,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "vol": 1000,
                })
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close, vol)
            VALUES (:code, :date, :open, :high, :low, :close, :vol)
        """), rows)

    monkeypatch.setattr(review, "get_db_engine", lambda: engine)

    payload = review.get_sector_watch_performance(days=30)
    labels = {row["label"]: row for row in payload["by_state"]}

    assert payload["summary"]["items"] == 2
    assert payload["summary"]["mature_5d"] == 2
    assert "已触发" in labels
    assert "涨幅偏高等回踩" in labels
    assert labels["已触发"]["metrics"]["5d"]["avg_return"] == 12.0
    assert labels["涨幅偏高等回踩"]["metrics"]["5d"]["avg_return"] == 5.0


def test_watchlist_list_returns_theme_priority_note_and_sorts(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO watchlist (
                code, name, industry, source, strategy_type, watch_price, target_price,
                stop_price, status, reason, theme
            )
            VALUES
                ('000001', '弱状态票', '机器人', 'sector_push_gap', 'sector_watch', 10, 12, 9, 'WATCHING', '题材观察', '机器人'),
                ('000002', '强状态票', '机器人', 'sector_push_gap', 'sector_watch', 10, 12, 9, 'WATCHING', '题材观察', '机器人')
        """))

    monkeypatch.setattr(watchlist, "get_db_engine", lambda: engine)
    monkeypatch.setattr(watchlist, "_latest_prices", lambda *_: {
        "000001": {"price": 10.5, "date": "2026-07-07"},
        "000002": {"price": 10.1, "date": "2026-07-07"},
    })
    monkeypatch.setattr(watchlist, "_load_theme_state_priority_boosts", lambda *args, **kwargs: {
        "PULLBACK_CONFIRMED": 25,
        "WAIT_PULLBACK": -25,
    })
    monkeypatch.setattr(watchlist, "_sector_watch_state", lambda item: (
        {
            "state": "PULLBACK_CONFIRMED",
            "label": "回踩放量确认",
            "action": "尾盘站稳确认价后再复核",
        }
        if item["code"] == "000002"
        else {
            "state": "WAIT_PULLBACK",
            "label": "涨幅偏高等回踩",
            "action": "等待回踩不破支撑后再确认",
        }
    ))

    payload = watchlist.list_watchlist()
    items = payload["items"]

    assert items[0]["code"] == "000002"
    assert "状态复盘加权" in items[0]["theme_priority_note"]
    assert "状态复盘降权" in items[1]["theme_priority_note"]


def test_watchlist_theme_state_audit_dedupes_same_state(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(audit_log, "get_db_engine", lambda: engine)

    first = audit_log.record_watchlist_theme_state_change(
        watchlist_id=1,
        code="000001",
        name="题材票",
        strategy_type="sector_watch",
        theme="机器人",
        state="APPROACH_CONFIRM",
        label="接近确认价",
        action="等待放量站稳",
        current_price=10.5,
        watch_price=10.0,
    )
    duplicate = audit_log.record_watchlist_theme_state_change(
        watchlist_id=1,
        code="000001",
        name="题材票",
        strategy_type="sector_watch",
        theme="机器人",
        state="APPROACH_CONFIRM",
        label="接近确认价",
        action="等待放量站稳",
        current_price=10.6,
        watch_price=10.0,
    )
    changed = audit_log.record_watchlist_theme_state_change(
        watchlist_id=1,
        code="000001",
        name="题材票",
        strategy_type="sector_watch",
        theme="机器人",
        state="PULLBACK_CONFIRMED",
        label="回踩放量确认",
        action="尾盘站稳再复核",
        current_price=10.2,
        watch_price=10.0,
    )

    with engine.connect() as conn:
        count = conn.execute(text("""
            SELECT COUNT(*)
            FROM lifecycle_events
            WHERE event_type = 'WATCHLIST_THEME_STATE_CHANGED'
        """)).scalar()

    assert first is True
    assert duplicate is False
    assert changed is True
    assert count == 2


def test_sector_watch_performance_prefers_state_events(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    signal_date = (pd.Timestamp.now().normalize() - pd.Timedelta(days=10)).date()
    event_time = f"{signal_date}T15:10:00"
    payload = {
        "state": "APPROACH_CONFIRM",
        "label": "接近确认价",
        "action": "等待放量站稳",
        "current_price": 10.0,
        "watch_price": 9.8,
    }
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO lifecycle_events (
                event_time, event_type, source, code, name, watchlist_id,
                strategy_type, theme, payload
            ) VALUES (
                :event_time, 'WATCHLIST_THEME_STATE_CHANGED', 'watchlist', '000001',
                '事件票', 1, 'sector_watch', '机器人', :payload
            )
        """), {"event_time": event_time, "payload": json.dumps(payload, ensure_ascii=False)})
        rows = []
        for idx, close in enumerate([10.1, 10.2, 10.3, 10.4, 10.5], start=1):
            d = (pd.Timestamp(signal_date) + pd.Timedelta(days=idx)).date()
            rows.append({
                "code": "000001",
                "date": str(d),
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "vol": 1000,
            })
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close, vol)
            VALUES (:code, :date, :open, :high, :low, :close, :vol)
        """), rows)

    monkeypatch.setattr(review, "get_db_engine", lambda: engine)

    payload = review.get_sector_watch_performance(days=30)
    labels = {row["label"]: row for row in payload["by_state"]}

    assert payload["source"] == "state_events"
    assert payload["summary"]["items"] == 1
    assert labels["接近确认价"]["metrics"]["5d"]["avg_return"] == 5.0


def test_theme_state_priority_boosts_use_mature_event_returns(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    signal_date = (pd.Timestamp.now().normalize() - pd.Timedelta(days=10)).date()
    with engine.begin() as conn:
        events = []
        daily_rows = []
        for idx, code in enumerate(["000001", "000002", "000003"], start=1):
            events.append({
                "event_time": f"{signal_date}T15:10:00",
                "event_type": "WATCHLIST_THEME_STATE_CHANGED",
                "source": "watchlist",
                "code": code,
                "name": f"强样本{idx}",
                "watchlist_id": idx,
                "strategy_type": "sector_watch",
                "theme": "机器人",
                "payload": json.dumps({"state": "PULLBACK_CONFIRMED", "label": "回踩放量确认", "current_price": 10.0}, ensure_ascii=False),
            })
            for day, close in enumerate([10.1, 10.2, 10.3, 10.4, 10.8], start=1):
                d = (pd.Timestamp(signal_date) + pd.Timedelta(days=day)).date()
                daily_rows.append({"code": code, "date": str(d), "open": close, "high": close, "low": close, "close": close, "vol": 1000})
        for idx, code in enumerate(["000004", "000005", "000006"], start=4):
            events.append({
                "event_time": f"{signal_date}T15:10:00",
                "event_type": "WATCHLIST_THEME_STATE_CHANGED",
                "source": "watchlist",
                "code": code,
                "name": f"弱样本{idx}",
                "watchlist_id": idx,
                "strategy_type": "sector_watch",
                "theme": "机器人",
                "payload": json.dumps({"state": "WAIT_PULLBACK", "label": "涨幅偏高等回踩", "current_price": 10.0}, ensure_ascii=False),
            })
            for day, close in enumerate([9.9, 9.8, 9.7, 9.6, 9.5], start=1):
                d = (pd.Timestamp(signal_date) + pd.Timedelta(days=day)).date()
                daily_rows.append({"code": code, "date": str(d), "open": close, "high": close, "low": close, "close": close, "vol": 1000})
        conn.execute(text("""
            INSERT INTO lifecycle_events (
                event_time, event_type, source, code, name, watchlist_id,
                strategy_type, theme, payload
            ) VALUES (
                :event_time, :event_type, :source, :code, :name, :watchlist_id,
                :strategy_type, :theme, :payload
            )
        """), events)
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close, vol)
            VALUES (:code, :date, :open, :high, :low, :close, :vol)
        """), daily_rows)

    monkeypatch.setattr(watchlist, "get_db_engine", lambda: engine)

    boosts = watchlist._load_theme_state_priority_boosts(days=30, min_samples=3)

    assert boosts["PULLBACK_CONFIRMED"] > 0
    assert boosts["WAIT_PULLBACK"] < 0


def test_sector_watch_state_tracks_refined_buy_point_states():
    base = {
        "strategy_type": "sector_watch",
        "source": "sector_push_gap",
        "current_price": 10.0,
        "watch_price": 10.0,
        "target_price": 12.0,
        "stop_price": 9.2,
    }

    tracking = watchlist._sector_watch_state(base)
    approach_confirm = watchlist._sector_watch_state({**base, "current_price": 11.7})
    wait_pullback = watchlist._sector_watch_state({**base, "current_price": 10.5})
    pullback_needs_volume = watchlist._sector_watch_state({**base, "current_price": 10.1, "intraday_high": 10.6})
    pullback_confirmed = watchlist._sector_watch_state({
        **base,
        "current_price": 10.1,
        "intraday_high": 10.6,
        "pa_volume_confirmed": True,
    })
    triggered = watchlist._sector_watch_state({**base, "current_price": 12.1})
    invalidated = watchlist._sector_watch_state({**base, "stop_hit": True})

    assert tracking["state"] == "THEME_TRACKING"
    assert tracking["label"] == "题材跟踪中"
    assert approach_confirm["state"] == "APPROACH_CONFIRM"
    assert "不提前追" in approach_confirm["action"]
    assert wait_pullback["state"] == "WAIT_PULLBACK"
    assert "等待回踩不破支撑" in wait_pullback["action"]
    assert pullback_needs_volume["state"] == "PULLBACK_NEEDS_VOLUME"
    assert "量能未确认" in pullback_needs_volume["action"]
    assert pullback_confirmed["state"] == "PULLBACK_CONFIRMED"
    assert "量能确认" in pullback_confirmed["action"]
    assert triggered["state"] == "TRIGGERED"
    assert invalidated["state"] == "INVALIDATED"


def test_strategy_template_requires_name():
    with pytest.raises(HTTPException) as exc:
        strategy_templates.save_strategy_template({"params": {"strategy_type": "squeeze"}})

    assert exc.value.status_code == 400
