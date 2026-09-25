from sqlalchemy import Column, String, Float, Integer, Date, DateTime, Text, JSON, UniqueConstraint
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
    turnover = Column(Float, nullable=True)

class ScanHistory(Base):
    __tablename__ = "scan_history"
    __table_args__ = (UniqueConstraint("code", "data_date", "strategy_type", name="uq_scan_history_signal"),)
    signal_id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20), nullable=False)
    date = Column(Date, nullable=False)
    data_date = Column(Date, nullable=True)
    scanned_at = Column(DateTime, nullable=True)
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
    result_group = Column(String(30), nullable=False, default="FORMAL", server_default="FORMAL")
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
    pa_trade_action = Column(String(20), nullable=True)
    pa_trade_setup = Column(String(80), nullable=True)
    pa_risk_pct = Column(Float, nullable=True)
    sop_grade = Column(String(10), nullable=True)
    sop_quality_score = Column(Float, nullable=True)
    sop_subgrade = Column(String(10), nullable=True)
    sop_vetoes = Column(JSON, nullable=True)
    sop_checks = Column(JSON, nullable=True)
    sop_bonuses = Column(JSON, nullable=True)
    sop_risks = Column(JSON, nullable=True)
    price_action_detail = Column(JSON, nullable=True)

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
    close_source = Column(String(50), nullable=True)
    closed_by = Column(String(50), nullable=True)
    updated_at = Column(DateTime, nullable=True)
    strategy_type = Column(String(20), nullable=True)
    remark = Column(Text, nullable=True)
    theme = Column(String(200), nullable=True)
    rise_logic = Column(Text, nullable=True)
    logic_status = Column(String(30), default="UNVERIFIED")
    logic_last_review_at = Column(DateTime, nullable=True)
    planned_entry_price = Column(Float, nullable=True)
    actual_entry_price = Column(Float, nullable=True)
    entry_slippage_pct = Column(Float, nullable=True)
    position_pct = Column(Float, nullable=True)
    shares = Column(Integer, nullable=True)
    capital_used = Column(Float, nullable=True)
    execution_note = Column(Text, nullable=True)
    plan_adherence = Column(String(30), default="UNKNOWN")
    watchlist_id = Column(Integer, nullable=True)
    trade_mode = Column(String(20), default='SIMULATED', nullable=False)  # SIMULATED | REAL
    entry_source = Column(String(50), nullable=True)
    entry_signal_date = Column(Date, nullable=True)
    entry_reason_snapshot = Column(Text, nullable=True)
    signal_sources = Column(String(20), nullable=True)
    execution_tier = Column(String(5), nullable=True)
    risk_unit = Column(Float, nullable=True)
    source_upgraded_at = Column(DateTime, nullable=True)
    pending_exit_reason = Column(Text, nullable=True)
    pending_exit_signal_date = Column(Date, nullable=True)
    pa_trade_action = Column(String(20), nullable=True)
    pa_trade_setup = Column(String(80), nullable=True)
    pa_entry_condition = Column(Text, nullable=True)
    pa_invalidation = Column(Text, nullable=True)
    pa_risk_pct = Column(Float, nullable=True)

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
    theme = Column(String(200), nullable=True)
    rise_logic = Column(Text, nullable=True)
    logic_status = Column(String(30), default="UNVERIFIED")
    logic_last_review_at = Column(DateTime, nullable=True)
    invalidation = Column(Text, nullable=True)
    pa_trade_action = Column(String(20), nullable=True)
    pa_trade_setup = Column(String(80), nullable=True)
    pa_entry_condition = Column(Text, nullable=True)
    pa_invalidation = Column(Text, nullable=True)
    pa_risk_pct = Column(Float, nullable=True)
    last_review_date = Column(Date, nullable=True)
    watch_decision = Column(String(30), nullable=True)
    watch_action = Column(Text, nullable=True)
    exit_reason = Column(Text, nullable=True)
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


class ScanAuditLog(Base):
    __tablename__ = "scan_audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_date = Column(Date, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    duration_sec = Column(Float, nullable=True)
    status = Column(String(20), default="SUCCESS")
    strategy_type = Column(String(20), nullable=True)
    params_snapshot = Column(JSON, nullable=True)
    version_snapshot = Column(JSON, nullable=True)
    total_snapshot = Column(Integer, default=0)
    candidate_count = Column(Integer, default=0)
    result_count = Column(Integer, default=0)
    fail_reasons = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    as_of = Column(DateTime, nullable=True)
    data_mode = Column(String(30), nullable=True)
    field_coverage = Column(JSON, nullable=True)
    effective_filters = Column(JSON, nullable=True)
    research_only = Column(Integer, default=0)
    degradation_reasons = Column(JSON, nullable=True)


