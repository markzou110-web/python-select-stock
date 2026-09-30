"""手动部分平仓拆行落账回归测试。

修复：close_paper_trade 部分卖出只更新剩余股数、不落 CLOSED 行，三个熔断器
（日内/月度/连错）都读不到已实现亏损，手动操作成为绕过熔断口径的通道。
同时验证部分唯一索引（uq_paper_trade_code_date_open，仅 OPEN 行）下拆行
INSERT 不与原 OPEN 行撞键——旧的全量唯一索引会让风控减仓/手动部分卖出的
拆行 INSERT 报 IntegrityError。
"""
import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.db import init_db
from core.models import Base
from routers.paper_trade import PaperTradeClose, close_paper_trade

import routers.paper_trade as pt


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)
    return engine


def _open_trade(engine, code="600000", shares=100, entry_price=10.0):
    with engine.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO paper_trading
                (code, name, entry_price, entry_date, status, trade_mode, shares, capital_used)
            VALUES
                (:code, '测试', :entry_price, :entry_date, 'OPEN', 'SIMULATED', :shares, :capital)
        """), {
            "code": code, "entry_price": entry_price,
            "entry_date": datetime.now().strftime("%Y-%m-%d"),
            "shares": shares, "capital": round(entry_price * shares, 2),
        })
        return result.lastrowid


def test_manual_partial_close_splits_closed_row(monkeypatch):
    engine = _engine()
    trade_id = _open_trade(engine, shares=100)
    monkeypatch.setattr(pt, "get_db_engine", lambda: engine)
    monkeypatch.setattr(pt, "send_paper_trade_notification", lambda t, b: None)

    result = close_paper_trade(trade_id, PaperTradeClose(close_price=9.0, close_shares=40))
    assert result["status"] == "success"
    assert result["partial"] is True
    assert result["remaining_shares"] == 60

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status, shares, capital_used, close_source, closed_by, close_price "
                 "FROM paper_trading WHERE code='600000' ORDER BY id")
        ).fetchall()
    assert len(rows) == 2
    open_row, closed_row = rows
    assert open_row[0] == "OPEN" and open_row[1] == 60
    assert open_row[2] == 600.0  # 剩余资金同步缩减
    assert closed_row[0] == "CLOSED" and closed_row[1] == 40
    assert closed_row[2] == 400.0  # 已卖出部分的资金落账，熔断器可见
    assert closed_row[3] == "manual_partial" and closed_row[4] == "user"
    assert closed_row[5] == 9.0


def test_partial_open_unique_index_allows_split_rows_blocks_open_dup():
    engine = _engine()
    _open_trade(engine, code="600001")
    with engine.begin() as conn:
        # 同 code 同 entry_date 的 CLOSED 拆行：合法
        conn.execute(text("""
            INSERT INTO paper_trading (code, name, entry_price, entry_date, status, trade_mode, shares, close_date)
            VALUES ('600001', '测试', 10.0, :d, 'CLOSED', 'SIMULATED', 30, :d)
        """), {"d": datetime.now().strftime("%Y-%m-%d")})
        # 同键再开一条 OPEN：被部分唯一索引拦截
        dup = conn.execute(text("""
            INSERT INTO paper_trading (code, name, entry_price, entry_date, status, trade_mode, shares)
            VALUES ('600001', '测试', 10.0, :d, 'OPEN', 'SIMULATED', 100)
            ON CONFLICT (code, entry_date) WHERE status = 'OPEN' DO NOTHING
        """), {"d": datetime.now().strftime("%Y-%m-%d")})
        assert dup.rowcount == 0


def test_full_close_still_works_under_partial_index(monkeypatch):
    engine = _engine()
    trade_id = _open_trade(engine, code="600002")
    monkeypatch.setattr(pt, "get_db_engine", lambda: engine)
    monkeypatch.setattr(pt, "send_paper_trade_notification", lambda t, b: None)

    result = close_paper_trade(trade_id, PaperTradeClose(close_price=11.0))
    assert result["status"] == "success"
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status FROM paper_trading WHERE code='600002'")
        ).fetchall()
    assert len(rows) == 1 and rows[0][0] == "CLOSED"
