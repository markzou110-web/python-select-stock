from pydantic import BaseModel
from typing import Optional


class PaperTradeCreate(BaseModel):
    code: str
    name: str
    price: float
    strategy_type: Optional[str] = None
