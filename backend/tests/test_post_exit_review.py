"""Elder 退出后月度回顾任务测试。

_collect_post_exit_review_items：只取平仓满 60 天且尚未写过
source='post_exit_review' 日志事件的交易（按 trade_id 去重）。
"""
import os
import sys
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base, PaperTrading
from core.tasks import _collect_post_exit_review_items
from routers.paper_trade import _ensure_trade_journal_table

NOW = pd.Timestamp("2026-09-26")


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    _ensure_trade_journal_table(engine)
    return engine


def _add_trade(engine, code="600001", **overrides):
    defaults = dict(
        entry_price=10.0, close_price=11.0, shares=100,
        status="CLOSED", strategy_type="tv_dual",
        entry_date=date(2026, 1, 5), close_date=(NOW - pd.Timedelta(days=90)).date(),
    )
    defaults.update(overrides)
    session = sessionmaker(bind=engine)()
    trade = PaperTrading(code=code, **defaults)
    session.add(trade)
    session.commit()
    trade_id = trade.id
    session.close()
    return trade_id


def test_only_trades_closed_beyond_cutoff_are_collected():
    engine = _engine()
    old_id = _add_trade(engine)
    _add_trade(
        engine,
        code="600002",
        close_date=(NOW - pd.Timedelta(days=30)).date(),  # 平仓未满 60 天
    )
    items = _collect_post_exit_review_items(engine, now=NOW)
    assert [row["id"] for row in items] == [old_id]


def test_reviewed_trades_are_deduplicated_by_journal_event():
    engine = _engine()
    old_id = _add_trade(engine)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO trade_journal_events
                    (event_time, source, code, trade_id, advice, action_taken, result_note)
                VALUES
                    (:event_time, 'post_exit_review', '600001', :trade_id, 'a', 'b', 'c')
            """),
            {"event_time": NOW.to_pydatetime(), "trade_id": old_id},
        )
    assert _collect_post_exit_review_items(engine, now=NOW) == []


def test_empty_book_returns_empty_list():
    engine = _engine()
    assert _collect_post_exit_review_items(engine, now=NOW) == []
