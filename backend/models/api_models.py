from pydantic import BaseModel, Field, field_validator
from typing import Optional, List

class PaperTradeCreate(BaseModel):
    code: str
    name: str
    price: float

class ScanRequest(BaseModel):
    """Scan request validation model"""
    threshold: float = Field(ge=0, le=1, default=0.12, description="均线粘合阈值 (0-1)")
    vol_multiplier: float = Field(gt=0, le=10, default=1.5, description="量能倍数")
    rsi_min: int = Field(ge=0, le=100, default=55, description="RSI最小值")
    use_macd_filter: bool = True
    use_bb_sqz: bool = False
    sqz_lookback: int = Field(ge=1, le=50, default=10, description="粘合回看天数")
    use_weekly: bool = True
    market_range: str = Field(
        default="全市场(除科创)",
        description="市场范围"
    )
    turnover_min: float = Field(ge=0, le=100, default=3.0, description="最小换手率")
    mkt_cap_min: float = Field(ge=0, default=0.0, description="最小市值(亿)")
    use_rs_filter: bool = True
    local_only: bool = True
    strategy: str = Field(default="Resonance", description="策略类型")
    rf_period: int = Field(ge=10, le=200, default=100, description="RF周期")
    rf_multiplier: float = Field(gt=0, le=10, default=3.0, description="RF倍数")
    only_signals: bool = False
    use_money_flow: bool = False
    money_flow_days: int = Field(ge=1, le=30, default=3, description="资金流统计天数")
    scan_date: Optional[str] = Field(default=None, description="选股日期 (YYYY-MM-DD)")

    @field_validator('strategy')
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        allowed_strategies = ["Resonance", "Range Filter", "Combined"]
        if v not in allowed_strategies:
            raise ValueError(f"策略必须是以下之一: {', '.join(allowed_strategies)}")
        return v

    @field_validator('market_range')
    @classmethod
    def validate_market_range(cls, v: str) -> str:
        # Allow basic market range options
        allowed_ranges = [
            "全市场(除科创)", "包含科创板",
            "沪深300", "上证50", "中证500", "中证1000"
        ]
        if v not in allowed_ranges:
            raise ValueError(f"市场范围必须是以下之一: {', '.join(allowed_ranges)}")
        return v
