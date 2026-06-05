from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from typing import Dict, Any
from datetime import datetime
import json

from core.db import get_db_engine
from core.logging_config import logger
from core.pro_workflow import recommend_strategy_template

router = APIRouter(prefix="/api/strategy-templates", tags=["strategy-templates"])


DEFAULT_TEMPLATE_PARAMS = {
    "strategy_type": "tv_dual_strict",
    "pine_min_signals": 3,
    "min_data_days": 120,
    "threshold": 0.12,
    "vol_multiplier": 1.5,
    "rsi_min": 55,
    "use_macd_filter": True,
    "use_bb_sqz": False,
    "sqz_lookback": 10,
    "use_weekly": False,
    "weekly_ma_period": 20,
    "market_range": "全市场(除科创)",
    "turnover_min": 3.0,
    "mkt_cap_min": 0,
    "use_rs_filter": False,
    "local_only": True,
    "data_date": "",
    "stop_loss_pct": -8,
}


def _row_to_template(row) -> Dict[str, Any]:
    mapping = row._mapping
    try:
        params = json.loads(mapping["params_json"] or "{}")
    except json.JSONDecodeError:
        params = {}
    return {
        "id": int(mapping["id"]),
        "name": mapping["name"],
        "strategy_type": mapping["strategy_type"],
        "params": params,
        "description": mapping["description"] or "",
        "is_default": bool(mapping["is_default"]),
        "created_at": mapping["created_at"].isoformat() if mapping["created_at"] else "",
        "updated_at": mapping["updated_at"].isoformat() if mapping["updated_at"] else "",
    }


def _ensure_seed_templates(engine) -> None:
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM strategy_templates")).scalar()
        if count and int(count) > 0:
            return
        seeds = [
            {
                "name": "TV双策略强共振",
                "strategy_type": "tv_dual_strict",
                "params": DEFAULT_TEMPLATE_PARAMS,
                "description": "尾盘买入候选：最近窗口内均线B共振和 TV-ZP long 同时出现。",
                "is_default": 1,
            },
            {
                "name": "强动能共振",
                "strategy_type": "pine",
                "params": {**DEFAULT_TEMPLATE_PARAMS, "strategy_type": "pine", "pine_min_signals": 3, "use_weekly": True},
                "description": "更偏右侧确认，要求多指标同时转强。",
                "is_default": 0,
            },
            {
                "name": "严格突破过滤",
                "strategy_type": "consensus",
                "params": {**DEFAULT_TEMPLATE_PARAMS, "strategy_type": "consensus", "vol_multiplier": 1.8, "turnover_min": 4},
                "description": "强调放量、强阳和趋势质量，信号更少但更聚焦。",
                "is_default": 0,
            },
        ]
        conn.execute(text("""
            INSERT INTO strategy_templates
                (name, strategy_type, params_json, description, is_default, created_at, updated_at)
            VALUES
                (:name, :strategy_type, :params_json, :description, :is_default, :created_at, :updated_at)
        """), [
            {
                **seed,
                "params_json": json.dumps(seed["params"], ensure_ascii=False),
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            }
            for seed in seeds
        ])
        conn.commit()


@router.get("/list")
def list_strategy_templates() -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return {"templates": []}
    try:
        _ensure_seed_templates(engine)
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT * FROM strategy_templates
                ORDER BY is_default DESC, updated_at DESC, id DESC
            """)).fetchall()
        return {"templates": [_row_to_template(row) for row in rows]}
    except Exception as exc:
        logger.error(f"List strategy templates error: {exc}")
        return {"templates": []}


@router.get("/recommendation")
def get_strategy_template_recommendation(
    market_regime: str = "UNKNOWN",
    risk_status: str = "ok",
    recent_win_rate: float = 0,
) -> Dict[str, Any]:
    return {
        "status": "success",
        "recommendation": recommend_strategy_template(
            market_regime=market_regime,
            risk_status=risk_status,
            recent_win_rate=float(recent_win_rate or 0),
        ),
    }


@router.post("/save")
def save_strategy_template(data: Dict[str, Any]) -> Dict[str, Any]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Template name is required")
    params = data.get("params") or {}
    strategy_type = data.get("strategy_type") or params.get("strategy_type") or "squeeze"

    engine = get_db_engine()
    if not engine:
        return {"status": "error", "detail": "Database unavailable"}

    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO strategy_templates
                    (name, strategy_type, params_json, description, is_default, created_at, updated_at)
                VALUES
                    (:name, :strategy_type, :params_json, :description, 0, :created_at, :updated_at)
            """), {
                "name": name,
                "strategy_type": strategy_type,
                "params_json": json.dumps(params, ensure_ascii=False),
                "description": data.get("description") or "",
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            })
            conn.commit()
        return {"status": "success"}
    except Exception as exc:
        logger.error(f"Save strategy template error: {exc}")
        return {"status": "error", "detail": str(exc)}


@router.delete("/remove/{template_id}")
def remove_strategy_template(template_id: int) -> Dict[str, str]:
    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM strategy_templates WHERE id = :id AND is_default = 0"),
                {"id": template_id},
            )
            conn.commit()
        return {"status": "success"}
    except Exception as exc:
        logger.error(f"Remove strategy template error: {exc}")
        return {"status": "error"}
