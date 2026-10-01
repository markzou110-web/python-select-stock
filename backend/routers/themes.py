"""题材热度看板 API（借鉴 easy-stock 主题热点页）。

GET /api/themes/heat?scope=CONCEPT&date=  → 市场环境 banner + 热度榜
GET /api/themes/members?scope=&theme=&date= → 成分股列表
市场环境聚合三类既有状态：市场状态闸门（R3 动量）、涨停情绪（ZT_EBB）、
NH-NL 宽度——与题材热度同源同时间戳。
"""
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from core.db import get_db_engine
from core.logging_config import logger
from core.market_regime import (
    compute_limit_up_sentiment,
    compute_market_state_gate,
    limit_up_sentiment_ebb,
)
from core.theme_heat import load_theme_board

router = APIRouter(prefix="/api/themes", tags=["themes"])


def _market_environment(engine) -> Dict[str, Any]:
    try:
        gate = compute_market_state_gate(engine)
    except Exception:
        gate = {}
    try:
        zt = compute_limit_up_sentiment(engine)
    except Exception:
        zt = {}
    ebb = limit_up_sentiment_ebb(zt) if zt else False
    if gate.get("blocked") and ebb:
        stage, confidence = "退潮", 0.7
    elif ebb:
        stage, confidence = "退潮", 0.6
    elif gate.get("blocked"):
        stage, confidence = "普跌压制", 0.6
    else:
        stage, confidence = "可交易", 0.5
    return {
        "stage": stage,
        "confidence": confidence,
        "as_of": gate.get("bar_date"),
        "mom_10d_pct": gate.get("mom_10d_pct"),
        "gate_blocked": gate.get("blocked"),
        "zt_broken_rate": zt.get("broken_rate"),
        "zt_max_streak": zt.get("max_streak"),
        "zt_bar_date": zt.get("bar_date"),
        "summary": (
            f"市场状态闸门：10日动量 {gate.get('mom_10d_pct')}%（{'建议暂停新开仓' if gate.get('blocked') else '正常'}）；"
            f"涨停情绪：炸板率 {zt.get('broken_rate') if zt.get('broken_rate') is not None else '--'}%"
            f"{'（退潮）' if ebb else ''}。SHADOW 观察数据，不构成交易指令。"
        ),
    }


@router.get("/heat")
def get_theme_heat(scope: str = "CONCEPT", date: str = "") -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="database unavailable")
    scope = scope.upper() if scope.upper() in {"CONCEPT", "INDUSTRY"} else "CONCEPT"
    board = load_theme_board(engine, scope=scope, bar_date=date or None)
    return {"market_environment": _market_environment(engine), **board}


@router.get("/members")
def get_theme_members(scope: str = "CONCEPT", theme: str = "", date: str = "") -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine or not theme:
        raise HTTPException(status_code=400, detail="theme required")
    scope = scope.upper() if scope.upper() in {"CONCEPT", "INDUSTRY"} else "CONCEPT"
    bar_date = date or pd.Timestamp.now().strftime("%Y-%m-%d")
    try:
        row = pd.read_sql(text(
            "SELECT members_json, narrative FROM theme_heat_history "
            "WHERE scope=:s AND theme=:t AND bar_date=( "
            "  SELECT MAX(bar_date) FROM theme_heat_history WHERE scope=:s2 AND theme=:t2 AND bar_date <= :d)"
        ), engine, params={"s": scope, "t": theme, "s2": scope, "t2": theme, "d": bar_date})
    except Exception as exc:
        logger.warning(f"theme members query failed: {exc}")
        raise HTTPException(status_code=500, detail="query failed")
    if row.empty:
        raise HTTPException(status_code=404, detail="theme not found for date")
    import json as _json

    members = _json.loads(row.iloc[0]["members_json"] or "[]")
    return {"theme": theme, "scope": scope, "bar_date": bar_date, "members": members,
            "narrative": row.iloc[0]["narrative"]}
