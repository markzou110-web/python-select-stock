from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal


class PaperTradeCreate(BaseModel):
    code: str
    name: str
    price: float
    strategy_type: Optional[str] = None
    remark: Optional[str] = None
    theme: Optional[str] = None
    rise_logic: Optional[str] = None
    watchlist_id: Optional[int] = None
    planned_entry_price: Optional[float] = None
    actual_entry_price: Optional[float] = None
    position_pct: Optional[float] = None
    shares: Optional[int] = None
    capital_used: Optional[float] = None
    execution_note: Optional[str] = None
    plan_adherence: Literal["FOLLOWED", "OVERRIDDEN", "UNKNOWN"] = "UNKNOWN"
    force: Optional[bool] = False
    trade_mode: Literal["SIMULATED", "REAL"] = "SIMULATED"
    entry_source: Optional[str] = None
    entry_signal_date: Optional[str] = None
    entry_reason_snapshot: Optional[str] = None
    pa_trade_action: Optional[str] = None
    pa_trade_setup: Optional[str] = None
    pa_entry_condition: Optional[str] = None
    pa_invalidation: Optional[str] = None
    pa_risk_pct: Optional[float] = None


class PaperTradeClose(BaseModel):
    close_price: float = Field(gt=0, description="卖出价格，必须大于 0")
    close_shares: Optional[int] = Field(default=None, gt=0, description="部分卖出股数；为空则全部平仓")
    execution_note: Optional[str] = None
