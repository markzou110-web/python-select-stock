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

SessionLocal = None

def get_db_engine(db_config: Optional[Dict[str, Any]] = None):
    """根据配置获取数据库引擎"""
    if not db_config:
        db_config = load_db_config()

    if not db_config:
        return None

    try:
        url = f"postgresql://{db_config['user']}:{db_config['pwd']}@{db_config['host']}:{db_config['port']}/{db_config['db']}"
        engine = create_engine(url, pool_size=10, max_overflow=20)
        
        global SessionLocal
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        
        return engine
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
                logger.info("Database and performance indexes verified via ORM.")
            except Exception as e:
                logger.debug(f"Index creation skipped: {e}")

            conn.commit()
    except Exception as e:
        logger.error(f"Database init failed: {e}")

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

        # 使用唯一的临时表名，防止多线程冲突
        # 验证临时表名格式
        temp_table_name = f"daily_k_temp_{code}"
        if not validate_table_name(temp_table_name):
            logger.error(f"Invalid temp table name: {temp_table_name}")
            return False

        data.to_sql(temp_table_name, engine, if_exists='replace', index=False)
        with engine.connect() as conn:
            # 使用参数化查询避免 SQL 注入 (临时表名已验证)
            conn.execute(text(f'''
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                SELECT code, CAST(date AS DATE), open, high, low, close, vol FROM {temp_table_name}
                ON CONFLICT (code, date) DO NOTHING
            '''))
            conn.execute(text(f"DROP TABLE {temp_table_name}"))
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

            for r in results:
                conn.execute(text('''
                    INSERT INTO scan_history (
                        code, name, date, price, pct, score, rsi, dif, bb, glue, industry, win_rate, signal_count, north_money, resonance, shadow_ratio, strategy_type
                    ) VALUES (
                        :code, :name, :date, :price, :pct, :score, :rsi, :dif, :bb, :glue, :industry, :win_rate, :signal_count, :north_money, :resonance, :shadow_ratio, :strategy_type
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
                        strategy_type = EXCLUDED.strategy_type
                '''), {
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
                    "strategy_type": r.get('strategy_type', 'squeeze')
                })
            conn.commit()
            logger.info(f"Saved {len(results)} scan records to database ({current_date})")
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
        
        # 转换回前端需要的格式
        results = []
        for _, row in df.iterrows():
            results.append({
                "代码": row['code'],
                "名称": row['name'],
                "行业": row['industry'],
                "现价": row['price'],
                "涨幅%": row['pct'],
                "Score": row['score'],
                "RSI": row['rsi'],
                "DIF": row['dif'],
                "BB": row['bb'],
                "粘合度": row['glue'],
                "历史胜率": row['win_rate'],
                "信号次数": row['signal_count'],
                "北向": row['north_money'],
                "共振": row['resonance'],
                "影线比": row['shadow_ratio'],
                "strategy_type": row.get('strategy_type', 'squeeze')
            })
        return results
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