class PointInTimeStockSnapshot(Base):
    __tablename__ = "point_in_time_stock_snapshots"
    __table_args__ = (
        UniqueConstraint("dataset_version", "code", name="uq_point_in_time_snapshot"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_version = Column(String(100), nullable=False)
    as_of = Column(DateTime, nullable=False)
    data_mode = Column(String(30), nullable=False)
    code = Column(String(20), nullable=False)
    name = Column(String(100), nullable=True)
    industry = Column(String(100), nullable=True)
    is_st_or_delist = Column(Integer, default=0)
    turnover = Column(Float, nullable=True)
    mkt_cap = Column(Float, nullable=True)
    source = Column(String(50), nullable=True)
    price = Column(Float, nullable=True)
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    pct_chg = Column(Float, nullable=True)
    vol = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)
    limit_up = Column(Float, nullable=True)
    limit_down = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EventCatalyst(Base):
    __tablename__ = "event_catalysts"
    __table_args__ = (
        UniqueConstraint("code", "published_at", "event_type", name="uq_event_catalyst_identity"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20), nullable=False)
    event_type = Column(String(40), nullable=False)
    published_at = Column(DateTime, nullable=False)
    title = Column(String(300), nullable=True)
    profit_growth_low = Column(Float, nullable=True)
    profit_growth_high = Column(Float, nullable=True)
    source_url = Column(Text, nullable=True)
    verified = Column(Integer, default=0)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ResearchThesis(Base):
    __tablename__ = "research_theses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20), nullable=False)
    title = Column(String(200), nullable=False)
    thesis_date = Column(Date, nullable=False)
    thesis_text = Column(Text, nullable=False)
    catalysts = Column(JSON, nullable=True)
    risks = Column(JSON, nullable=True)
    confirmation_condition = Column(Text, nullable=True)
    invalidation_condition = Column(Text, nullable=True)
    source_snapshot = Column(JSON, nullable=True)
    strategy_type = Column(String(30), nullable=True)
    signal_id = Column(Integer, nullable=True)
    status = Column(String(20), default="ACTIVE", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class FailureSample(Base):
    __tablename__ = "failure_samples"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20))
    name = Column(String(50))
    sample_date = Column(Date)
    strategy_type = Column(String(20), nullable=True)
    failure_type = Column(String(50), nullable=True)
    reason = Column(Text, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    source = Column(String(50), default="paper_trade")
    created_at = Column(DateTime, default=datetime.utcnow)


class RecommendationEvent(Base):
    __tablename__ = "recommendation_events"
    __table_args__ = (
        UniqueConstraint("event_date", "source", "code", "strategy_type", name="uq_recommendation_events_identity"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_date = Column(Date)
    event_time = Column(DateTime, default=datetime.utcnow)
    source = Column(String(50), default="scan")
    code = Column(String(20))
    name = Column(String(50))
    industry = Column(String(100), nullable=True)
    strategy_type = Column(String(30), nullable=True)
    recommendation_price = Column(Float)
    score = Column(Float, nullable=True)
    trade_bucket = Column(String(20), nullable=True)
    trade_eligible = Column(Integer, nullable=True)
    final_trade_score = Column(Float, nullable=True)
    pa_trade_action = Column(String(20), nullable=True)
    pa_trade_setup = Column(String(80), nullable=True)
    pa_entry_price = Column(Float, nullable=True)
    pa_stop_price = Column(Float, nullable=True)
    sector_strength_score = Column(Float, nullable=True)
    stock_sector_fit_score = Column(Float, nullable=True)
    sector_alignment_score = Column(Float, nullable=True)
    sector_phase = Column(String(40), nullable=True)
    market_regime = Column(String(30), nullable=True)
    blockers = Column(JSON, nullable=True)
    status = Column(String(30), default="OPEN")
    created_at = Column(DateTime, default=datetime.utcnow)


class IntradaySignalSnapshot(Base):
    """Append-only point-in-time candidate state for signal performance attribution."""
    __tablename__ = "intraday_signal_snapshots"
    signal_id = Column(String(64), primary_key=True)
    signal_date = Column(Date, nullable=False)
    signal_time = Column(DateTime, nullable=False)
    source = Column(String(40), nullable=False)
    code = Column(String(20), nullable=False)
    name = Column(String(80), nullable=True)
    strategy_type = Column(String(40), nullable=False)
    grade = Column(String(10), nullable=True)
    signal_price = Column(Float, nullable=False)
    confirmation_price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    trade_bucket = Column(String(20), nullable=True)
    trade_eligible = Column(Integer, default=0)
    instruction_state = Column(String(30), nullable=False)
    market_stage = Column(String(30), nullable=True)
    sector_phase = Column(String(40), nullable=True)
    sector_mainline = Column(String(30), nullable=True)
    reachability = Column(String(40), nullable=True)
    strong_exception_shadow = Column(Integer, default=0)
    snapshot_payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class LifecycleEvent(Base):
    __tablename__ = "lifecycle_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_time = Column(DateTime, default=datetime.utcnow)
    event_type = Column(String(40))
    source = Column(String(50), nullable=True)
    code = Column(String(20), nullable=True)
    name = Column(String(50), nullable=True)
    watchlist_id = Column(Integer, nullable=True)
    trade_id = Column(Integer, nullable=True)
    strategy_type = Column(String(30), nullable=True)
    theme = Column(String(200), nullable=True)
    payload = Column(JSON, nullable=True)


class TaskRunAudit(Base):
    __tablename__ = "task_run_audits"
    task_id = Column(String(100), primary_key=True)
    task_name = Column(String(150), nullable=True)
    status = Column(String(30), default="PENDING")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_sec = Column(Float, nullable=True)
    result_summary = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)


class NotificationAudit(Base):
    __tablename__ = "notification_audits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sent_at = Column(DateTime, default=datetime.utcnow)
    title = Column(String(200))
    channels = Column(JSON, nullable=True)
    results = Column(JSON, nullable=True)
    group_name = Column(String(100), nullable=True)
    body_preview = Column(Text, nullable=True)


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    id = Column(Integer, primary_key=True, autoincrement=True)
    dedupe_key = Column(String(64), unique=True, nullable=False)
    channel = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    group_name = Column(String(100), nullable=True)
    is_archive = Column(Integer, default=1)
    status = Column(String(20), default="PENDING", nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    next_retry_at = Column(DateTime, nullable=False)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_at = Column(DateTime, nullable=True)


class StrategyReleaseState(Base):
    __tablename__ = "strategy_release_states"
    strategy_key = Column(String(100), primary_key=True)
    state = Column(String(30), nullable=False, default="DRAFT")
    version = Column(String(80), nullable=True)
    evidence = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class BacktestExperiment(Base):
    __tablename__ = "backtest_experiments"
    experiment_id = Column(String(40), primary_key=True)
    content_hash = Column(String(64), nullable=False, unique=True)
    experiment_type = Column(String(40), nullable=False)
    strategy_type = Column(String(40), nullable=False)
    code_version = Column(String(80), nullable=False)
    data_hash = Column(String(64), nullable=False)
    request_payload = Column(JSON, nullable=False)
    data_manifest = Column(JSON, nullable=False)
    result_payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ExecutionIntent(Base):
    __tablename__ = "execution_intents"
    __table_args__ = (
        UniqueConstraint("signal_date", "source", "code", "strategy_type", name="uq_execution_intent_signal"),
    )
    intent_id = Column(String(40), primary_key=True)
    signal_date = Column(Date, nullable=False)
    issued_at = Column(DateTime, nullable=False)
    valid_until = Column(DateTime, nullable=True)
    source = Column(String(40), nullable=False)
    code = Column(String(20), nullable=False)
    name = Column(String(50), nullable=True)
    strategy_type = Column(String(40), nullable=False)
    instruction = Column(String(20), nullable=False)
    state = Column(String(20), nullable=False, default="ISSUED")
    planned_entry_price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    planned_position_pct = Column(Float, nullable=True)
    ordered_shares = Column(Integer, nullable=True)
    filled_shares = Column(Integer, nullable=True)
    actual_price = Column(Float, nullable=True)
    slippage_pct = Column(Float, nullable=True)
    signal_snapshot = Column(JSON, nullable=True)
    updated_at = Column(DateTime, nullable=False)


class ExecutionIntentEvent(Base):
    __tablename__ = "execution_intent_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    intent_id = Column(String(40), nullable=False)
    event_at = Column(DateTime, nullable=False)
    from_state = Column(String(20), nullable=True)
    to_state = Column(String(20), nullable=False)
    actual_price = Column(Float, nullable=True)
    shares = Column(Integer, nullable=True)
    note = Column(Text, nullable=True)
