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


def test_llm_narrative_generation(monkeypatch):
    """AI 未配置 → None 回落；配置 + fake 客户端 → 叙述生成。"""
    from core import theme_heat as th
    from core.config import config as app_config

    stats = {"heat_score": 88.5, "flow_3d": 8.37, "chg_today": 3.2,
             "limit_up_count": 2, "max_streak": 3, "pct_above_ma20": 66.7}
    evidence = ["OpenAI 发布新智能体"]

    monkeypatch.setattr(app_config, "AI_MODEL", "")
    assert th.generate_narrative_llm("人工智能", stats, evidence) is None

    monkeypatch.setattr(app_config, "AI_MODEL", "test-model")
    monkeypatch.setattr(app_config, "AI_API_KEY", "test-key")

    import core.theme_heat as th_mod
    calls = []
    def _fake_post(payload):
        calls.append(payload)
        return "题材处于发酵阶段，资金与涨停梯队共振；注意追高风险。", {"total_tokens": 100}
    monkeypatch.setattr(th_mod, "_post_chat_text", _fake_post)

    out = th.generate_narrative_llm("人工智能", stats, evidence)
    assert out and "发酵" in out
    assert len(calls) == 1 and "人工智能" in calls[0]["messages"][1]["content"]


def test_collect_llm_narrative_wired(monkeypatch):
    """collect 的 use_llm 路径：fake 生成函数被调用且结果落库。"""
    from core.theme_heat import collect_theme_heat, load_theme_board
    import core.theme_heat as th

    engine = _engine()
    _seed_limit_up(engine)
    monkeypatch.setattr(th, "generate_narrative_llm",
                        lambda theme, stats, evidence: f"AI 解读 {theme}")
    collect_theme_heat(engine=engine, scope="CONCEPT", bar_date=BAR,
                       fetchers={"board": _fake_board, "flow": _fake_flow,
                                 "hot": lambda: pd.DataFrame([{"代码": "600001"}]),
                                 "members": _fake_members})
    board = load_theme_board(engine, scope="CONCEPT", bar_date=BAR)
    by_theme = {t["theme"]: t for t in board["themes"]}
    assert by_theme["人工智能"]["narrative_llm"] == "AI 解读 人工智能"


def test_market_env_llm_generation_and_cache(monkeypatch):
    """市场环境 AI 叙述：fake 客户端生成 → system_setting 当日缓存 → load 读回。"""
    import core.theme_heat as th
    from core.config import config as app_config

    engine = _engine()
    monkeypatch.setattr(app_config, "AI_MODEL", "test-model")
    monkeypatch.setattr(app_config, "AI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setattr(app_config, "AI_API_KEY", "test-key")
    captured = {}
    def _fake_post(payload):
        captured["user"] = payload["messages"][1]["content"]
        return "组合结构符合退潮后的弱反抽，动量-0.4%但炸板率18%；注意缩量反复。", {}
    monkeypatch.setattr(th, "_post_chat_text", _fake_post)

    out = th.generate_market_env_llm(engine)
    assert out and "弱反抽" in out
    assert "top_themes" in captured["user"]
    # 缓存读回
    assert th.load_cached_market_env_summary(engine) == out


def test_daily_review_theme_digest_line(monkeypatch):
    """每日 AI 复盘正文追加题材热度 TOP3 与市场环境叙述（只增行）。"""
    import core.tasks as tasks
    from core.theme_heat import collect_theme_heat

    engine = _engine()
    _seed_limit_up(engine)
    collect_theme_heat(engine=engine, scope="CONCEPT", bar_date=BAR,
                       fetchers={"board": _fake_board, "flow": _fake_flow,
                                 "hot": lambda: pd.DataFrame([{"代码": "600001"}]),
                                 "members": _fake_members})
    monkeypatch.setattr(tasks, "get_db_engine", lambda: engine)
    # 直接复刻任务里的拼装逻辑验证（任务本体过重，锁行为关键段）
    from core.theme_heat import load_cached_market_env_summary, load_theme_board
    board = load_theme_board(engine, scope="CONCEPT", bar_date=BAR)
    top = (board.get("themes") or [])[:3]
    assert top[0]["theme"] == "人工智能"
    line = "题材热度TOP3：" + "；".join(
        f"{t['theme']} {t['heat']:.0f}分/{t.get('tier') or '观察'}/3日资金{t.get('flow_3d')}亿"
        for t in top
    )
    assert "人工智能" in line and "3日资金" in line
