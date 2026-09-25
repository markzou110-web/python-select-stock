import os
import json
import math
import re
import uuid
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from typing import Optional, Dict, Any, List
from .logging_config import logger
from .models import Base, StockBasic, DailyK, ScanHistory, PaperTrading, SystemSetting, StockFundamental
from .errors import DatabaseError, ValidationError

# 获取项目根目录下的配置文件路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "db_config.json")


def validate_stock_code(code: str) -> bool:
    """
    Validate Chinese stock code format (6 digits).
    Includes:
    - 00xxxx, 30xxxx (SZ)
    - 60xxxx, 68xxxx, 900xxx (SH)
    - 43xxxx, 83xxxx, 87xxxx, 88xxxx, 92xxxx (BJ)
    """
    return bool(re.match(r'^[0-9]\d{5}$', str(code)))


def validate_table_name(name: str) -> bool:
    """
    Validate table name to prevent SQL injection.
    Only allows alphanumeric characters and underscores.
    """
    return bool(re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', str(name)))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    # 修复 R3-1: NaN 和 inf/-inf 都会导致 json.dumps 生成无效 JSON（Infinity token），
    # 进而导致 PG CAST AS JSONB 失败，整批 save_scan_results 丢失。
    # stock.py:37 的 _json_safe_response 已有此守卫，此处补齐。
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
    try:
        if pd.isna(value) if not isinstance(value, (dict, list, tuple, set)) else False:
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            scalar = value.item()
            # numpy 标量也可能转出 inf/nan
            if isinstance(scalar, float) and (math.isnan(scalar) or math.isinf(scalar)):
                return None
            return scalar
        except Exception:
            pass
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


PRICE_ACTION_DETAIL_KEYS = [
    "tv_match", "tv_ma_signal", "tv_zp_signal",
    "signal_sources", "tv_execution_policy_version", "tv_execution_tier",
    "tv_execution_tier_label", "tv_execution_risk_unit", "tv_execution_auto",
    "tv_same_day_dual", "trade_exit_policy",
    "bark_selection_source_label", "bark_scan_strategy_label",
    "pct_5d",
    "price_action_score", "price_action_regime", "price_action_signal", "price_action_pattern",
    "price_action_entry_quality", "price_action_summary", "price_action_risks",
    "pa_market_cycle", "pa_range_location", "pa_entry_price", "pa_stop_price",
    "pa_close_guard_price", "pa_hard_stop_price", "pa_invalidation_basis",
    "pa_invalidation_rule",
    "pa_target_price", "pa_risk_reward", "pa_actual_space_rr", "pa_target_basis",
    "pa_structure_score", "pa_execution_score", "pa_risk_score", "pa_tags", "pa_pullback_legs",
    "pa_pullback_structure", "pa_pullback_validity", "pa_pullback_status",
    "pa_pullback_status_label", "pa_pullback_support_price", "pa_pullback_confirmation_price",
    "pa_pullback_invalidation_price", "pa_pullback_action", "pa_breakout_quality", "pa_failure_risk",
    "pa_entry_quality_score", "pa_h2_quality", "pa_range_rule",
    "pa_h2_state", "pa_l2_state", "pa_second_entry_retracement_quality",
    "pa_follow_through_state", "pa_follow_through",
    "pa_failed_breakout_type", "pa_trap_risk", "pa_micro_channel",
    "pa_always_in_strength", "pa_trend_damage", "pa_channel_state",
    "pa_position_strategy", "pa_weekly_context", "pa_multi_timeframe_score",
    "pa_multi_timeframe_note", "pa_current_week_complete",
    "pa_monthly_trend", "pa_monthly_state", "pa_monthly_as_of",
    "pa_weekly_position", "pa_weekly_position_state", "pa_weekly_position_as_of",
    "pa_weekly_pattern_signals",
    "weekly_pattern_watch_only",
    "pa_swing_entry_route", "pa_timeframe_shadow_only",
    "price_action_version", "target_model_version", "score_model_version",
    "pa_volume_pattern", "pa_volume_confirmed", "pa_volume_pullback",
    "pa_volume_pullback_status", "pa_volume_pullback_label", "pa_volume_pullback_score_delta",
    "pa_volume_pullback_breakout_date", "pa_volume_pullback_support_price",
    "pa_volume_pullback_confirmation_label", "pa_volume_pullback_confirmation_date",
    "pa_volume_pullback_stop_price",
    "pa_volume_confirmation_state",
    "pa_volume_confirmation_final", "pa_close_confirmation_phase", "pa_close_confirmation_as_of",
    "pa_close_time_eligible", "pa_confirmation_state", "pa_execution_stage",
    "pa_execution_stage_label", "pa_signal_date",
    "pa_volume_ratio", "pa_volume_ratio_percentile", "pa_breakout_volume_threshold",
    "pa_confirmation_volume_threshold",
    "pa_volume_risk", "pa_failed_second_entry", "pa_second_entry_risk",
    "pa_gap_type", "pa_gap_type_v2", "pa_gap_fill_pct", "pa_opening_behavior",
    "pa_gap_edges", "pa_gap_risk", "pa_range_width_quality",
    "pa_range_center_risk", "pa_range_failed_breakout_count",
    "pa_trend_phase", "pa_trend_phase_action", "pa_structure_state",
    "pa_structure_state_label", "pa_structure_state_action", "pa_mtr_state",
    "pa_mtr_direction", "pa_nearest_support_zone",
    "pa_nearest_resistance_zone", "pa_sr_confluence_grade", "pa_mtf_state",
    "pa_mtf_intraday", "pa_decision_summary",
    "pa_trend_path_quality", "pa_information_discreteness", "pa_trend_efficiency",
    "pa_top_day_contribution", "pa_trend_net_return", "pa_nonlinear_trend_strength",
    "pa_trend_extension_atr", "pa_extreme_trend", "pa_path_research_score_delta",
    "pa_eight_rules", "pa_eight_rule_primary", "pa_eight_rule_score_delta",
    "pa_eight_rule_risk_delta",
    "pa_trade_plan", "trade_eligible", "trade_bucket", "trade_blockers",
    "trade_cautions", "trade_gate_policy_version", "sop_soft_vetoes",
    "final_trade_score", "trade_timeframe", "exit_hint",
    "a_eod_controlled_trial", "a_eod_policy_version", "a_eod_trial_checks",
    "a_eod_entry_extension_pct", "a_eod_trade_cautions",
    "a_eod_portfolio_cap_pct", "a_eod_max_positions",
    "market_regime", "effective_market_regime",
    "market_segment", "market_segment_stage", "market_segment_label", "market_segment_breadth",
    "sop_grade", "sop_action", "sop_risks", "sop_vetoes", "sop_checks", "sop_bonuses",
    "sop_quality_score", "sop_subgrade",
    "sector_momentum_score", "sector_breadth",
    "sector_strength_score", "stock_sector_fit_score",
    "sector_phase", "sector_rank", "sector_alignment_score", "sector_relative_pct",
    "sector_3d_pct", "sector_5d_pct", "sector_consecutive_up_days", "sector_role",
    "stock_rank_in_sector", "limit_up_unsealed",
    "sector_mainline", "leadership_score", "leadership_components", "leadership_reason",
    "limit_up_status", "first_limit_time", "last_limit_time", "break_count",
    "limit_up_streak", "seal_amount", "limit_up_sector_rank",
    "event_catalyst", "event_driven_candidate", "event_model_version",
    "event_post_limit_state", "event_alert_tier", "event_health_scope",
    "trade_blocker_groups",
    "execution_rr", "execution_plan_state", "distance_to_trade",
    "execution_plan_frozen", "frozen_plan_date", "frozen_plan_expiry_date",
    "frozen_plan_valid_sessions", "active_confirmation_price", "active_stop_price",
    "active_close_guard_price", "active_target_price", "active_execution_plan_source",
    "sector_watch_only", "sector_watch_reason",
    "market_sentiment_stage", "market_sentiment_label", "market_sentiment_score",
    "market_sentiment_reason", "market_cycle_metrics", "market_sentiment_model_version",
    "portfolio_position_cap_pct", "market_allowed_actions", "market_forbidden_actions", "market_breadth",
    "trade_opportunity_score", "trade_opportunity_label", "decision_score_components",
    "position_plan", "trade_state", "execution_instruction",
    "raw_score", "calibrated_score", "score_components",
    "research_eligible", "research_missing_fields",
    "strategy_health",
    "strategy_health_segment", "strategy_health_scope",
    "grade_stage", "grade_label", "grade_action", "grade_reason",
    "decision_lifecycle_state", "decision_lifecycle_action",
    "confirmation_event_state", "confirmation_event_reason",
    "early_value_transition_state",
    "bottom_discovery_watch_only", "bottom_discovery_stage", "bottom_discovery_action",
    "bottom_discovery_metrics", "bottom_discovery_transition_state",
    "sequoia_research_shadow_only", "release_state", "shadow_instruction",
    "high_tight_flag_watch_only", "turtle_breakout_watch_only", "limit_up_shakeout_watch_only",
    "high_tight_flag_metrics", "turtle_breakout_metrics", "limit_up_shakeout_metrics",
    "rps_60", "rps_120", "rps_250", "rps_sector_120", "rps_acceleration", "rps_data_date",
    "shakeout_source_event_date", "prior_limit_up_status", "prior_limit_up_streak",
    "prior_limit_up_break_count",
    "evidence_id", "evidence_grade", "evidence_status", "evidence_summary",
    "evidence_reason_codes", "evidence_gate_mode", "evidence_pipeline_stage",
    "evidence_bundle", "decision_memo",
    "selection_health", "execution_health", "strategy_health_control_cohort",
    "confirmation_reachability", "confirmation_limit_price", "confirmation_reachability_reason",
    "execution_review_state", "strong_exception_shadow", "strong_exception_shadow_reason",
    "strong_exception_shadow_entry",
    "sop_base_grade", "sop_quality_gap_to_a", "sop_grade_reason",
    "sop_grade_transition_reasons", "sop_quality_dimensions",
    "sop_a_grade_eligible", "sop_a_grade_gate_reasons", "sop_grade_policy_version",
    "execution_plan_frozen", "frozen_plan_date", "frozen_confirmation_price",
    "frozen_stop_price", "frozen_close_guard_price", "frozen_target_price", "generated_confirmation_price",
    "generated_stop_price", "active_execution_plan_source", "active_confirmation_price",
    "active_stop_price", "active_close_guard_price", "active_target_price", "pa_setup_confirmed",
    "pa_plan_triggered", "pa_close_confirmed", "pa_confirmation_state",
    "pa_close_confirmation_as_of", "pa_close_confirmation_phase", "pa_close_time_eligible",
    "display_trade_score", "display_signal_score",
    "display_quality_score", "display_opportunity_score", "display_rank_score",
    "score_display_scale",
    "result_group", "data_mode", "as_of",
    "mkt_cap_yi", "money_flow", "money_flow_status", "回测统计",
    "frozen_entry_extension_pct", "frozen_confirmation_triggered",
]


def _price_action_detail_snapshot(row: Dict[str, Any]) -> str:
    detail = {key: row.get(key) for key in PRICE_ACTION_DETAIL_KEYS if key in row}
    return json.dumps(_json_safe(detail), ensure_ascii=False)


def load_db_config() -> Dict[str, Any]:
    """从本地文件加载数据库配置"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load db config: {e}")
            return {}
    return {}

def save_db_config(config: Dict[str, Any]) -> bool:
    """保存数据库配置到本地文件"""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)
        return True
    except (OSError, TypeError) as e:
        logger.error(f"Failed to save db config: {e}")
        return False

_engine = None
SessionLocal = None

def get_db_engine(db_config: Optional[Dict[str, Any]] = None):
    """根据配置获取数据库引擎"""
    global _engine, SessionLocal
    if _engine is not None:
        return _engine

    if not db_config:
        db_config = load_db_config()

    if not db_config:
        # 兜底：使用环境变量配置 (config.py)
        try:
            from .config import config as app_config
            url = app_config.get_database_url()
            _engine = create_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=3600)
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
            logger.info("Database engine created from environment config.")
            return _engine
        except Exception as e:
            logger.error(f"Failed to create engine from env config: {e}")
            return None

    try:
        url = f"postgresql://{db_config['user']}:{db_config['pwd']}@{db_config['host']}:{db_config['port']}/{db_config['db']}"
        _engine = create_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=3600)
        
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
        
        return _engine
    except (KeyError, ValueError) as e:
        logger.error(f"Invalid db config: {e}")
        return None
    except Exception as e:
        logger.error(f"Error creating engine: {e}")
        return None

def init_db(engine=None):
    """初始化数据库表"""
    if engine is None:
        engine = get_db_engine()
    if not engine: return
    try:
        # 使用 ORM 创建所有表 (如果不存在则创建)
        Base.metadata.create_all(bind=engine)
        
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(80) PRIMARY KEY,
                    applied_at TIMESTAMP NOT NULL,
                    description TEXT
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS task_slot_claims (
                    slot_key VARCHAR(160) PRIMARY KEY,
                    claimed_at TIMESTAMP NOT NULL,
                    status VARCHAR(30) NOT NULL DEFAULT 'CLAIMED'
                )
            """))
            conn.execute(text("""
                INSERT INTO schema_migrations(version, applied_at, description)
                VALUES ('2026-07-12-execution-ops-v1', CURRENT_TIMESTAMP, 'execution evidence, event, notification and ops fields')
                ON CONFLICT(version) DO NOTHING
            """))
            conn.execute(text("""
                INSERT INTO schema_migrations(version, applied_at, description)
                VALUES ('2026-07-13-intraday-signal-snapshots-v1', CURRENT_TIMESTAMP, 'append-only point-in-time signal snapshots')
                ON CONFLICT(version) DO NOTHING
            """))
            conn.execute(text("""
                INSERT INTO schema_migrations(version, applied_at, description)
                VALUES ('2026-07-16-notification-snapshot-resilience-v1', CURRENT_TIMESTAMP, 'durable notification outbox and quote-bearing point-in-time snapshots')
                ON CONFLICT(version) DO NOTHING
            """))
            conn.commit()
            # 性能索引
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_k_date ON daily_k(date);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_k_code ON daily_k(code);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_k_code_date ON daily_k(code, date);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_scan_history_date ON scan_history(date);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_intraday_signal_snapshots_date ON intraday_signal_snapshots(signal_date DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_intraday_signal_snapshots_code_date ON intraday_signal_snapshots(code, signal_date DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_paper_trading_status ON paper_trading(status);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stock_basic_industry ON stock_basic(industry);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_k_date_code ON daily_k(date, code);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_paper_trading_status_date ON paper_trading(status, entry_date DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist(status);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_watchlist_code_status ON watchlist(code, status);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_strategy_templates_type ON strategy_templates(strategy_type);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_scan_audit_date ON scan_audit_log(scan_date DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_failure_samples_code_date ON failure_samples(code, sample_date DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_lifecycle_events_time ON lifecycle_events(event_time DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_lifecycle_events_type ON lifecycle_events(event_type);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_lifecycle_events_watchlist_type ON lifecycle_events(watchlist_id, event_type, event_time DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_lifecycle_events_code_type ON lifecycle_events(code, event_type, event_time DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_task_run_audits_started ON task_run_audits(started_at DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_notification_audits_sent ON notification_audits(sent_at DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_notification_outbox_due ON notification_outbox(status, next_retry_at);"))
                logger.info("Database and performance indexes verified via ORM.")
            except Exception as e:
                logger.debug(f"Index creation skipped: {e}")

            # Scan audit point-in-time metadata. Additive migration only: existing audit rows remain valid.
            try:
                audit_columns = {
                    "as_of": "TIMESTAMP",
                    "data_mode": "VARCHAR(30)",
                    "field_coverage": "JSON",
                    "effective_filters": "JSON",
                    "research_only": "INTEGER DEFAULT 0",
                    "degradation_reasons": "JSON",
                }
                if engine.dialect.name == "sqlite":
                    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(scan_audit_log)"))}
                    for column_name, definition in audit_columns.items():
                        if column_name not in existing:
                            conn.execute(text(f"ALTER TABLE scan_audit_log ADD COLUMN {column_name} {definition}"))
                else:
                    clauses = ", ".join(
                        f"ADD COLUMN IF NOT EXISTS {name} {definition}"
                        for name, definition in audit_columns.items()
                    )
                    conn.execute(text(f"ALTER TABLE scan_audit_log {clauses}"))
                logger.info("Migration: scan audit point-in-time metadata ensured.")
            except Exception as e:
                logger.debug(f"scan audit metadata migration skipped: {e}")

            # Additive only: old snapshot rows remain readable and simply have NULL quotes.
            try:
                quote_columns = {
                    "price": "FLOAT", "open": "FLOAT", "high": "FLOAT", "low": "FLOAT",
                    "pct_chg": "FLOAT", "vol": "FLOAT", "amount": "FLOAT",
                    "limit_up": "FLOAT", "limit_down": "FLOAT",
                }
                if engine.dialect.name == "sqlite":
                    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(point_in_time_stock_snapshots)"))}
                    for column_name, definition in quote_columns.items():
                        if column_name not in existing:
                            conn.execute(text(f"ALTER TABLE point_in_time_stock_snapshots ADD COLUMN {column_name} {definition}"))
                else:
                    clauses = ", ".join(
                        f"ADD COLUMN IF NOT EXISTS {name} {definition}"
                        for name, definition in quote_columns.items()
                    )
                    conn.execute(text(f"ALTER TABLE point_in_time_stock_snapshots {clauses}"))
                logger.info("Migration: point-in-time snapshot quote columns ensured.")
            except Exception as e:
                logger.debug(f"point-in-time snapshot quote migration skipped: {e}")

            # --- Migration: add trade_mode column if missing ---
            try:
                conn.execute(text("""
                    ALTER TABLE paper_trading ADD COLUMN IF NOT EXISTS trade_mode VARCHAR(20) DEFAULT 'SIMULATED' NOT NULL
                """))
                conn.execute(text("""
                    UPDATE paper_trading SET trade_mode = 'SIMULATED' WHERE trade_mode IS NULL
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_paper_trading_mode ON paper_trading(trade_mode);"))
                logger.info("Migration: trade_mode column ensured.")
            except Exception as e:
                logger.debug(f"trade_mode migration skipped (may already exist): {e}")

            # --- Migration: paper trading entry source metadata ---
            try:
                conn.execute(text("""
                    ALTER TABLE paper_trading
                    ADD COLUMN IF NOT EXISTS entry_source VARCHAR(50),
                    ADD COLUMN IF NOT EXISTS entry_signal_date DATE,
                    ADD COLUMN IF NOT EXISTS entry_reason_snapshot TEXT
                """))
                logger.info("Migration: paper trading entry metadata columns ensured.")
            except Exception as e:
                logger.debug(f"paper trading entry metadata migration skipped: {e}")

            # Additive only: preserves all existing positions and leaves legacy rows nullable.
            try:
                execution_state_columns = {
                    "signal_sources": "VARCHAR(20)",
                    "execution_tier": "VARCHAR(5)",
                    "risk_unit": "FLOAT",
                    "source_upgraded_at": "TIMESTAMP",
                    "pending_exit_reason": "TEXT",
                    "pending_exit_signal_date": "DATE",
                }
                if engine.dialect.name == "sqlite":
                    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(paper_trading)"))}
                    for column_name, definition in execution_state_columns.items():
                        if column_name not in existing:
                            conn.execute(text(f"ALTER TABLE paper_trading ADD COLUMN {column_name} {definition}"))
                else:
                    clauses = ", ".join(
                        f"ADD COLUMN IF NOT EXISTS {name} {definition}"
                        for name, definition in execution_state_columns.items()
                    )
                    conn.execute(text(f"ALTER TABLE paper_trading {clauses}"))
                logger.info("Migration: TV execution state columns ensured.")
            except Exception as e:
                logger.debug(f"TV execution state migration skipped: {e}")

            # --- Migration: stock theme and rise logic ---
            try:
                if engine.dialect.name == "sqlite":
                    for table_name in ("paper_trading", "watchlist"):
                        existing = {
                            row[1]
                            for row in conn.execute(text(f"PRAGMA table_info({table_name})"))
                        }
                        if "theme" not in existing:
                            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN theme VARCHAR(200)"))
                        if "rise_logic" not in existing:
                            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN rise_logic TEXT"))
                else:
                    conn.execute(text("""
                        ALTER TABLE paper_trading
                        ADD COLUMN IF NOT EXISTS theme VARCHAR(200),
                        ADD COLUMN IF NOT EXISTS rise_logic TEXT
                    """))
                    conn.execute(text("""
                        ALTER TABLE watchlist
                        ADD COLUMN IF NOT EXISTS theme VARCHAR(200),
                        ADD COLUMN IF NOT EXISTS rise_logic TEXT
                    """))
                logger.info("Migration: stock theme and rise logic columns ensured.")
            except Exception as e:
                logger.debug(f"stock theme and rise logic migration skipped: {e}")

            # --- Migration: P1 lifecycle, logic status, and execution audit ---
            try:
                columns = {
                    "paper_trading": {
                        "logic_status": "VARCHAR(30) DEFAULT 'UNVERIFIED'",
                        "logic_last_review_at": "TIMESTAMP",
                        "planned_entry_price": "FLOAT",
                        "actual_entry_price": "FLOAT",
                        "entry_slippage_pct": "FLOAT",
                        "position_pct": "FLOAT",
                        "shares": "INTEGER",
                        "capital_used": "FLOAT",
                        "execution_note": "TEXT",
                        "plan_adherence": "VARCHAR(30) DEFAULT 'UNKNOWN'",
                        "watchlist_id": "INTEGER",
                    },
                    "watchlist": {
                        "logic_status": "VARCHAR(30) DEFAULT 'UNVERIFIED'",
                        "logic_last_review_at": "TIMESTAMP",
                    },
                }
                if engine.dialect.name == "sqlite":
                    for table_name, definitions in columns.items():
                        existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))}
                        for column_name, definition in definitions.items():
                            if column_name not in existing:
                                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"))
                else:
                    for table_name, definitions in columns.items():
                        clauses = ", ".join(f"ADD COLUMN IF NOT EXISTS {name} {definition}" for name, definition in definitions.items())
                        conn.execute(text(f"ALTER TABLE {table_name} {clauses}"))
                conn.execute(text("UPDATE watchlist SET logic_status = 'UNVERIFIED' WHERE logic_status IS NULL"))
                conn.execute(text("UPDATE paper_trading SET logic_status = 'UNVERIFIED' WHERE logic_status IS NULL"))
                conn.execute(text("UPDATE paper_trading SET plan_adherence = 'UNKNOWN' WHERE plan_adherence IS NULL"))
                logger.info("Migration: P1 lifecycle and execution audit columns ensured.")
            except Exception as e:
                logger.debug(f"P1 lifecycle migration skipped: {e}")

            # --- Migration: paper trading close audit metadata ---
            try:
                conn.execute(text("""
                    ALTER TABLE paper_trading
                    ADD COLUMN IF NOT EXISTS close_source VARCHAR(50),
                    ADD COLUMN IF NOT EXISTS closed_by VARCHAR(50),
                    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP
                """))
                logger.info("Migration: paper trading close audit columns ensured.")
            except Exception as e:
                logger.debug(f"paper trading close audit migration skipped: {e}")

            # --- Migration: price action P0 fields ---
            try:
                conn.execute(text("""
                    ALTER TABLE scan_history
                    ADD COLUMN IF NOT EXISTS price_action_score FLOAT,
                    ADD COLUMN IF NOT EXISTS price_action_regime VARCHAR(50),
                    ADD COLUMN IF NOT EXISTS price_action_signal VARCHAR(50),
                    ADD COLUMN IF NOT EXISTS price_action_pattern VARCHAR(50),
                    ADD COLUMN IF NOT EXISTS price_action_entry_quality VARCHAR(50),
                    ADD COLUMN IF NOT EXISTS price_action_summary TEXT,
                    ADD COLUMN IF NOT EXISTS pa_entry_price FLOAT,
                    ADD COLUMN IF NOT EXISTS pa_stop_price FLOAT,
                    ADD COLUMN IF NOT EXISTS pa_target_price FLOAT,
                    ADD COLUMN IF NOT EXISTS pa_risk_reward FLOAT,
                    ADD COLUMN IF NOT EXISTS pa_trade_action VARCHAR(20),
                    ADD COLUMN IF NOT EXISTS pa_trade_setup VARCHAR(80),
                    ADD COLUMN IF NOT EXISTS pa_risk_pct FLOAT,
                    ADD COLUMN IF NOT EXISTS price_action_detail JSONB
                """))
                logger.info("Migration: price action columns ensured.")
            except Exception as e:
                logger.debug(f"price action migration skipped (may already exist): {e}")

            # --- Migration: SOP grade snapshots for historical review ---
            try:
                conn.execute(text("""
                    ALTER TABLE scan_history
                    ADD COLUMN IF NOT EXISTS sop_grade VARCHAR(10),
                    ADD COLUMN IF NOT EXISTS sop_quality_score FLOAT,
                    ADD COLUMN IF NOT EXISTS sop_subgrade VARCHAR(10),
                    ADD COLUMN IF NOT EXISTS sop_vetoes JSONB,
                    ADD COLUMN IF NOT EXISTS sop_checks JSONB,
                    ADD COLUMN IF NOT EXISTS sop_bonuses JSONB,
                    ADD COLUMN IF NOT EXISTS sop_risks JSONB
                """))
                if engine.dialect.name == "postgresql":
                    conn.execute(text("""
                        UPDATE scan_history
                        SET
                            sop_grade = COALESCE(sop_grade, price_action_detail->>'sop_grade'),
                            sop_quality_score = COALESCE(
                                sop_quality_score,
                                CASE
                                    WHEN (price_action_detail->>'sop_quality_score') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN (price_action_detail->>'sop_quality_score')::float
                                    ELSE NULL
                                END
                            ),
                            sop_subgrade = COALESCE(sop_subgrade, price_action_detail->>'sop_subgrade'),
                            sop_vetoes = COALESCE(sop_vetoes, price_action_detail->'sop_vetoes'),
                            sop_checks = COALESCE(sop_checks, price_action_detail->'sop_checks'),
                            sop_bonuses = COALESCE(sop_bonuses, price_action_detail->'sop_bonuses'),
                            sop_risks = COALESCE(sop_risks, price_action_detail->'sop_risks')
                        WHERE price_action_detail IS NOT NULL
                    """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_scan_history_sop_grade ON scan_history(sop_grade);"))
                logger.info("Migration: SOP grade snapshot columns ensured.")
            except Exception as e:
                logger.debug(f"SOP grade snapshot migration skipped: {e}")

            # --- Migration: stable scan result grouping ---
            try:
                has_grouping_source_columns = True
                if engine.dialect.name == "sqlite":
                    existing_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(scan_history)"))}
                    has_grouping_source_columns = {"sop_checks", "sop_bonuses"}.issubset(existing_columns)
                    if "result_group" not in existing_columns:
                        conn.execute(text("ALTER TABLE scan_history ADD COLUMN result_group VARCHAR(30)"))
                else:
                    conn.execute(text("""
                        ALTER TABLE scan_history
                        ADD COLUMN IF NOT EXISTS result_group VARCHAR(30)
                    """))
                if has_grouping_source_columns:
                    conn.execute(text("""
                        UPDATE scan_history
                        SET result_group = CASE
                            WHEN COALESCE(CAST(sop_checks AS TEXT), '') LIKE '%复活%'
                              OR COALESCE(CAST(sop_bonuses AS TEXT), '') LIKE '%历史信号复活%'
                                THEN 'HISTORICAL_REVIVAL'
                            WHEN COALESCE(CAST(sop_checks AS TEXT), '') LIKE '%强趋势加速%'
                              OR COALESCE(CAST(sop_bonuses AS TEXT), '') LIKE '%涨停/大阳加速观察%'
                                THEN 'MOMENTUM_WATCH'
                            ELSE 'FORMAL'
                        END
                        WHERE result_group IS NULL
                    """))
                else:
                    conn.execute(text("""
                        UPDATE scan_history SET result_group = 'FORMAL'
                        WHERE result_group IS NULL
                    """))
                if engine.dialect.name == "postgresql":
                    conn.execute(text("""
                        ALTER TABLE scan_history
                        ALTER COLUMN result_group SET DEFAULT 'FORMAL',
                        ALTER COLUMN result_group SET NOT NULL
                    """))
                logger.info("Migration: scan result grouping ensured.")
            except Exception as e:
                logger.debug(f"scan result grouping migration skipped: {e}")

            # --- Migration: lossless scan signal identity and data-date semantics ---
            try:
                if engine.dialect.name == "postgresql":
                    conn.execute(text("""
                        ALTER TABLE scan_history
                        ADD COLUMN IF NOT EXISTS signal_id BIGSERIAL,
                        ADD COLUMN IF NOT EXISTS data_date DATE,
                        ADD COLUMN IF NOT EXISTS scanned_at TIMESTAMP
                    """))
                    conn.execute(text("""
                        UPDATE scan_history
                        SET data_date = COALESCE(data_date, date),
                            scanned_at = COALESCE(scanned_at, date::timestamp),
                            strategy_type = COALESCE(strategy_type, 'squeeze')
                    """))
                    conn.execute(text("""
                        DO $$
                        DECLARE pk_name text;
                        BEGIN
                            SELECT conname INTO pk_name
                            FROM pg_constraint
                            WHERE conrelid = 'scan_history'::regclass AND contype = 'p';
                            IF pk_name IS NOT NULL AND pk_name <> 'scan_history_signal_id_pkey' THEN
                                EXECUTE format('ALTER TABLE scan_history DROP CONSTRAINT %I', pk_name);
                            END IF;
                            IF NOT EXISTS (
                                SELECT 1 FROM pg_constraint
                                WHERE conrelid = 'scan_history'::regclass AND contype = 'p'
                            ) THEN
                                ALTER TABLE scan_history
                                ADD CONSTRAINT scan_history_signal_id_pkey PRIMARY KEY (signal_id);
                            END IF;
                        END $$;
                    """))
                    conn.execute(text("""
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_scan_history_signal
                        ON scan_history(code, data_date, strategy_type)
                    """))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_scan_history_data_date ON scan_history(data_date DESC)"))
                logger.info("Migration: scan signal identity and data_date ensured.")
            except Exception as e:
                logger.debug(f"scan signal identity migration skipped: {e}")

            # --- Migration: Brooks trade-plan snapshots ---
            try:
                conn.execute(text("""
                    ALTER TABLE paper_trading
                    ADD COLUMN IF NOT EXISTS pa_trade_action VARCHAR(20),
                    ADD COLUMN IF NOT EXISTS pa_trade_setup VARCHAR(80),
                    ADD COLUMN IF NOT EXISTS pa_entry_condition TEXT,
                    ADD COLUMN IF NOT EXISTS pa_invalidation TEXT,
                    ADD COLUMN IF NOT EXISTS pa_risk_pct FLOAT
                """))
                conn.execute(text("""
                    ALTER TABLE watchlist
                    ADD COLUMN IF NOT EXISTS pa_trade_action VARCHAR(20),
                    ADD COLUMN IF NOT EXISTS pa_trade_setup VARCHAR(80),
                    ADD COLUMN IF NOT EXISTS pa_entry_condition TEXT,
                    ADD COLUMN IF NOT EXISTS pa_invalidation TEXT,
                    ADD COLUMN IF NOT EXISTS pa_risk_pct FLOAT,
                    ADD COLUMN IF NOT EXISTS last_review_date DATE,
                    ADD COLUMN IF NOT EXISTS watch_decision VARCHAR(30),
                    ADD COLUMN IF NOT EXISTS watch_action TEXT,
                    ADD COLUMN IF NOT EXISTS exit_reason TEXT
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_scan_history_pa_action ON scan_history(pa_trade_action);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_watchlist_pa_action ON watchlist(pa_trade_action);"))
                logger.info("Migration: Brooks trade-plan snapshot columns ensured.")
            except Exception as e:
                logger.debug(f"Brooks trade-plan migration skipped: {e}")

            # --- Recommendation event log for Bark/scan lifecycle review ---
            try:
                if engine.dialect.name == "sqlite":
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS recommendation_events (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            event_date DATE,
                            event_time TIMESTAMP,
                            source VARCHAR(50),
                            code VARCHAR(20),
                            name VARCHAR(50),
                            industry VARCHAR(100),
                            strategy_type VARCHAR(30),
                            recommendation_price FLOAT,
                            score FLOAT,
                            trade_bucket VARCHAR(20),
                            trade_eligible INTEGER,
                            final_trade_score FLOAT,
                            pa_trade_action VARCHAR(20),
                            pa_trade_setup VARCHAR(80),
                            pa_entry_price FLOAT,
                            pa_stop_price FLOAT,
                            sector_strength_score FLOAT,
                            stock_sector_fit_score FLOAT,
                            sector_alignment_score FLOAT,
                            sector_phase VARCHAR(40),
                            market_regime VARCHAR(30),
                            blockers JSON,
                            status VARCHAR(30),
                            created_at TIMESTAMP
                        )
                    """))
                else:
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS recommendation_events (
                            id SERIAL PRIMARY KEY,
                            event_date DATE,
                            event_time TIMESTAMP,
                            source VARCHAR(50),
                            code VARCHAR(20),
                            name VARCHAR(50),
                            industry VARCHAR(100),
                            strategy_type VARCHAR(30),
                            recommendation_price FLOAT,
                            score FLOAT,
                            trade_bucket VARCHAR(20),
                            trade_eligible INTEGER,
                            final_trade_score FLOAT,
                            pa_trade_action VARCHAR(20),
                            pa_trade_setup VARCHAR(80),
                            pa_entry_price FLOAT,
                            pa_stop_price FLOAT,
                            sector_strength_score FLOAT,
                            stock_sector_fit_score FLOAT,
                            sector_alignment_score FLOAT,
                            sector_phase VARCHAR(40),
                            market_regime VARCHAR(30),
                            blockers JSONB,
                            status VARCHAR(30),
                            created_at TIMESTAMP,
                            UNIQUE(event_date, source, code, strategy_type)
                        )
                    """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_recommendation_events_date ON recommendation_events(event_date DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_recommendation_events_code_date ON recommendation_events(code, event_date DESC);"))
                conn.execute(text("""
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_recommendation_events_identity
                    ON recommendation_events(event_date, source, code, strategy_type)
                """))
                event_columns = {
                    "sector_strength_score": "FLOAT",
                    "stock_sector_fit_score": "FLOAT",
                    "sector_alignment_score": "FLOAT",
                }
                if engine.dialect.name == "sqlite":
                    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(recommendation_events)"))}
                    for column_name, definition in event_columns.items():
                        if column_name not in existing:
                            conn.execute(text(f"ALTER TABLE recommendation_events ADD COLUMN {column_name} {definition}"))
                else:
                    clauses = ", ".join(f"ADD COLUMN IF NOT EXISTS {name} {definition}" for name, definition in event_columns.items())
                    conn.execute(text(f"ALTER TABLE recommendation_events {clauses}"))
                logger.info("Migration: recommendation event log ensured.")
            except Exception as e:
                logger.debug(f"recommendation event migration skipped: {e}")

            # --- AI candidate review persistence (manual + scheduled batches) ---
            try:
                id_type = "INTEGER PRIMARY KEY AUTOINCREMENT" if engine.dialect.name == "sqlite" else "SERIAL PRIMARY KEY"
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS ai_candidate_reviews (
                        id {id_type},
                        review_date VARCHAR(10) NOT NULL,
                        code VARCHAR(10) NOT NULL,
                        name VARCHAR(40),
                        action VARCHAR(10) NOT NULL,
                        confidence INTEGER,
                        summary TEXT,
                        positive_factors TEXT,
                        risk_factors TEXT,
                        data_limitations TEXT,
                        guardrail_adjusted INTEGER DEFAULT 0,
                        entry_price FLOAT,
                        stop_price FLOAT,
                        target_price FLOAT,
                        market_summary TEXT,
                        model VARCHAR(80),
                        source VARCHAR(20) NOT NULL,
                        batch_id VARCHAR(48) NOT NULL,
                        created_at TIMESTAMP,
                        UNIQUE(review_date, code, source)
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ai_candidate_reviews_date ON ai_candidate_reviews(review_date DESC, id DESC);"))
                logger.info("Migration: ai_candidate_reviews ensured.")
            except Exception as e:
                logger.debug(f"ai_candidate_reviews migration skipped: {e}")

            # --- Intraday limit-up / broken-board leadership evidence ---
            try:
                id_type = "INTEGER PRIMARY KEY AUTOINCREMENT" if engine.dialect.name == "sqlite" else "SERIAL PRIMARY KEY"
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS limit_up_events (
                        id {id_type},
                        event_date DATE NOT NULL,
                        code VARCHAR(20) NOT NULL,
                        name VARCHAR(80),
                        industry VARCHAR(100),
                        status VARCHAR(20) NOT NULL,
                        first_limit_time VARCHAR(6),
                        last_limit_time VARCHAR(6),
                        break_count INTEGER DEFAULT 0,
                        limit_up_streak INTEGER DEFAULT 0,
                        seal_amount FLOAT DEFAULT 0,
                        turnover FLOAT DEFAULT 0,
                        amount FLOAT DEFAULT 0,
                        first_seen_at TIMESTAMP,
                        last_seen_at TIMESTAMP,
                        UNIQUE(event_date, code)
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_limit_up_events_date ON limit_up_events(event_date DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_limit_up_events_code_date ON limit_up_events(code, event_date DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_limit_up_events_industry_date ON limit_up_events(industry, event_date DESC);"))
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS intraday_minute_bars (
                        id {id_type},
                        code VARCHAR(20) NOT NULL,
                        bar_time VARCHAR(19) NOT NULL,
                        open FLOAT,
                        close FLOAT,
                        high FLOAT,
                        low FLOAT,
                        volume FLOAT,
                        amount FLOAT,
                        average_price FLOAT,
                        created_at TIMESTAMP,
                        UNIQUE(code, bar_time)
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_intraday_minute_bars_time ON intraday_minute_bars(bar_time DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_intraday_minute_bars_code_time ON intraday_minute_bars(code, bar_time DESC);"))
                logger.info("Migration: limit-up leadership events ensured.")
            except Exception as e:
                logger.debug(f"limit-up event migration skipped: {e}")

            # ── breadth_history：盘中实时聚合的市场/板块宽度历史 ──
            # 修复 6/22 节后首日 bug：原 build_sector_history_context / load_market_cycle_history
            # 直接读 daily_k 表，节后首日 daily_k 还是上个交易日数据，导致板块 slope 和市场宽度
            # 用滞后数据误判（证券板块暴涨却判 SECTOR_FADE）。本表由 record_breadth_snapshot
            # 在每次扫描后写入今日实时聚合，读函数优先读此表，新表空时回退 daily_k（向下兼容）。
            try:
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS breadth_history (
                        bar_date DATE NOT NULL,
                        scope VARCHAR(10) NOT NULL,
                        industry VARCHAR(50),
                        advance_ratio FLOAT,
                        strong_ratio FLOAT,
                        weak_ratio FLOAT,
                        avg_return FLOAT,
                        limit_up_ratio FLOAT,
                        total_count INTEGER,
                        updated_at TIMESTAMP,
                        UNIQUE(bar_date, scope, industry)
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_breadth_history_date_scope ON breadth_history(bar_date DESC, scope);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_breadth_history_industry_date ON breadth_history(industry, bar_date DESC);"))
                logger.info("Migration: breadth_history table ensured.")
            except Exception as e:
                logger.debug(f"breadth_history migration skipped: {e}")

            # 修复 R3-2: paper_trading 的 ON CONFLICT (code, entry_date) 需要唯一约束。
            # 原来只在测试中建了这个索引，生产 PG 上缺约束→运行时报错。
            try:
                conn.execute(text("""
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_trade_code_date
                    ON paper_trading (code, entry_date)
                """))
                logger.info("Migration: paper_trading unique index (code, entry_date) ensured.")
            except Exception as e:
                logger.debug(f"paper_trading unique index migration skipped: {e}")

            # 筹码分布需要历史换手率。仅增加可空列，旧行情与交易记录保持不变。
            try:
                if engine.dialect.name == "sqlite":
                    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(daily_k)"))}
                    if "turnover" not in existing:
                        conn.execute(text("ALTER TABLE daily_k ADD COLUMN turnover FLOAT"))
                else:
                    conn.execute(text("ALTER TABLE daily_k ADD COLUMN IF NOT EXISTS turnover FLOAT"))
                conn.execute(text("""
                    INSERT INTO schema_migrations(version, applied_at, description)
                    VALUES ('2026-09-09-daily-k-turnover-v1', CURRENT_TIMESTAMP, 'add historical turnover for estimated chip distribution')
                    ON CONFLICT(version) DO NOTHING
                """))
                logger.info("Migration: daily_k turnover column ensured.")
            except Exception as e:
                logger.debug(f"daily_k turnover migration skipped: {e}")

            conn.commit()
    except Exception as e:
        logger.error(f"Database init failed: {e}")


def claim_task_slot(slot_key: str, engine=None) -> bool:
    """Atomically claim one deterministic task slot; false means it already ran/started."""
    engine = engine or get_db_engine()
    if engine is None or not slot_key:
        return False
    try:
        with engine.begin() as conn:
            result = conn.execute(text("""
                INSERT INTO task_slot_claims(slot_key, claimed_at, status)
                VALUES (:slot_key, CURRENT_TIMESTAMP, 'CLAIMED')
                ON CONFLICT(slot_key) DO NOTHING
            """), {"slot_key": str(slot_key)[:160]})
        return bool(result.rowcount)
    except Exception as exc:
        logger.warning(f"Task slot claim unavailable: {exc}")
        return False


def save_point_in_time_snapshot(
    snapshot: pd.DataFrame,
    dataset_version: str,
    as_of: Any,
    data_mode: str,
    engine=None,
) -> int:
    """Persist the stock-state inputs used by one scan without changing market/trade tables."""
    if snapshot is None or snapshot.empty or not dataset_version:
        return 0
    engine = engine or get_db_engine()
    if engine is None:
        return 0
    try:
        with engine.connect() as conn:
            existing_count = int(conn.execute(text("""
                SELECT COUNT(*) FROM point_in_time_stock_snapshots
                WHERE dataset_version = :dataset_version
            """), {"dataset_version": dataset_version}).scalar() or 0)
        if existing_count >= len(snapshot):
            return existing_count
    except Exception:
        # The following insert remains the source of truth and will report failure.
        pass
    rows = []
    for _, item in snapshot.iterrows():
        code = str(item.get("code") or "").zfill(6)
        if not validate_stock_code(code):
            continue
        name = str(item.get("name") or code)
        rows.append({
            "dataset_version": dataset_version,
            "as_of": as_of,
            "data_mode": data_mode,
            "code": code,
            "name": name,
            "industry": item.get("industry"),
            "is_st_or_delist": int(bool(re.search(r"ST|退", name, re.IGNORECASE))),
            "turnover": None if pd.isna(item.get("turnover")) else float(item.get("turnover")),
            "mkt_cap": None if pd.isna(item.get("mkt_cap")) else float(item.get("mkt_cap")),
            "source": item.get("source") or data_mode,
            "price": None if pd.isna(item.get("price")) else float(item.get("price")),
            "open": None if pd.isna(item.get("open")) else float(item.get("open")),
            "high": None if pd.isna(item.get("high")) else float(item.get("high")),
            "low": None if pd.isna(item.get("low")) else float(item.get("low")),
            "pct_chg": None if pd.isna(item.get("pct_chg")) else float(item.get("pct_chg")),
            "vol": None if pd.isna(item.get("vol")) else float(item.get("vol")),
            "amount": None if pd.isna(item.get("amount")) else float(item.get("amount")),
            "limit_up": None if pd.isna(item.get("limit_up")) else float(item.get("limit_up")),
            "limit_down": None if pd.isna(item.get("limit_down")) else float(item.get("limit_down")),
            "created_at": datetime.now(),
        })
    if not rows:
        return 0
    statement = text("""
        INSERT INTO point_in_time_stock_snapshots
            (dataset_version, as_of, data_mode, code, name, industry, is_st_or_delist,
             turnover, mkt_cap, source, price, open, high, low, pct_chg, vol,
             amount, limit_up, limit_down, created_at)
        VALUES
            (:dataset_version, :as_of, :data_mode, :code, :name, :industry, :is_st_or_delist,
             :turnover, :mkt_cap, :source, :price, :open, :high, :low, :pct_chg, :vol,
             :amount, :limit_up, :limit_down, :created_at)
        ON CONFLICT(dataset_version, code) DO UPDATE SET
            name=EXCLUDED.name, industry=EXCLUDED.industry,
            is_st_or_delist=EXCLUDED.is_st_or_delist, turnover=EXCLUDED.turnover,
            mkt_cap=EXCLUDED.mkt_cap, source=EXCLUDED.source,
            price=EXCLUDED.price, open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
            pct_chg=EXCLUDED.pct_chg, vol=EXCLUDED.vol, amount=EXCLUDED.amount,
            limit_up=EXCLUDED.limit_up, limit_down=EXCLUDED.limit_down
    """)
    try:
        with engine.begin() as conn:
            conn.execute(statement, rows)
        return len(rows)
    except Exception as exc:
        logger.error(f"Failed to save point-in-time snapshot: {exc}")
        return 0


def load_recent_point_in_time_snapshot(max_age_minutes: float = 5, engine=None) -> pd.DataFrame:
    """Load the latest complete, same-day live snapshot for cross-worker failover."""
    engine = engine or get_db_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        with engine.connect() as conn:
            latest = conn.execute(text("""
                SELECT dataset_version, MAX(as_of) AS as_of, MAX(source) AS source, COUNT(*) AS row_count
                FROM point_in_time_stock_snapshots
                WHERE data_mode = 'LIVE_SNAPSHOT' AND price IS NOT NULL AND pct_chg IS NOT NULL
                GROUP BY dataset_version
                ORDER BY MAX(as_of) DESC
                LIMIT 1
            """)).mappings().first()
            if not latest or int(latest["row_count"] or 0) < 4000:
                return pd.DataFrame()
            as_of = pd.to_datetime(latest["as_of"]).to_pydatetime()
            now = datetime.now()
            if as_of.date() != now.date() or (now - as_of).total_seconds() > max_age_minutes * 60:
                return pd.DataFrame()
            snapshot = pd.read_sql(text("""
                SELECT code, name, industry, price, open, high, low, pct_chg, vol,
                       amount, turnover, mkt_cap, limit_up, limit_down
                FROM point_in_time_stock_snapshots
                WHERE dataset_version = :dataset_version
                ORDER BY code
            """), conn, params={"dataset_version": latest["dataset_version"]})
        snapshot.attrs = {
            "fetched_at": as_of,
            "data_date": as_of.strftime("%Y-%m-%d"),
            "source": f"持久化短时快照·{latest['source'] or '未知源'}",
        }
        return snapshot
    except Exception as exc:
        logger.warning(f"Recent point-in-time snapshot unavailable: {exc}")
        return pd.DataFrame()


def save_event_catalyst(event: Dict[str, Any], engine=None) -> bool:
    engine = engine or get_db_engine()
    if engine is None or not validate_stock_code(str(event.get("code") or "")):
        return False
    payload = {
        "code": str(event["code"]),
        "event_type": str(event.get("event_type") or "EARNINGS_SURPRISE"),
        "published_at": event.get("published_at"),
        "title": event.get("title"),
        "profit_growth_low": event.get("profit_growth_low"),
        "profit_growth_high": event.get("profit_growth_high"),
        "source_url": event.get("source_url"),
        "verified": int(bool(event.get("verified"))),
        "metadata_json": json.dumps(_json_safe(event.get("metadata") or {}), ensure_ascii=False),
        "created_at": datetime.now(),
    }
    try:
        with engine.begin() as conn:
            json_value = ":metadata_json" if engine.dialect.name == "sqlite" else "CAST(:metadata_json AS JSONB)"
            conn.execute(text(f"""
                INSERT INTO event_catalysts
                    (code, event_type, published_at, title, profit_growth_low, profit_growth_high,
                     source_url, verified, metadata_json, created_at)
                VALUES
                    (:code, :event_type, :published_at, :title, :profit_growth_low, :profit_growth_high,
                     :source_url, :verified, {json_value}, :created_at)
                ON CONFLICT(code, published_at, event_type) DO UPDATE SET
                    title=EXCLUDED.title, profit_growth_low=EXCLUDED.profit_growth_low,
                    profit_growth_high=EXCLUDED.profit_growth_high, source_url=EXCLUDED.source_url,
                    verified=EXCLUDED.verified, metadata_json=EXCLUDED.metadata_json
            """), payload)
        return True
    except Exception as exc:
        logger.error(f"Failed to save event catalyst: {exc}")
        return False


def load_active_event_catalysts(engine=None, as_of: Any = None, days: int = 10) -> Dict[str, Dict[str, Any]]:
    engine = engine or get_db_engine()
    if engine is None:
        return {}
    as_of_date = pd.Timestamp(as_of or datetime.now()).date()
    start_date = as_of_date - pd.Timedelta(days=max(1, min(int(days), 90)))
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT code, event_type, published_at, title, profit_growth_low,
                       profit_growth_high, source_url, verified, metadata_json
                FROM event_catalysts
                WHERE verified = 1 AND published_at >= :start_date AND published_at < :end_date
                ORDER BY published_at DESC
            """), {"start_date": start_date, "end_date": as_of_date + pd.Timedelta(days=1)}).mappings().all()
        active: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            active.setdefault(str(row["code"]), dict(row))
        return active
    except Exception as exc:
        logger.warning(f"Failed to load event catalysts: {exc}")
        return {}


def save_scan_audit_log(audit: Dict[str, Any], engine=None) -> bool:
    if engine is None:
        engine = get_db_engine()
    if not engine:
        return False

    payload = {
        "scan_date": audit.get("scan_date"),
        "started_at": audit.get("started_at"),
        "finished_at": audit.get("finished_at"),
        "duration_sec": audit.get("duration_sec"),
        "status": audit.get("status") or "SUCCESS",
        "strategy_type": audit.get("strategy_type"),
        "params_snapshot": json.dumps(_json_safe(audit.get("params_snapshot") or {}), ensure_ascii=False),
        "version_snapshot": json.dumps(_json_safe(audit.get("version_snapshot") or {}), ensure_ascii=False),
        "total_snapshot": int(audit.get("total_snapshot") or 0),
        "candidate_count": int(audit.get("candidate_count") or 0),
        "result_count": int(audit.get("result_count") or 0),
        "fail_reasons": json.dumps(_json_safe(audit.get("fail_reasons") or {}), ensure_ascii=False),
        "error_message": audit.get("error_message"),
        "as_of": audit.get("as_of"),
        "data_mode": audit.get("data_mode"),
        "field_coverage": json.dumps(_json_safe(audit.get("field_coverage") or {}), ensure_ascii=False),
        "effective_filters": json.dumps(_json_safe(audit.get("effective_filters") or []), ensure_ascii=False),
        "research_only": int(bool(audit.get("research_only"))),
        "degradation_reasons": json.dumps(_json_safe(audit.get("degradation_reasons") or []), ensure_ascii=False),
    }

    try:
        with engine.connect() as conn:
            if engine.dialect.name == "sqlite":
                conn.execute(text("""
                    INSERT INTO scan_audit_log (
                        scan_date, started_at, finished_at, duration_sec, status, strategy_type,
                        params_snapshot, version_snapshot, total_snapshot, candidate_count,
                        result_count, fail_reasons, error_message, as_of, data_mode,
                        field_coverage, effective_filters, research_only, degradation_reasons
                    ) VALUES (
                        :scan_date, :started_at, :finished_at, :duration_sec, :status, :strategy_type,
                        :params_snapshot, :version_snapshot, :total_snapshot, :candidate_count,
                        :result_count, :fail_reasons, :error_message, :as_of, :data_mode,
                        :field_coverage, :effective_filters, :research_only, :degradation_reasons
                    )
                """), payload)
            else:
                conn.execute(text("""
                    INSERT INTO scan_audit_log (
                        scan_date, started_at, finished_at, duration_sec, status, strategy_type,
                        params_snapshot, version_snapshot, total_snapshot, candidate_count,
                        result_count, fail_reasons, error_message, as_of, data_mode,
                        field_coverage, effective_filters, research_only, degradation_reasons
                    ) VALUES (
                        CAST(:scan_date AS DATE), :started_at, :finished_at, :duration_sec, :status, :strategy_type,
                        CAST(:params_snapshot AS JSONB), CAST(:version_snapshot AS JSONB), :total_snapshot, :candidate_count,
                        :result_count, CAST(:fail_reasons AS JSONB), :error_message, :as_of, :data_mode,
                        CAST(:field_coverage AS JSONB), CAST(:effective_filters AS JSONB), :research_only,
                        CAST(:degradation_reasons AS JSONB)
                    )
                """), payload)
            conn.commit()
        return True
    except Exception as exc:
        logger.error(f"Failed to save scan audit log: {exc}")
        return False


def save_failure_sample(sample: Dict[str, Any], engine=None) -> bool:
    if engine is None:
        engine = get_db_engine()
    if not engine:
        return False

    try:
        with engine.connect() as conn:
            date_expr = ":sample_date" if engine.dialect.name == "sqlite" else "CAST(:sample_date AS DATE)"
            exists = conn.execute(text(f"""
                SELECT 1 FROM failure_samples
                WHERE code = :code
                  AND sample_date = {date_expr}
                  AND COALESCE(failure_type, '') = COALESCE(:failure_type, '')
                  AND COALESCE(source, '') = COALESCE(:source, '')
                  AND ABS(COALESCE(pnl_pct, 0) - COALESCE(:pnl_pct, 0)) < 0.001
                LIMIT 1
            """), {
                "code": sample.get("code"),
                "sample_date": sample.get("sample_date") or datetime.now().strftime("%Y-%m-%d"),
                "failure_type": sample.get("failure_type"),
                "source": sample.get("source") or "paper_trade",
                "pnl_pct": sample.get("pnl_pct"),
            }).fetchone()
            if exists:
                return True
            conn.execute(text(f"""
                INSERT INTO failure_samples (
                    code, name, sample_date, strategy_type, failure_type,
                    reason, pnl_pct, source, created_at
                ) VALUES (
                    :code, :name, {date_expr}, :strategy_type, :failure_type,
                    :reason, :pnl_pct, :source, :created_at
                )
            """), {
                "code": sample.get("code"),
                "name": sample.get("name"),
                "sample_date": sample.get("sample_date") or datetime.now().strftime("%Y-%m-%d"),
                "strategy_type": sample.get("strategy_type"),
                "failure_type": sample.get("failure_type"),
                "reason": sample.get("reason"),
                "pnl_pct": sample.get("pnl_pct"),
                "source": sample.get("source") or "paper_trade",
                "created_at": datetime.now(),
            })
            conn.commit()
        return True
    except Exception as exc:
        logger.error(f"Failed to save failure sample: {exc}")
        return False


def record_breadth_snapshot(
    snapshot_df: pd.DataFrame,
    sector_map: Dict[str, str],
    engine=None,
    bar_date: Optional[str] = None,
) -> None:
    """把实时快照聚合成市场/板块宽度，upsert 进 breadth_history 表。

    修复 6/22 节后首日 bug：盘中扫描时板块/市场层面的历史数据直接读 daily_k，
    但节后首日 daily_k 滞后，导致 sector_trend_slope 和市场宽度误判。本函数在
    每次扫描后写入今日实时聚合，让后续读取拿到新鲜数据。

    写入两级行：
      - scope='MARKET'  industry=NULL  全市场宽度
      - scope='SECTOR'  industry=板块名  各板块宽度

    安全契约：失败只 log 不抛，绝不阻断扫描主流程。

    Args:
        snapshot_df: 实时快照（含 code, pct_chg, industry 等列）
        sector_map: {code: 板块名} 映射；snapshot_df 已含 industry 列时可为空
        engine: DB engine；为空时静默跳过
        bar_date: 交易日字符串；为空时用今日
    """
    if engine is None or snapshot_df is None or snapshot_df.empty:
        return
    try:
        today = bar_date or datetime.now().strftime("%Y-%m-%d")
        pct = pd.to_numeric(snapshot_df["pct_chg"], errors="coerce").dropna()
        if pct.empty:
            return
        total = len(pct)
        rows = []

        # MARKET 级别（industry 用 '__MARKET__' 哨兵值，避免 NULL 在 ON CONFLICT 的歧义）
        rows.append({
            "bar_date": today, "scope": "MARKET", "industry": "__MARKET__",
            "advance_ratio": round(float((pct > 0).sum() / total * 100), 1),
            "strong_ratio": round(float((pct >= 5).sum() / total * 100), 1),
            "weak_ratio": round(float((pct <= -5).sum() / total * 100), 1),
            "avg_return": round(float(pct.mean()), 2),
            "limit_up_ratio": round(float((pct >= 9.8).sum() / total * 100), 1),
            "total_count": int(total),
            "updated_at": datetime.now(),
        })

        # SECTOR 级别：优先用 snapshot_df 自带的 industry 列，回退到 sector_map
        if "industry" in snapshot_df.columns:
            industries = snapshot_df.assign(
                _pct=pct.reindex(snapshot_df.index)
            ).dropna(subset=["_pct"])
            industries = industries.assign(
                industry=industries["industry"].fillna(
                    industries["code"].map(sector_map)
                ).fillna("未知")
            )
        else:
            industries = snapshot_df.assign(
                _pct=pct.reindex(snapshot_df.index),
                industry=snapshot_df["code"].map(sector_map).fillna("未知"),
            ).dropna(subset=["_pct"])

        for industry, group in industries.groupby("industry"):
            g_pct = pd.to_numeric(group["_pct"], errors="coerce").dropna()
            if g_pct.empty:
                continue
            g_total = len(g_pct)
            rows.append({
                "bar_date": today, "scope": "SECTOR", "industry": str(industry),
                "advance_ratio": round(float((g_pct > 0).sum() / g_total * 100), 1),
                "strong_ratio": round(float((g_pct >= 5).sum() / g_total * 100), 1),
                "weak_ratio": round(float((g_pct <= -5).sum() / g_total * 100), 1),
                "avg_return": round(float(g_pct.mean()), 2),
                "limit_up_ratio": None,
                "total_count": int(g_total),
                "updated_at": datetime.now(),
            })

        with engine.begin() as conn:
            for r in rows:
                conn.execute(text("""
                    INSERT INTO breadth_history
                        (bar_date, scope, industry, advance_ratio, strong_ratio,
                         weak_ratio, avg_return, limit_up_ratio, total_count, updated_at)
                    VALUES
                        (:bar_date, :scope, :industry, :advance_ratio, :strong_ratio,
                         :weak_ratio, :avg_return, :limit_up_ratio, :total_count, :updated_at)
                    ON CONFLICT(bar_date, scope, industry) DO UPDATE SET
                        advance_ratio=EXCLUDED.advance_ratio,
                        strong_ratio=EXCLUDED.strong_ratio,
                        weak_ratio=EXCLUDED.weak_ratio,
                        avg_return=EXCLUDED.avg_return,
                        limit_up_ratio=EXCLUDED.limit_up_ratio,
                        total_count=EXCLUDED.total_count,
                        updated_at=EXCLUDED.updated_at
                """), r)
        logger.debug(f"breadth_history updated: {len(rows)} rows for {today}")
    except Exception as exc:
        logger.warning(f"record_breadth_snapshot failed (scan continues): {exc}")


def save_recommendation_events(
    results: List[Dict[str, Any]],
    engine=None,
    source: str = "scan",
    event_date: Optional[str] = None,
    market_regime: Optional[str] = None,
) -> bool:
    """Persist scan/Bark recommendations as event records for lifecycle review."""
    if engine is None:
        engine = get_db_engine()
    if not engine or not results:
        return False

    event_date = event_date or datetime.now().strftime("%Y-%m-%d")
    rows = []
    for r in results:
        detail = r.get("price_action_detail") or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}
        pa_plan = r.get("pa_trade_plan") or detail.get("pa_trade_plan") or {}
        trade_bucket = r.get("trade_bucket") or detail.get("trade_bucket") or "UNKNOWN"
        blockers = r.get("trade_blockers") or detail.get("trade_blockers") or []
        rows.append({
            "event_date": event_date,
            "event_time": datetime.now(),
            "source": source,
            "code": r.get("代码") or r.get("code"),
            "name": r.get("名称") or r.get("name"),
            "industry": r.get("行业") or r.get("industry") or "未知",
            "strategy_type": r.get("strategy_type") or "squeeze",
            "recommendation_price": float(r.get("现价") or r.get("price") or 0),
            "score": float(r.get("Score") or r.get("score") or 0),
            "trade_bucket": trade_bucket,
            "trade_eligible": 1 if (r.get("trade_eligible") or detail.get("trade_eligible")) else 0,
            "final_trade_score": float(r.get("final_trade_score") or detail.get("final_trade_score") or 0),
            "pa_trade_action": pa_plan.get("action") or r.get("pa_trade_action"),
            "pa_trade_setup": pa_plan.get("setup") or r.get("pa_trade_setup"),
            "pa_entry_price": float(r.get("pa_entry_price") or detail.get("pa_entry_price") or 0) or None,
            "pa_stop_price": float(r.get("pa_stop_price") or detail.get("pa_stop_price") or 0) or None,
            "sector_strength_score": float(r.get("sector_strength_score") or detail.get("sector_strength_score") or 0) or None,
            "stock_sector_fit_score": float(r.get("stock_sector_fit_score") or detail.get("stock_sector_fit_score") or 0) or None,
            "sector_alignment_score": float(r.get("sector_alignment_score") or detail.get("sector_alignment_score") or 0) or None,
            "sector_phase": r.get("sector_phase") or detail.get("sector_phase"),
            "market_regime": market_regime,
            "blockers": json.dumps(_json_safe(blockers), ensure_ascii=False),
            "status": "OPEN" if trade_bucket in {"TRADE", "WATCH"} else "FILTERED",
            "created_at": datetime.now(),
        })

    try:
        with engine.connect() as conn:
            if engine.dialect.name == "sqlite":
                conn.execute(text("""
                    INSERT INTO recommendation_events (
                        event_date, event_time, source, code, name, industry, strategy_type,
                        recommendation_price, score, trade_bucket, trade_eligible, final_trade_score,
                        pa_trade_action, pa_trade_setup, pa_entry_price, pa_stop_price,
                        sector_strength_score, stock_sector_fit_score, sector_alignment_score,
                        sector_phase, market_regime, blockers, status, created_at
                    ) VALUES (
                        :event_date, :event_time, :source, :code, :name, :industry, :strategy_type,
                        :recommendation_price, :score, :trade_bucket, :trade_eligible, :final_trade_score,
                        :pa_trade_action, :pa_trade_setup, :pa_entry_price, :pa_stop_price,
                        :sector_strength_score, :stock_sector_fit_score, :sector_alignment_score,
                        :sector_phase, :market_regime, :blockers, :status, :created_at
                    )
                    ON CONFLICT (event_date, source, code, strategy_type) DO UPDATE SET
                        event_time = excluded.event_time,
                        recommendation_price = excluded.recommendation_price,
                        score = excluded.score,
                        trade_bucket = excluded.trade_bucket,
                        final_trade_score = excluded.final_trade_score,
                        sector_strength_score = excluded.sector_strength_score,
                        stock_sector_fit_score = excluded.stock_sector_fit_score,
                        sector_alignment_score = excluded.sector_alignment_score,
                        blockers = excluded.blockers,
                        status = excluded.status
                """), rows)
            else:
                conn.execute(text("""
                    INSERT INTO recommendation_events (
                        event_date, event_time, source, code, name, industry, strategy_type,
                        recommendation_price, score, trade_bucket, trade_eligible, final_trade_score,
                        pa_trade_action, pa_trade_setup, pa_entry_price, pa_stop_price,
                        sector_strength_score, stock_sector_fit_score, sector_alignment_score,
                        sector_phase, market_regime, blockers, status, created_at
                    ) VALUES (
                        CAST(:event_date AS DATE), :event_time, :source, :code, :name, :industry, :strategy_type,
                        :recommendation_price, :score, :trade_bucket, :trade_eligible, :final_trade_score,
                        :pa_trade_action, :pa_trade_setup, :pa_entry_price, :pa_stop_price,
                        :sector_strength_score, :stock_sector_fit_score, :sector_alignment_score,
                        :sector_phase, :market_regime, CAST(:blockers AS JSONB), :status, :created_at
                    )
                    ON CONFLICT (event_date, source, code, strategy_type) DO UPDATE SET
                        event_time = EXCLUDED.event_time,
                        recommendation_price = EXCLUDED.recommendation_price,
                        score = EXCLUDED.score,
                        trade_bucket = EXCLUDED.trade_bucket,
                        final_trade_score = EXCLUDED.final_trade_score,
                        sector_strength_score = EXCLUDED.sector_strength_score,
                        stock_sector_fit_score = EXCLUDED.stock_sector_fit_score,
                        sector_alignment_score = EXCLUDED.sector_alignment_score,
                        blockers = EXCLUDED.blockers,
                        status = EXCLUDED.status
                """), rows)
            conn.commit()
        return True
    except Exception as exc:
        logger.error(f"Failed to save recommendation events: {exc}")
        return False


def save_ai_candidate_reviews(
    analyses: List[Dict[str, Any]],
    *,
    review_date: str,
    model: str = "",
    market_summary: str = "",
    source: str = "manual",
    engine=None,
) -> Optional[Dict[str, Any]]:
    """Persist one AI review batch; upserts per (review_date, code, source)."""
    if engine is None:
        engine = get_db_engine()
    if not engine or not analyses:
        return None
    batch_id = f"{source}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    rows = []
    for item in analyses:
        levels = item.get("system_levels") if isinstance(item.get("system_levels"), dict) else {}
        rows.append({
            "review_date": str(review_date)[:10],
            "code": str(item.get("code") or "").zfill(6),
            "name": str(item.get("name") or "")[:40],
            "action": str(item.get("action") or "WAIT")[:10],
            "confidence": int(item.get("confidence") or 0),
            "summary": str(item.get("summary") or "")[:500],
            "positive_factors": json.dumps(item.get("positive_factors") or [], ensure_ascii=False),
            "risk_factors": json.dumps(item.get("risk_factors") or [], ensure_ascii=False),
            "data_limitations": json.dumps(item.get("data_limitations") or [], ensure_ascii=False),
            "guardrail_adjusted": 1 if item.get("guardrail_adjusted") else 0,
            "entry_price": levels.get("entry_price"),
            "stop_price": levels.get("stop_price"),
            "target_price": levels.get("target_price"),
            "market_summary": str(market_summary or "")[:500],
            "model": str(model or "")[:80],
            "source": str(source or "manual")[:20],
            "batch_id": batch_id,
            "created_at": datetime.now(),
        })
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO ai_candidate_reviews (
                    review_date, code, name, action, confidence, summary,
                    positive_factors, risk_factors, data_limitations, guardrail_adjusted,
                    entry_price, stop_price, target_price, market_summary,
                    model, source, batch_id, created_at
                ) VALUES (
                    :review_date, :code, :name, :action, :confidence, :summary,
                    :positive_factors, :risk_factors, :data_limitations, :guardrail_adjusted,
                    :entry_price, :stop_price, :target_price, :market_summary,
                    :model, :source, :batch_id, :created_at
                )
                ON CONFLICT (review_date, code, source) DO UPDATE SET
                    name = excluded.name,
                    action = excluded.action,
                    confidence = excluded.confidence,
                    summary = excluded.summary,
                    positive_factors = excluded.positive_factors,
                    risk_factors = excluded.risk_factors,
                    data_limitations = excluded.data_limitations,
                    guardrail_adjusted = excluded.guardrail_adjusted,
                    entry_price = excluded.entry_price,
                    stop_price = excluded.stop_price,
                    target_price = excluded.target_price,
                    market_summary = excluded.market_summary,
                    model = excluded.model,
                    batch_id = excluded.batch_id,
                    created_at = excluded.created_at
            """), rows)
        return {"batch_id": batch_id, "count": len(rows)}
    except Exception as exc:
        logger.error(f"Failed to save AI candidate reviews: {exc}")
        return None


def get_latest_ai_candidate_reviews(review_date: str, engine=None) -> Optional[Dict[str, Any]]:
    """Return the most recent AI review batch for one review date."""
    if engine is None:
        engine = get_db_engine()
    if not engine or not review_date:
        return None
    try:
        with engine.connect() as conn:
            batch_id = conn.execute(text(
                "SELECT batch_id FROM ai_candidate_reviews WHERE review_date = :d "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ), {"d": str(review_date)[:10]}).scalar()
            if not batch_id:
                return None
            rows = conn.execute(text(
                "SELECT * FROM ai_candidate_reviews WHERE batch_id = :b ORDER BY id ASC"
            ), {"b": batch_id}).mappings().all()
    except Exception as exc:
        logger.error(f"Failed to load AI candidate reviews: {exc}")
        return None
    if not rows:
        return None
    action_order = {"BUY": 0, "WAIT": 1, "AVOID": 2}

    def _parse_list(raw: Any) -> List[str]:
        if isinstance(raw, list):
            return raw
        try:
            parsed = json.loads(str(raw or "[]"))
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    analyses = [{
        "code": row["code"],
        "name": row["name"],
        "action": row["action"],
        "confidence": int(row["confidence"] or 0),
        "summary": row["summary"],
        "positive_factors": _parse_list(row["positive_factors"]),
        "risk_factors": _parse_list(row["risk_factors"]),
        "data_limitations": _parse_list(row["data_limitations"]),
        "guardrail_adjusted": bool(row["guardrail_adjusted"]),
        "system_levels": {
            "entry_price": row["entry_price"],
            "stop_price": row["stop_price"],
            "target_price": row["target_price"],
        },
    } for row in rows]
    analyses.sort(key=lambda item: (action_order.get(item["action"], 3), -item["confidence"]))
    first = rows[0]
    return {
        "batch_id": batch_id,
        "review_date": first["review_date"],
        "model": first["model"],
        "source": first["source"],
        "market_summary": first["market_summary"],
        "analyses": analyses,
    }

def save_to_db(df: pd.DataFrame, code: str, engine=None) -> bool:
    """
    将数据保存到 PostgreSQL (增量)

    Args:
        df: DataFrame with OHLCV columns and optional '换手率'
        code: Stock code (validated)
        engine: Database engine (optional)

    Returns:
        True if successful, False otherwise
    """
    # Validate stock code to prevent SQL injection
    if not validate_stock_code(code):
        logger.error(f"Invalid stock code format: {code}")
        return False

    if engine is None:
        engine = get_db_engine()
    if not engine or df.empty:
        return False

    try:
        columns = ['日期', '开盘', '最高', '最低', '收盘', '成交量']
        data = df[columns].copy()
        data['换手率'] = pd.to_numeric(df['换手率'], errors='coerce') if '换手率' in df.columns else None
        data['code'] = code
        data = data.rename(columns={
            '日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low',
            '收盘': 'close', '成交量': 'vol', '换手率': 'turnover',
        })

        # 统一将 date 列转为字符串，确保数据库能够一致解析
        data['date'] = data['date'].astype(str)

        # 转换为记录字典列表，使用批量参数化 INSERT 替代临时表以确保线程/事务安全
        rows = data.to_dict('records')

        with engine.connect() as conn:
            conn.execute(text('''
                INSERT INTO daily_k (code, date, open, high, low, close, vol, turnover)
                VALUES (:code, CAST(:date AS DATE), :open, :high, :low, :close, :vol, :turnover)
                ON CONFLICT (code, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    vol = EXCLUDED.vol,
                    turnover = COALESCE(EXCLUDED.turnover, daily_k.turnover)
            '''), rows)
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"save_to_db Error ({code}): {e}")
        return False

def load_from_db(code: str, start_date: str, engine=None) -> pd.DataFrame:
    """
    从 PostgreSQL 读取历史数据

    Args:
        code: Stock code (validated)
        start_date: Start date string (YYYY-MM-DD)
        engine: Database engine (optional)

    Returns:
        DataFrame with historical data
    """
    # Validate stock code
    if not validate_stock_code(code):
        logger.error(f"Invalid stock code format: {code}")
        return pd.DataFrame()

    if engine is None:
        engine = get_db_engine()
    if not engine:
        return pd.DataFrame()

    try:
        # 使用参数化查询防止 SQL 注入
        query = text("""
            SELECT date as "日期", open as "开盘", high as "最高",
                   low as "最低", close as "收盘", vol as "成交量",
                   turnover as "换手率"
            FROM daily_k
            WHERE code = :code AND date >= :start_date
            ORDER BY date ASC
        """)
        df = pd.read_sql(query, engine, params={"code": code, "start_date": start_date})
        if not df.empty:
            df['日期'] = df['日期'].apply(lambda x: x.strftime('%Y-%m-%d'))
        return df
    except Exception as e:
        logger.error(f"Error loading from DB for {code}: {e}")
        return pd.DataFrame()

def save_scan_results(
    results: List[Dict[str, Any]],
    engine=None,
    data_date: Optional[str] = None,
    replace_strategy_types: Optional[List[str]] = None,
) -> bool:
    """
    持久化保存选股结果集

    Args:
        results: List of scan result dictionaries
        engine: Database engine (optional)

    Returns:
        True if successful, False otherwise
    """
    if engine is None:
        engine = get_db_engine()
    if not engine:
        return False

    try:
        scanned_at = datetime.now()
        data_date = str(
            data_date
            or (results[0].get("data_date") if results else None)
            or scanned_at.strftime("%Y-%m-%d")
        )[:10]
        with engine.connect() as conn:
            # Replace only the strategies produced by this run. Other strategy
            # snapshots for the same data date remain available for comparison.
            strategy_types = {
                str(strategy_type)
                for strategy_type in (replace_strategy_types or [])
                if strategy_type
            }
            strategy_types.update(str(row.get("strategy_type") or "squeeze") for row in results)
            if not strategy_types:
                return False
            for strategy_type in strategy_types:
                conn.execute(
                    text("""
                        DELETE FROM scan_history
                        WHERE COALESCE(data_date, date) = :data_date
                          AND COALESCE(strategy_type, 'squeeze') = :strategy_type
                    """),
                    {"data_date": data_date, "strategy_type": strategy_type},
                )

            # 批量构建参数列表
            rows = []
            for r in results:
                pa_plan = r.get('pa_trade_plan') or {}
                rows.append({
                    "code": r.get('代码'),
                    "name": r.get('名称'),
                    "date": data_date,
                    "data_date": data_date,
                    "scanned_at": scanned_at,
                    "price": float(r.get('现价', 0)),
                    "pct": float(r.get('涨幅%', 0)),
                    "score": float(r.get('Score', 0)),
                    "rsi": float(r.get('RSI', 0)),
                    "dif": float(r.get('DIF', 0)),
                    "bb": float(r.get('BB', 0)),
                    "glue": float(r.get('粘合度', 0)),
                    "industry": r.get('行业', '未知'),
                    "win_rate": r.get('历史胜率', '0%'),
                    "signal_count": int(r.get('信号次数', 0)),
                    "north_money": r.get('北向', '---'),
                    "resonance": r.get('共振', '独苗'),
                    "shadow_ratio": float(r.get('影线比', 0)),
                    "strategy_type": r.get('strategy_type', 'squeeze'),
                    "result_group": r.get('result_group') or (
                        'HISTORICAL_REVIVAL' if r.get('revival_watch_only')
                        else 'MOMENTUM_WATCH' if r.get('momentum_acceleration_watch_only')
                        else 'FORMAL'
                    ),
                    "roe": float(r.get('ROE', 0)) if r.get('ROE') is not None else None,
                    "net_profit_yoy": float(r.get('净利YOY', 0)) if r.get('净利YOY') is not None else None,
                    "price_action_score": float(r.get('price_action_score', 0)) if r.get('price_action_score') is not None else None,
                    "price_action_regime": r.get('price_action_regime'),
                    "price_action_signal": r.get('price_action_signal'),
                    "price_action_pattern": r.get('price_action_pattern'),
                    "price_action_entry_quality": r.get('price_action_entry_quality'),
                    "price_action_summary": r.get('price_action_summary'),
                    "pa_entry_price": float(r.get('pa_entry_price', 0)) if r.get('pa_entry_price') is not None else None,
                    "pa_stop_price": float(r.get('pa_stop_price', 0)) if r.get('pa_stop_price') is not None else None,
                    "pa_target_price": float(r.get('pa_target_price', 0)) if r.get('pa_target_price') is not None else None,
                    "pa_risk_reward": float(r.get('pa_risk_reward', 0)) if r.get('pa_risk_reward') is not None else None,
                    "pa_trade_action": pa_plan.get("action"),
                    "pa_trade_setup": pa_plan.get("setup"),
                    "pa_risk_pct": float(pa_plan.get("risk_pct", 0)) if pa_plan.get("risk_pct") is not None else None,
                    "sop_grade": r.get("sop_grade"),
                    "sop_quality_score": float(r.get("sop_quality_score", 0)) if r.get("sop_quality_score") is not None else None,
                    "sop_subgrade": r.get("sop_subgrade"),
                    "sop_vetoes": json.dumps(r.get("sop_vetoes") or [], ensure_ascii=False),
                    "sop_checks": json.dumps(r.get("sop_checks") or [], ensure_ascii=False),
                    "sop_bonuses": json.dumps(r.get("sop_bonuses") or [], ensure_ascii=False),
                    "sop_risks": json.dumps(r.get("sop_risks") or [], ensure_ascii=False),
                    "price_action_detail": _price_action_detail_snapshot(r)
                })

            # 一次性批量插入 (executemany)
            if rows:
                conn.execute(text('''
                    INSERT INTO scan_history (
                        code, name, date, data_date, scanned_at, price, pct, score, rsi, dif, bb, glue, industry, win_rate, signal_count, north_money, resonance, shadow_ratio, strategy_type, result_group, roe, net_profit_yoy,
                        price_action_score, price_action_regime, price_action_signal, price_action_pattern, price_action_entry_quality, price_action_summary, pa_entry_price, pa_stop_price, pa_target_price, pa_risk_reward, pa_trade_action, pa_trade_setup, pa_risk_pct,
                        sop_grade, sop_quality_score, sop_subgrade, sop_vetoes, sop_checks, sop_bonuses, sop_risks, price_action_detail
                    ) VALUES (
                        :code, :name, :date, :data_date, :scanned_at, :price, :pct, :score, :rsi, :dif, :bb, :glue, :industry, :win_rate, :signal_count, :north_money, :resonance, :shadow_ratio, :strategy_type, :result_group, :roe, :net_profit_yoy,
                        :price_action_score, :price_action_regime, :price_action_signal, :price_action_pattern, :price_action_entry_quality, :price_action_summary, :pa_entry_price, :pa_stop_price, :pa_target_price, :pa_risk_reward, :pa_trade_action, :pa_trade_setup, :pa_risk_pct,
                        :sop_grade, :sop_quality_score, :sop_subgrade, CAST(:sop_vetoes AS JSONB), CAST(:sop_checks AS JSONB), CAST(:sop_bonuses AS JSONB), CAST(:sop_risks AS JSONB), CAST(:price_action_detail AS JSONB)
                    ) ON CONFLICT (code, data_date, strategy_type) DO UPDATE SET
                        date = EXCLUDED.date,
                        scanned_at = EXCLUDED.scanned_at,
                        price = EXCLUDED.price,
                        pct = EXCLUDED.pct,
                        score = EXCLUDED.score,
                        rsi = EXCLUDED.rsi,
                        dif = EXCLUDED.dif,
                        bb = EXCLUDED.bb,
                        glue = EXCLUDED.glue,
                        industry = EXCLUDED.industry,
                        win_rate = EXCLUDED.win_rate,
                        signal_count = EXCLUDED.signal_count,
                        north_money = EXCLUDED.north_money,
                        resonance = EXCLUDED.resonance,
                        shadow_ratio = EXCLUDED.shadow_ratio,
                        strategy_type = EXCLUDED.strategy_type,
                        result_group = EXCLUDED.result_group,
                        roe = EXCLUDED.roe,
                        net_profit_yoy = EXCLUDED.net_profit_yoy,
                        price_action_score = EXCLUDED.price_action_score,
                        price_action_regime = EXCLUDED.price_action_regime,
                        price_action_signal = EXCLUDED.price_action_signal,
                        price_action_pattern = EXCLUDED.price_action_pattern,
                        price_action_entry_quality = EXCLUDED.price_action_entry_quality,
                        price_action_summary = EXCLUDED.price_action_summary,
                        pa_entry_price = EXCLUDED.pa_entry_price,
                        pa_stop_price = EXCLUDED.pa_stop_price,
                        pa_target_price = EXCLUDED.pa_target_price,
                        pa_risk_reward = EXCLUDED.pa_risk_reward,
                        pa_trade_action = EXCLUDED.pa_trade_action,
                        pa_trade_setup = EXCLUDED.pa_trade_setup,
                        pa_risk_pct = EXCLUDED.pa_risk_pct,
                        sop_grade = EXCLUDED.sop_grade,
                        sop_quality_score = EXCLUDED.sop_quality_score,
                        sop_subgrade = EXCLUDED.sop_subgrade,
                        sop_vetoes = EXCLUDED.sop_vetoes,
                        sop_checks = EXCLUDED.sop_checks,
                        sop_bonuses = EXCLUDED.sop_bonuses,
                        sop_risks = EXCLUDED.sop_risks,
                        price_action_detail = EXCLUDED.price_action_detail
                '''), rows)
            conn.commit()
            logger.info(f"Saved {len(results)} scan records to database (data_date={data_date})")
        try:
            save_recommendation_events(results, engine=engine, source="scan", event_date=data_date)
        except Exception as event_exc:
            logger.warning(f"Recommendation event persistence skipped: {event_exc}")
        return True
    except Exception as e:
        logger.error(f"Failed to save scan results: {e}")
        return False

def get_scan_history_by_date(date_str: str, engine=None) -> List[Dict[str, Any]]:
    """
    按日期获取历史选股结果

    Args:
        date_str: Date string (YYYY-MM-DD)
        engine: Database engine (optional)

    Returns:
        List of scan result dictionaries
    """
    if engine is None:
        engine = get_db_engine()
    if not engine:
        return []

    try:
        # 使用参数化查询防止 SQL 注入
        query = text("SELECT * FROM scan_history WHERE COALESCE(data_date, date) = :date ORDER BY score DESC")
        df = pd.read_sql(query, engine, params={"date": date_str})
        if df.empty:
            return []
        
        # Define a column mapping
        col_mapping = {
            "code": "代码",
            "data_date": "data_date",
            "scanned_at": "scanned_at",
            "name": "名称",
            "industry": "行业",
            "price": "现价",
            "pct": "涨幅%",
            "score": "Score",
            "rsi": "RSI",
            "dif": "DIF",
            "bb": "BB",
            "glue": "粘合度",
            "win_rate": "历史胜率",
            "signal_count": "信号次数",
            "north_money": "北向",
            "resonance": "共振",
            "shadow_ratio": "影线比",
            "strategy_type": "strategy_type",
            "result_group": "result_group",
            "roe": "ROE",
            "net_profit_yoy": "净利YOY",
            "price_action_score": "price_action_score",
            "price_action_regime": "price_action_regime",
            "price_action_signal": "price_action_signal",
            "price_action_pattern": "price_action_pattern",
            "price_action_entry_quality": "price_action_entry_quality",
            "price_action_summary": "price_action_summary",
            "pa_entry_price": "pa_entry_price",
            "pa_stop_price": "pa_stop_price",
            "pa_target_price": "pa_target_price",
            "pa_risk_reward": "pa_risk_reward",
            "pa_trade_action": "pa_trade_action",
            "pa_trade_setup": "pa_trade_setup",
            "pa_risk_pct": "pa_risk_pct",
            "sop_grade": "sop_grade",
            "sop_quality_score": "sop_quality_score",
            "sop_subgrade": "sop_subgrade",
            "sop_vetoes": "sop_vetoes",
            "sop_checks": "sop_checks",
            "sop_bonuses": "sop_bonuses",
            "sop_risks": "sop_risks",
            "price_action_detail": "price_action_detail"
        }
        
        # 仅过滤并重命名 DataFrame 中存在的列，以防结构字段缺失
        existing_mapping = {k: v for k, v in col_mapping.items() if k in df.columns}
        df_mapped = df[list(existing_mapping.keys())].rename(columns=existing_mapping)
        
        # 兜底确保策略类型列存在
        if "strategy_type" not in df_mapped.columns:
            df_mapped["strategy_type"] = "squeeze"
            
        records = df_mapped.to_dict('records')
        for record in records:
            detail = record.pop("price_action_detail", None)
            if detail:
                if isinstance(detail, str):
                    try:
                        detail = json.loads(detail)
                    except json.JSONDecodeError:
                        detail = {}
                if isinstance(detail, dict):
                    record.update({k: v for k, v in detail.items() if v is not None})
            if record.get("pa_trade_action") or record.get("pa_trade_setup"):
                record["pa_trade_plan"] = record.get("pa_trade_plan") or {
                    "action": record.get("pa_trade_action") or "WAIT",
                    "action_label": record.get("pa_trade_action") or "历史计划",
                    "setup": record.get("pa_trade_setup") or record.get("price_action_pattern") or "历史结构",
                    "quality": record.get("price_action_entry_quality") or "观望",
                    "entry_condition": "历史记录仅保留 Brooks 计划摘要，请打开个股详情刷新完整计划。",
                    "invalidation": f"跌破 {record.get('pa_stop_price')}" if record.get("pa_stop_price") else "历史记录未保存完整失效条件。",
                    "risk_pct": record.get("pa_risk_pct") or 0,
                    "risk_reward": record.get("pa_risk_reward") or 0,
                    "position_hint": "历史快照",
                    "checklist": [],
                    "management": [],
                    "avoid_reasons": [],
                }
        return _json_safe(records)
    except Exception as e:
        logger.error(f"Error loading scan history for {date_str}: {e}")
        return []

def get_scan_dates(engine=None) -> List[str]:
    """
    获取所有有选股记录的日期

    Args:
        engine: Database engine (optional)

    Returns:
        List of date strings (YYYY-MM-DD)
    """
    if engine is None:
        engine = get_db_engine()
    if not engine:
        return []

    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT DISTINCT COALESCE(data_date, date) AS signal_date FROM scan_history ORDER BY signal_date DESC"))
            return [str(row[0]) for row in res]
    except Exception as e:
        logger.error(f"Error getting scan dates: {e}")
        return []

def get_available_dates(engine=None) -> List[Dict[str, Any]]:
    """
    获取可用于选股的数据日期列表

    Args:
        engine: Database engine (optional)

    Returns:
        List of dicts with 'date' and 'stock_count' keys
    """
    if engine is None:
        engine = get_db_engine()
    if not engine:
        return []

    try:
        with engine.connect() as conn:
            query = text("""
                SELECT date, COUNT(DISTINCT code) as stock_count
                FROM daily_k
                GROUP BY date
                HAVING COUNT(DISTINCT code) >= 500
                ORDER BY date DESC
                LIMIT 30
            """)
            result = conn.execute(query)
            return [{"date": str(row[0]), "stock_count": int(row[1])} for row in result.fetchall()]
    except Exception as e:
        logger.error(f"Error getting available dates: {e}")
        return []

def save_stock_basic(df: pd.DataFrame, engine=None) -> bool:
    """
    保存股票基础信息 (板块、名称)

    Args:
        df: DataFrame with columns ['code', 'name', 'industry']
        engine: Database engine (optional)

    Returns:
        True if successful, False otherwise
    """
    if engine is None:
        engine = get_db_engine()
    if not engine or df.empty:
        return False

    try:
        data = df[['code', 'name', 'industry']].copy()
        temp_table = f"stock_basic_temp_{uuid.uuid4().hex[:8]}"
        if not validate_table_name(temp_table):
            logger.error(f"Invalid temp table name: {temp_table}")
            return False
        data.to_sql(temp_table, engine, if_exists='replace', index=False)
        try:
            with engine.connect() as conn:
                conn.execute(text(f'''
                    INSERT INTO stock_basic (code, name, industry)
                    SELECT code, name, industry FROM {temp_table}
                    ON CONFLICT (code) DO UPDATE SET
                        name = EXCLUDED.name,
                        industry = CASE
                            WHEN EXCLUDED.industry = '未知' AND stock_basic.industry IS NOT NULL AND stock_basic.industry != '未知'
                            THEN stock_basic.industry
                            ELSE EXCLUDED.industry
                        END
                '''))
                conn.execute(text(f"DROP TABLE {temp_table}"))
            conn.commit()
            return True
        except Exception:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))
                    conn.commit()
            except Exception:
                pass
            raise
    except Exception as e:
        logger.error(f"save_stock_basic Error: {e}")
        return False

def get_stock_basic_map(engine=None) -> Dict[str, str]:
    """
    获取股票基础信息映射 {code: industry}

    Args:
        engine: Database engine (optional)

    Returns:
        Dictionary mapping stock codes to industries
    """
    if engine is None:
        engine = get_db_engine()
    if not engine:
        return {}

    try:
        query = text("SELECT code, industry FROM stock_basic")
        df = pd.read_sql(query, engine)
        return pd.Series(df.industry.values, index=df.code).to_dict()
    except Exception as e:
        logger.error(f"Error loading stock basic map: {e}")
        return {}

def get_setting(key: str, default: Any = None, engine=None) -> Any:
    """
    获取系统设置

    Args:
        key: Setting key
        default: Default value if key not found
        engine: Database engine (optional)

    Returns:
        Setting value or default
    """
    if SessionLocal is None:
        if engine is None:
            engine = get_db_engine()
        if not engine:
            return default

    try:
        with SessionLocal() as session:
            setting = session.get(SystemSetting, key)
            return setting.value if setting else default
    except Exception as e:
        logger.debug(f"Error getting setting {key}: {e}")
        return default

def save_setting(key: str, value: Any, engine=None) -> bool:
    """
    保存系统设置

    Args:
        key: Setting key
        value: Setting value
        engine: Database engine (optional)

    Returns:
        True if successful, False otherwise
    """
    if SessionLocal is None:
        if engine is None:
            engine = get_db_engine()
        if not engine:
            return False

    try:
        with SessionLocal() as session:
            setting = session.get(SystemSetting, key)
            if setting:
                setting.value = str(value)
            else:
                setting = SystemSetting(key=key, value=str(value))
                session.add(setting)
            session.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving setting {key}: {e}")
        return False
