import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.execution_audit import assess_persistent_b_shadow, load_recent_push_counts
from core.scanner import _inject_missing_fundamentals
from core.sentinel import _candidate_brief_lines


def test_supplement_candidate_receives_missing_fundamentals_without_overwriting_existing_values():
    candidate = {"代码": "300759", "ROE": None, "净利YOY": None}
    _inject_missing_fundamentals(candidate, {
        "300759": {"roe": 2.1, "net_profit_yoy": 9.75},
    })
    assert candidate["ROE"] == 2.1
    assert candidate["净利YOY"] == 9.75
    assert candidate["fundamental_data_source"] == "stock_fundamentals"

    candidate["ROE"] = 8.0
    _inject_missing_fundamentals(candidate, {"300759": {"roe": 99, "net_profit_yoy": 99}})
    assert candidate["ROE"] == 8.0


def test_recent_push_count_is_point_in_time_and_includes_current_day():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE scan_history (code TEXT, strategy_type TEXT, data_date TEXT, date TEXT)
        """))
        conn.execute(text("""
            INSERT INTO scan_history VALUES
            ('300759', 'tv_dual_strict', '2026-07-13', '2026-07-13'),
            ('300759', 'tv_dual_strict', '2026-07-14', '2026-07-14'),
            ('300759', 'tv_dual', '2026-07-14', '2026-07-14')
        """))
    counts = load_recent_push_counts(engine, ["300759"], "tv_dual_strict", "2026-07-15")
    assert counts == {"300759": 3}


def test_persistent_b_shadow_is_observation_only_and_rejects_chasing():
    candidate = {
        "strategy_type": "tv_dual_strict", "sop_grade": "B", "sop_quality_score": 65,
        "recent_push_days": 3, "trade_opportunity_score": 70, "pa_risk_reward": 2,
        "sector_alignment_score": 85, "涨幅%": 4, "trade_blockers": ["量能未确认"],
    }
    assessment = assess_persistent_b_shadow(candidate)
    assert assessment["eligible"] is True
    assert assessment["instruction"] == "影子验证，不可交易"

    candidate["涨幅%"] = 9
    extended = assess_persistent_b_shadow(candidate)
    assert extended["eligible"] is True
    assert extended["entry_mode"] == "WAIT_PULLBACK"
    assert extended["instruction"] == "影子等待回踩，不可交易"


def test_bark_labels_persistent_b_shadow_as_not_tradeable():
    stock = {
        "代码": "300759", "名称": "康龙化成", "现价": 41.09,
        "sop_grade": "B", "trade_eligible": False, "trade_bucket": "OBSERVE",
        "trade_blockers": ["量能未确认"], "persistent_b_shadow_eligible": True,
        "persistent_b_shadow": {"entry_mode": "WAIT_PULLBACK"}, "recent_push_days": 3,
    }
    body = "\n".join(_candidate_brief_lines(stock))
    assert body.startswith("指令：不可交易")
    assert "原因：量能未确认" in body
    assert len(body.splitlines()) == 3
