from sqlalchemy import Column, String, Float, Integer, Date, DateTime, Text
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime

class Base(DeclarativeBase):
    pass

class StockBasic(Base):
    __tablename__ = "stock_basic"
    code = Column(String(20), primary_key=True)
    name = Column(String(50))
    industry = Column(String(100))

class DailyK(Base):
    __tablename__ = "daily_k"
    code = Column(String(20), primary_key=True)
    date = Column(Date, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    vol = Column(Float)

class ScanHistory(Base):
    __tablename__ = "scan_history"
    code = Column(String(20), primary_key=True)
    date = Column(Date, primary_key=True)
    name = Column(String(50))
    price = Column(Float)
    pct = Column(Float)
    score = Column(Float)
    rsi = Column(Float)
    dif = Column(Float)
    bb = Column(Float)
    glue = Column(Float)
    industry = Column(String(100))
    win_rate = Column(String(20))
    signal_count = Column(Integer)
    north_money = Column(String(100))
    resonance = Column(String(50))
    shadow_ratio = Column(Float)
    strategy_type = Column(String(20))
    roe = Column(Float, nullable=True)
    net_profit_yoy = Column(Float, nullable=True)
    price_action_score = Column(Float, nullable=True)
    price_action_regime = Column(String(50), nullable=True)
    price_action_signal = Column(String(50), nullable=True)
    price_action_pattern = Column(String(50), nullable=True)
    price_action_entry_quality = Column(String(50), nullable=True)
    price_action_summary = Column(Text, nullable=True)
    pa_entry_price = Column(Float, nullable=True)
    pa_stop_price = Column(Float, nullable=True)
    pa_target_price = Column(Float, nullable=True)
    pa_risk_reward = Column(Float, nullable=True)

class PaperTrading(Base):
    __tablename__ = "paper_trading"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20))
    name = Column(String(50))
    entry_price = Column(Float)
    entry_date = Column(Date)
    current_price = Column(Float)
    high_since_entry = Column(Float, nullable=True) # 用于移动止损追踪
    status = Column(String(20), default='OPEN')
    close_price = Column(Float, nullable=True)
    close_date = Column(Date, nullable=True)
    strategy_type = Column(String(20), nullable=True)
    remark = Column(Text, nullable=True)
    trade_mode = Column(String(20), default='SIMULATED', nullable=False)  # SIMULATED | REAL

class SystemSetting(Base):
    __tablename__ = "system_settings"
    key = Column(String(100), primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class StockFundamental(Base):
    __tablename__ = "stock_fundamentals"
    code = Column(String(20), primary_key=True)
    roe = Column(Float)
    pe_ttm = Column(Float)
    pe_percentile = Column(Float)
    net_profit_yoy = Column(Float)
    revenue_yoy = Column(Float)
    label = Column(String(50))
    updated_at = Column(Date)

class WatchlistItem(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20))
    name = Column(String(50))
    industry = Column(String(100), nullable=True)
    source = Column(String(50), default="manual")
    strategy_type = Column(String(20), default="squeeze")
    watch_price = Column(Float)
    target_price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    status = Column(String(20), default="WATCHING")
    reason = Column(Text, nullable=True)
    invalidation = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class StrategyTemplate(Base):
    __tablename__ = "strategy_templates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100))
    strategy_type = Column(String(20), default="squeeze")
    params_json = Column(Text)
    description = Column(Text, nullable=True)
    is_default = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
