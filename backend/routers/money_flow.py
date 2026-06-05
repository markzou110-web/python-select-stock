from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from core.db import validate_stock_code
from core.money_flow import get_money_flow_rank, get_sector_money_flow_rank, get_stock_money_flow

router = APIRouter(prefix="/api/money-flow", tags=["money-flow"])


@router.get("/stock/{code}")
def stock_money_flow(code: str, force_refresh: bool = False) -> Dict[str, Any]:
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code")
    return get_stock_money_flow(code, force_refresh=force_refresh)


@router.get("/rank")
def stock_money_flow_rank(indicator: str = "今日", limit: int = 30, force_refresh: bool = False) -> Dict[str, Any]:
    return get_money_flow_rank(indicator=indicator, limit=limit, force_refresh=force_refresh)


@router.get("/sector-rank")
def sector_money_flow_rank(
    indicator: str = "今日",
    sector_type: str = "行业资金流",
    limit: int = 30,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    return get_sector_money_flow_rank(
        indicator=indicator,
        sector_type=sector_type,
        limit=limit,
        force_refresh=force_refresh,
    )
