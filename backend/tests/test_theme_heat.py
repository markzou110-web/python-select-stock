"""题材热度看板测试（借鉴 easy-stock 主题热点页；SHADOW 研究评分）。

覆盖：heat_score 加权、collect 注入式采集（board/flow/hot/members 可编程）、
涨停/人气/MA20 统计、落库与榜单读取（趋势 new 标签）、API 端点。
"""
import os
import sys
from datetime import datetime

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.db import init_db
from core.models import Base
from core.theme_heat import heat_score, load_theme_board

BAR = "2026-09-30"


def _engine():
    # StaticPool：TestClient 在工作线程执行请求，内存 SQLite 必须跨线程共享连接
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    init_db(engine)
    return engine


def _seed_limit_up(engine):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO limit_up_events (event_date, code, name, industry, status, limit_up_streak, first_seen_at, last_seen_at) "
            "VALUES ('2026-09-30', '600001', '甲', '人工智能', 'SEALED', 3, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),"
            "('2026-09-30', '600002', '乙', '人工智能', 'SEALED', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))


def _fake_board():
    return pd.DataFrame([
        {"板块名称": "人工智能", "最新价": 1000.0, "涨跌幅": 3.2},
        {"板块名称": "内存", "最新价": 900.0, "涨跌幅": -1.1},
    ])


def _fake_flow(indicator, sector_type):
    rows = {"人工智能": 8.37, "内存": -88.71}
    return pd.DataFrame([{"名称": k, "主力净流入-净额": v * 1e8} for k, v in rows.items()])


def _fake_members(symbol):
    if symbol == "人工智能":
        return pd.DataFrame([{"代码": "600001"}, {"代码": "600002"}, {"代码": "600003"}])
    return pd.DataFrame([{"代码": "600009"}])


def test_heat_score_weighted_sum():
    score = heat_score(80, 90, 60, 70)
    assert score == round(0.35 * 80 + 0.30 * 90 + 0.20 * 60 + 0.15 * 70, 1)
    # 分量缺失回落中性 50
    assert heat_score(None, None, 0, None) == round(0.35 * 50 + 0.30 * 50 + 0.15 * 50, 1)


def test_collect_with_injected_fetchers_saves_ranked_board():
    from core.theme_heat import collect_theme_heat

    engine = _engine()
    _seed_limit_up(engine)
    result = collect_theme_heat(
        engine=engine, scope="CONCEPT", bar_date=BAR,
        fetchers={"board": _fake_board, "flow": _fake_flow,
                  "hot": lambda: pd.DataFrame([{"代码": "600001"}]),
                  "members": _fake_members},
    )
    assert result["saved"] == 2 and result["themes"] == 2
    board = load_theme_board(engine, scope="CONCEPT", bar_date=BAR)
    themes = {t["theme"]: t for t in board["themes"]}
    assert board["themes"][0]["theme"] == "人工智能"  # 涨停+人气+资金全面占优 → rank 1
    ai = themes["人工智能"]
    assert ai["trend"] == "new"
    assert ai["limit_up_count"] == 2 and ai["max_streak"] == 3
    assert ai["hot_overlap"] == 1
    assert ai["narrative"] and "涨停" in ai["narrative"]
    assert ai["members"] == ["600001", "600002", "600003"]


def test_theme_api_heat_and_members(monkeypatch):
    import routers.themes as themes_router

    engine = _engine()
    _seed_limit_up(engine)
    from core.theme_heat import collect_theme_heat

    collect_theme_heat(engine=engine, scope="CONCEPT", bar_date=BAR,
                       fetchers={"board": _fake_board, "flow": _fake_flow,
                                 "hot": lambda: pd.DataFrame([{"代码": "600001"}]),
                                 "members": _fake_members})
    monkeypatch.setattr(themes_router, "get_db_engine", lambda: engine)
    app = FastAPI()
    app.include_router(themes_router.router)
    client = TestClient(app)

    heat = client.get(f"/api/themes/heat?scope=CONCEPT&date={BAR}").json()
    assert heat["market_environment"]["stage"] in {"可交易", "普跌压制", "退潮"}
    assert heat["themes"][0]["theme"] == "人工智能"
    assert "market_environment" in heat

    members = client.get("/api/themes/members?theme=人工智能&scope=CONCEPT").json()
    assert members["members"] == ["600001", "600002", "600003"]
    assert client.get("/api/themes/members?theme=不存在主题").status_code == 404
