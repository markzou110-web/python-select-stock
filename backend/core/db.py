import os
import json
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime

# 获取项目根目录下的配置文件路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "db_config.json")

def load_db_config():
    """从本地文件加载数据库配置"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_db_config(config):
    """保存数据库配置到本地文件"""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)
    except:
        pass

def get_db_engine(db_config=None):
    """根据配置获取数据库引擎"""
    if not db_config:
        db_config = load_db_config()
        
    if not db_config:
        return None
        
    try:
        url = f"postgresql://{db_config['user']}:{db_config['pwd']}@{db_config['host']}:{db_config['port']}/{db_config['db']}"
        engine = create_engine(url, pool_size=10, max_overflow=20)
        return engine
    except Exception as e:
        print(f"Error creating engine: {e}")
        return None

def init_db(engine=None):
    """初始化数据库表"""
    if engine is None:
        engine = get_db_engine()
    if not engine: return
    try:
        with engine.connect() as conn:
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS daily_k (
                    code VARCHAR(20),
                    date DATE,
                    open FLOAT,
                    high FLOAT,
                    low FLOAT,
                    close FLOAT,
                    vol FLOAT,
                    PRIMARY KEY (code, date)
                )
            '''))
            conn.commit()
    except Exception as e:
        print(f"Database init failed: {e}")

def save_to_db(df, code, engine=None):
    """将数据保存到 PostgreSQL (增量)"""
    if engine is None:
        engine = get_db_engine()
    if not engine or df.empty: return
    try:
        data = df[['日期', '开盘', '最高', '最低', '收盘', '成交量']].copy()
        data['code'] = code
        data = data.rename(columns={'日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'vol'})
        
        # 使用唯一的临时表名，防止多线程冲突
        temp_table_name = f"daily_k_temp_{code}"
        data.to_sql(temp_table_name, engine, if_exists='replace', index=False)
        with engine.connect() as conn:
            conn.execute(text(f'''
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                SELECT code, CAST(date AS DATE), open, high, low, close, vol FROM {temp_table_name}
                ON CONFLICT (code, date) DO NOTHING
            '''))
            conn.execute(text(f"DROP TABLE {temp_table_name}"))
            conn.commit()
    except Exception as e:
        with open("sync_error.log", "a") as f:
            f.write(f"[{datetime.now()}] save_to_db Error ({code}): {str(e)}\n")

def load_from_db(code, start_date, engine=None):
    """从 PostgreSQL 读取历史数据"""
    if engine is None:
        engine = get_db_engine()
    if not engine: return pd.DataFrame()
    try:
        query = f"SELECT date as \"日期\", open as \"开盘\", high as \"最高\", low as \"最低\", close as \"收盘\", vol as \"成交量\" FROM daily_k WHERE code='{code}' AND date >= '{start_date}' ORDER BY date ASC"
        df = pd.read_sql(query, engine)
        if not df.empty:
            df['日期'] = df['日期'].apply(lambda x: x.strftime('%Y-%m-%d'))
        return df
    except:
        return pd.DataFrame()
