from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal


class PaperTradeCreate(BaseModel):
    code: str
    name: str
    price: float
    strategy_type: Optional[str] = None
    remark: Optional[str] = None
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
