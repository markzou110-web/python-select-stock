import os
import json
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
    if pd.isna(value) if not isinstance(value, (dict, list, tuple, set)) else False:
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


PRICE_ACTION_DETAIL_KEYS = [
    "price_action_score", "price_action_regime", "price_action_signal", "price_action_pattern",
    "price_action_entry_quality", "price_action_summary", "price_action_risks",
    "pa_market_cycle", "pa_range_location", "pa_entry_price", "pa_stop_price",
    "pa_target_price", "pa_risk_reward", "pa_tags", "pa_pullback_legs",
    "pa_pullback_structure", "pa_pullback_validity", "pa_pullback_status",
    "pa_pullback_status_label", "pa_pullback_support_price", "pa_pullback_confirmation_price",
    "pa_pullback_invalidation_price", "pa_pullback_action", "pa_breakout_quality", "pa_failure_risk",
    "pa_entry_quality_score", "pa_h2_quality", "pa_range_rule",
    "pa_failed_breakout_type", "pa_trap_risk", "pa_micro_channel",
    "pa_always_in_strength", "pa_trend_damage", "pa_channel_state",
    "pa_position_strategy", "pa_weekly_context", "pa_multi_timeframe_score",
    "pa_multi_timeframe_note", "pa_volume_pattern", "pa_volume_confirmed",
    "pa_volume_risk", "pa_failed_second_entry", "pa_second_entry_risk",
    "pa_gap_type", "pa_gap_risk", "pa_range_width_quality",
    "pa_range_center_risk", "pa_range_failed_breakout_count",
    "pa_trend_phase", "pa_trend_phase_action", "pa_decision_summary",
    "pa_eight_rules", "pa_eight_rule_primary", "pa_eight_rule_score_delta",
    "pa_eight_rule_risk_delta",
    "pa_trade_plan", "trade_eligible", "trade_bucket", "trade_blockers",
    "final_trade_score", "trade_timeframe", "exit_hint",
    "market_regime",
    "sop_grade", "sop_action", "sop_risks", "sector_momentum_score", "sector_breadth",
    "sector_phase", "sector_rank", "sector_alignment_score", "sector_relative_pct",
    "sector_3d_pct", "sector_5d_pct", "sector_consecutive_up_days", "sector_role",
    "sector_mainline", "leadership_score",
    "sector_watch_only", "sector_watch_reason",
    "market_sentiment_stage", "market_sentiment_label", "market_sentiment_score",
    "portfolio_position_cap_pct", "market_allowed_actions", "market_forbidden_actions", "market_breadth",
    "trade_opportunity_score", "trade_opportunity_label", "decision_score_components",
    "position_plan", "trade_state", "execution_instruction",
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
            # 性能索引
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_k_date ON daily_k(date);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_k_code ON daily_k(code);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_k_code_date ON daily_k(code, date);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_scan_history_date ON scan_history(date);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_paper_trading_status ON paper_trading(status);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stock_basic_industry ON stock_basic(industry);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_k_date_code ON daily_k(date, code);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_paper_trading_status_date ON paper_trading(status, entry_date DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist(status);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_watchlist_code_status ON watchlist(code, status);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_strategy_templates_type ON strategy_templates(strategy_type);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_scan_audit_date ON scan_audit_log(scan_date DESC);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_failure_samples_code_date ON failure_samples(code, sample_date DESC);"))
                logger.info("Database and performance indexes verified via ORM.")
            except Exception as e:
                logger.debug(f"Index creation skipped: {e}")

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
                logger.info("Migration: recommendation event log ensured.")
            except Exception as e:
                logger.debug(f"recommendation event migration skipped: {e}")

            conn.commit()
    except Exception as e:
        logger.error(f"Database init failed: {e}")


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
    }

    try:
        with engine.connect() as conn:
            if engine.dialect.name == "sqlite":
                conn.execute(text("""
                    INSERT INTO scan_audit_log (
                        scan_date, started_at, finished_at, duration_sec, status, strategy_type,
                        params_snapshot, version_snapshot, total_snapshot, candidate_count,
                        result_count, fail_reasons, error_message
                    ) VALUES (
                        :scan_date, :started_at, :finished_at, :duration_sec, :status, :strategy_type,
                        :params_snapshot, :version_snapshot, :total_snapshot, :candidate_count,
                        :result_count, :fail_reasons, :error_message
                    )
                """), payload)
            else:
                conn.execute(text("""
                    INSERT INTO scan_audit_log (
                        scan_date, started_at, finished_at, duration_sec, status, strategy_type,
                        params_snapshot, version_snapshot, total_snapshot, candidate_count,
                        result_count, fail_reasons, error_message
                    ) VALUES (
                        CAST(:scan_date AS DATE), :started_at, :finished_at, :duration_sec, :status, :strategy_type,
                        CAST(:params_snapshot AS JSONB), CAST(:version_snapshot AS JSONB), :total_snapshot, :candidate_count,
                        :result_count, CAST(:fail_reasons AS JSONB), :error_message
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
                        sector_phase, market_regime, blockers, status, created_at
                    ) VALUES (
                        :event_date, :event_time, :source, :code, :name, :industry, :strategy_type,
                        :recommendation_price, :score, :trade_bucket, :trade_eligible, :final_trade_score,
                        :pa_trade_action, :pa_trade_setup, :pa_entry_price, :pa_stop_price,
                        :sector_phase, :market_regime, :blockers, :status, :created_at
                    )
                    ON CONFLICT (event_date, source, code, strategy_type) DO UPDATE SET
                        event_time = excluded.event_time,
                        recommendation_price = excluded.recommendation_price,
                        score = excluded.score,
                        trade_bucket = excluded.trade_bucket,
                        final_trade_score = excluded.final_trade_score,
                        blockers = excluded.blockers,
                        status = excluded.status
                """), rows)
            else:
                conn.execute(text("""
                    INSERT INTO recommendation_events (
                        event_date, event_time, source, code, name, industry, strategy_type,
                        recommendation_price, score, trade_bucket, trade_eligible, final_trade_score,
                        pa_trade_action, pa_trade_setup, pa_entry_price, pa_stop_price,
                        sector_phase, market_regime, blockers, status, created_at
                    ) VALUES (
                        CAST(:event_date AS DATE), :event_time, :source, :code, :name, :industry, :strategy_type,
                        :recommendation_price, :score, :trade_bucket, :trade_eligible, :final_trade_score,
                        :pa_trade_action, :pa_trade_setup, :pa_entry_price, :pa_stop_price,
                        :sector_phase, :market_regime, CAST(:blockers AS JSONB), :status, :created_at
                    )
                    ON CONFLICT (event_date, source, code, strategy_type) DO UPDATE SET
                        event_time = EXCLUDED.event_time,
                        recommendation_price = EXCLUDED.recommendation_price,
                        score = EXCLUDED.score,
                        trade_bucket = EXCLUDED.trade_bucket,
                        final_trade_score = EXCLUDED.final_trade_score,
                        blockers = EXCLUDED.blockers,
                        status = EXCLUDED.status
                """), rows)
            conn.commit()
        return True
    except Exception as exc:
        logger.error(f"Failed to save recommendation events: {exc}")
        return False

def save_to_db(df: pd.DataFrame, code: str, engine=None) -> bool:
    """
    将数据保存到 PostgreSQL (增量)

    Args:
        df: DataFrame with columns ['日期', '开盘', '最高', '最低', '收盘', '成交量']
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
        data = df[['日期', '开盘', '最高', '最低', '收盘', '成交量']].copy()
        data['code'] = code
        data = data.rename(columns={'日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'vol'})

        # 统一将 date 列转为字符串，确保数据库能够一致解析
        data['date'] = data['date'].astype(str)

        # 转换为记录字典列表，使用批量参数化 INSERT 替代临时表以确保线程/事务安全
        rows = data.to_dict('records')

        with engine.connect() as conn:
            conn.execute(text('''
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                VALUES (:code, CAST(:date AS DATE), :open, :high, :low, :close, :vol)
                ON CONFLICT (code, date) DO NOTHING
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
                   low as "最低", close as "收盘", vol as "成交量"
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

def save_scan_results(results: List[Dict[str, Any]], engine=None) -> bool:
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
    if not engine or not results:
        return False

    try:
        current_date = datetime.now().strftime("%Y-%m-%d")
        with engine.connect() as conn:
            # 先删除当天旧记录，避免上次扫描的残留股票仍然显示
            conn.execute(text("DELETE FROM scan_history WHERE date = :date"), {"date": current_date})

            # 批量构建参数列表
            rows = []
            for r in results:
                pa_plan = r.get('pa_trade_plan') or {}
                rows.append({
                    "code": r.get('代码'),
                    "name": r.get('名称'),
                    "date": current_date,
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
                    "price_action_detail": _price_action_detail_snapshot(r)
                })

            # 一次性批量插入 (executemany)
            if rows:
                conn.execute(text('''
                    INSERT INTO scan_history (
                        code, name, date, price, pct, score, rsi, dif, bb, glue, industry, win_rate, signal_count, north_money, resonance, shadow_ratio, strategy_type, roe, net_profit_yoy,
                        price_action_score, price_action_regime, price_action_signal, price_action_pattern, price_action_entry_quality, price_action_summary, pa_entry_price, pa_stop_price, pa_target_price, pa_risk_reward, pa_trade_action, pa_trade_setup, pa_risk_pct, price_action_detail
                    ) VALUES (
                        :code, :name, :date, :price, :pct, :score, :rsi, :dif, :bb, :glue, :industry, :win_rate, :signal_count, :north_money, :resonance, :shadow_ratio, :strategy_type, :roe, :net_profit_yoy,
                        :price_action_score, :price_action_regime, :price_action_signal, :price_action_pattern, :price_action_entry_quality, :price_action_summary, :pa_entry_price, :pa_stop_price, :pa_target_price, :pa_risk_reward, :pa_trade_action, :pa_trade_setup, :pa_risk_pct, CAST(:price_action_detail AS JSONB)
                    ) ON CONFLICT (code, date) DO UPDATE SET
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
                        price_action_detail = EXCLUDED.price_action_detail
                '''), rows)
            conn.commit()
            logger.info(f"Saved {len(results)} scan records to database ({current_date})")
        try:
            save_recommendation_events(results, engine=engine, source="scan", event_date=current_date)
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
        query = text("SELECT * FROM scan_history WHERE date = :date ORDER BY score DESC")
        df = pd.read_sql(query, engine, params={"date": date_str})
        if df.empty:
            return []
        
        # Define a column mapping
        col_mapping = {
            "code": "代码",
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
        return records
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
            res = conn.execute(text("SELECT DISTINCT date FROM scan_history ORDER BY date DESC"))
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
