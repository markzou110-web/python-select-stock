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
                CREATE TABLE IF NOT EXISTS stock_basic (
                    code VARCHAR(20) PRIMARY KEY,
                    name VARCHAR(50),
                    industry VARCHAR(100)
                );
            '''))
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
                );
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS scan_history (
                    code VARCHAR(20),
                    name VARCHAR(50),
                    date DATE,
                    price FLOAT,
                    pct FLOAT,
                    score FLOAT,
                    rsi FLOAT,
                    dif FLOAT,
                    bb FLOAT,
                    glue FLOAT,
                    industry VARCHAR(100),
                    win_rate VARCHAR(20),
                    signal_count INTEGER,
                    north_money VARCHAR(100),
                    resonance VARCHAR(50),
                    shadow_ratio FLOAT,
                    PRIMARY KEY (code, date)
                );
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS paper_trading (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(20),
                    name VARCHAR(50),
                    entry_price FLOAT,
                    entry_date DATE,
                    current_price FLOAT,
                    status VARCHAR(20) DEFAULT 'OPEN',
                    UNIQUE(code, entry_date)
                );
            '''))
            # 兼容性迁移：确保 resonance 和 shadow_ratio 列存在
            try:
                conn.execute(text("ALTER TABLE scan_history ADD COLUMN IF NOT EXISTS resonance VARCHAR(50);"))
                conn.execute(text("ALTER TABLE scan_history ADD COLUMN IF NOT EXISTS shadow_ratio FLOAT;"))
            except:
                pass
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

def save_scan_results(results, engine=None):
    """持久化保存选股结果集"""
    if engine is None: engine = get_db_engine()
    if not engine or not results: return
    
    try:
        current_date = datetime.now().strftime("%Y-%m-%d")
        with engine.connect() as conn:
            for r in results:
                conn.execute(text('''
                    INSERT INTO scan_history (
                        code, name, date, price, pct, score, rsi, dif, bb, glue, industry, win_rate, signal_count, north_money, resonance, shadow_ratio
                    ) VALUES (
                        :code, :name, :date, :price, :pct, :score, :rsi, :dif, :bb, :glue, :industry, :win_rate, :signal_count, :north_money, :resonance, :shadow_ratio
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
                        shadow_ratio = EXCLUDED.shadow_ratio
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
                    "shadow_ratio": float(r.get('影线比', 0))
                })
            conn.commit()
            print(f"💾 数据库：已成功保存 {len(results)} 条选股记录 ({current_date})")
    except Exception as e:
        print(f"❌ 数据库：保存选股结果失败: {e}")

def get_scan_history_by_date(date_str, engine=None):
    """按日期获取历史选股结果"""
    if engine is None: engine = get_db_engine()
    if not engine: return []
    try:
        query = f"SELECT * FROM scan_history WHERE date = '{date_str}' ORDER BY score DESC"
        df = pd.read_sql(query, engine)
        if df.empty: return []
        
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
                "影线比": row['shadow_ratio']
            })
        return results
    except Exception as e:
        print(f"Error loading scan history: {e}")
        return []

def get_scan_dates(engine=None):
    """获取所有有选股记录的日期"""
    if engine is None: engine = get_db_engine()
    if not engine: return []
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT DISTINCT date FROM scan_history ORDER BY date DESC"))
            return [str(row[0]) for row in res]
    except:
        return []

def save_stock_basic(df, engine=None):
    """保存股票基础信息 (板块、名称)"""
    if engine is None: engine = get_db_engine()
    if not engine or df.empty: return
    try:
        data = df[['code', 'name', 'industry']].copy()
        temp_table = "stock_basic_temp"
        data.to_sql(temp_table, engine, if_exists='replace', index=False)
        with engine.connect() as conn:
            conn.execute(text(f'''
                INSERT INTO stock_basic (code, name, industry)
                SELECT code, name, industry FROM {temp_table}
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    industry = EXCLUDED.industry
            '''))
            conn.execute(text(f"DROP TABLE {temp_table}"))
            conn.commit()
    except Exception as e:
        print(f"❌ save_stock_basic Error: {e}")

def get_stock_basic_map(engine=None):
    """获取股票基础信息映射 {code: industry}"""
    if engine is None: engine = get_db_engine()
    if not engine: return {}
    try:
        query = "SELECT code, industry FROM stock_basic"
        df = pd.read_sql(query, engine)
        return pd.Series(df.industry.values, index=df.code).to_dict()
    except:
        return {}
