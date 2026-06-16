import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.research_summary import build_research_summary


def test_research_summary_aggregates_scan_history():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO scan_history (
                code, date, name, industry, strategy_type, score, win_rate,
                price_action_regime, price_action_signal, pa_trade_action, pa_trade_setup
            ) VALUES
            ('000001', '2026-05-31', '平安银行', '银行', 'pine', 88, '62%',
             '多头趋势', 'H2二次入场', 'BUY', '强H2'),
            ('000002', '2026-05-31', '万科A', '地产', 'pine', 72, '50%',
             '震荡区间', '区间下沿反转', 'WATCH', '区间反转'),
            ('000003', '2026-05-30', '招商银行', '银行', 'squeeze', 66, '55%',
             '多头趋势', '突破回踩', 'BUY', '突破回踩')
        """))
        conn.execute(text("""
            UPDATE scan_history
            SET price_action_detail = '{"research_eligible": true, "calibrated_score": 80}'
        """))

    summary = build_research_summary(engine)

    assert summary["status"] == "ok"
    assert summary["summary"]["total_signals"] == 3
    assert summary["summary"]["latest_date"] == "2026-05-31"
    assert summary["summary"]["avg_score"] == 80.0
    assert summary["summary"]["effective_signals"] == 3
    assert summary["by_strategy"][0]["strategy_type"] == "pine"
    assert summary["by_strategy"][0]["avg_win_rate"] == 56.0
    assert summary["by_industry"][0] == {"name": "银行", "count": 2}
    assert summary["by_regime"][0] == {"name": "多头趋势", "count": 2}
    assert any(item == {"name": "强信号", "count": 1} for item in summary["score_buckets"])


def test_research_summary_handles_empty_database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    summary = build_research_summary(engine)

    assert summary["status"] == "ok"
    assert summary["summary"]["total_signals"] == 0
    assert summary["by_strategy"] == []
