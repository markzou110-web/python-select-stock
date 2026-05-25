from pydantic import BaseModel, Field
from typing import Optional


class PaperTradeCreate(BaseModel):
    code: str
    name: str
    price: float
    strategy_type: Optional[str] = None
    remark: Optional[str] = None


class PaperTradeClose(BaseModel):
    close_price: float = Field(gt=0, description="卖出价格，必须大于 0")

