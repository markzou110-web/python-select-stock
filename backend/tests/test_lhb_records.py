"""龙虎榜日度记录测试（借鉴 easy-stock 行情总览；ReviewCenter 只读参考）。

覆盖：lhb_records 建表迁移、save upsert（同日同股多原因并存、原因截断）、
load 的日期/代码过滤、collect 复用 direct_sources 归一字段（fetcher 注入）、
review 路由的持仓/候选标注。
"""
import os
import sys
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.db import init_db, load_lhb_records, save_lhb_records
from core.lhb_records import collect_lhb_records
from core.models import Base

# 事件日相对"今天"取，days 过滤用真实 now 计算，保证窗口覆盖
DAY2 = (date.today() - timedelta(days=1)).isoformat()
DAY1 = (date.today() - timedelta(days=2)).isoformat()


def _engine():
    # StaticPool + check_same_thread=False：TestClient 在工作线程执行请求，
    # 内存 SQLite 必须跨线程共享同一连接，否则读到空库
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    init_db(engine)  # 走真实迁移路径，验证 lhb_records 建表
    return engine


def _row(event_date, code, reason="日涨幅偏离值达7%的证券", net=1200.0):
    return {"event_date": event_date, "code": code, "name": f"股{code}",
            "reason": reason, "net_buy_wan": net, "buy_wan": 3000.0,
            "sell_wan": 1800.0, "pct_chg": 10.0}


def test_save_upsert_keeps_multiple_reasons_and_truncates_long_reason():
    engine = _engine()
    assert save_lhb_records([_row(DAY2, "600000"), _row(DAY2, "600001")], engine=engine) == 2
    # 同日同股不同上榜原因 → 两行并存
    assert save_lhb_records([_row(DAY2, "600000", reason="有价格涨跌幅限制的日换手率达到20%")], engine=engine) == 1
    # 同键重复写入 → upsert 不产生新行、更新净买
    assert save_lhb_records([_row(DAY2, "600000", net=1500.0)], engine=engine) == 1
    rows = load_lhb_records(engine, days=3)
    assert len(rows) == 3
    same_code = [row for row in rows if row["code"] == "600000"]
    assert len(same_code) == 2
    assert {row["net_buy_wan"] for row in same_code} == {1200.0, 1500.0}

    long_reason = "长" * 250
    save_lhb_records([_row(DAY2, "600002", reason=long_reason)], engine=engine)
    row = [r for r in load_lhb_records(engine, days=3) if r["code"] == "600002"][0]
    assert len(row["reason"]) == 200


def test_load_filters_by_codes_and_days():
    engine = _engine()
    save_lhb_records([_row(DAY1, "600000"), _row(DAY2, "600000"), _row(DAY2, "600001")], engine=engine)
    assert len(load_lhb_records(engine, days=1)) == 2  # 只取最近 1 个自然日
    codes = load_lhb_records(engine, days=7, codes=["600001"])
    assert [row["code"] for row in codes] == ["600001"]
    assert load_lhb_records(_engine()) == []


def _seed_daily_k(engine, dates):
    with engine.begin() as conn:
        for day in dates:
            conn.execute(text(
                "INSERT INTO daily_k (code, date, open, high, low, close, vol) "
                "VALUES ('600000', :d, 10, 11, 9, 10.5, 1000)"
            ), {"d": day})


def test_collect_uses_trade_dates_from_daily_k_and_saves_rows():
    engine = _engine()
    # 交易日相对"今天"取：load 的 days 窗口也按真实 now 计算，硬编码日期会随
    # 时间推移掉出窗口（本测试曾在 09-30 后第 8 天爆炸）
    day2 = date.today() - timedelta(days=1)
    day1 = date.today() - timedelta(days=2)
    _seed_daily_k(engine, [day1, day2])
    payloads = {
        day2.isoformat(): {"stocks": [{"code": "600000", "name": "甲", "reason": "日涨幅偏离",
                                       "net_buy_wan": 1200.0, "buy_wan": 3000.0,
                                       "sell_wan": 1800.0, "change_pct": 10.0}]},
        day1.isoformat(): {"stocks": [{"code": "600001", "name": "乙", "reason": "换手率达20%",
                                       "net_buy_wan": -500.0, "buy_wan": 800.0,
                                       "sell_wan": 1300.0, "change_pct": -3.0}]},
    }
    result = collect_lhb_records(engine=engine, days=2, fetcher=lambda d: payloads[d])
    assert result == {"saved": 2, "days": 2, "errors": 0}
    rows = load_lhb_records(engine, days=7)
    assert {row["code"] for row in rows} == {"600000", "600001"}
    assert rows[0]["net_buy_wan"] == 1200.0  # 同日按净买降序


def test_collect_falls_back_to_calendar_days_and_counts_errors():
    engine = _engine()  # daily_k 为空 → 回退自然日
    calls = []

    def fetcher(day):
        calls.append(day)
        if len(calls) == 1:
            raise RuntimeError("source down")
        return {"stocks": []}

    result = collect_lhb_records(engine=engine, days=3, fetcher=fetcher)
    assert result["days"] == 3
    assert result["errors"] == 1
    assert len(calls) == 3


def test_review_router_flags_positions_and_candidates(monkeypatch):
    import routers.review as review_router

    engine = _engine()
    save_lhb_records([_row(DAY2, "600000"), _row(DAY2, "600001")], engine=engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO paper_trading (code, name, entry_price, entry_date, status, trade_mode) "
            "VALUES ('600000', '甲', 10.0, '2026-09-25', 'OPEN', 'SIMULATED')"
        ))
        conn.execute(text(
            "INSERT INTO scan_history (code, date, score) VALUES ('600001', '2026-09-25', 90)"
        ))
    monkeypatch.setattr(review_router, "get_db_engine", lambda: engine)
    app = FastAPI()
    app.include_router(review_router.router)
    client = TestClient(app)
    response = client.get("/api/review/lhb-records?days=3")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["position_hits"] == 1
    by_code = {row["code"]: row for row in payload["records"]}
    assert by_code["600000"]["is_position"] is True
    assert by_code["600000"]["is_recent_candidate"] is False
    assert by_code["600001"]["is_recent_candidate"] is True
    assert by_code["600001"]["is_position"] is False
