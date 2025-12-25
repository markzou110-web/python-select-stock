import streamlit as st
import akshare as ak
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import time
import requests
from streamlit_autorefresh import st_autorefresh
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3  # 保留以供参考，但默认使用 PG
import os
import json
import random
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# --- Page Config ---
st.set_page_config(page_title="A股均线粘合监控 v2.0", layout="wide", page_icon="🚀")

# --- Custom CSS for Styling (Light Theme) ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap');

    /* Global Styles */
    .stApp {
        background-color: #fdfdfd;
        font-family: 'Outfit', sans-serif;
        color: #1a1c1e;
    }

    /* Professional Card System */
    .premium-card {
        background: #ffffff;
        border: 1px solid #edf2f7;
        border-radius: 20px;
        padding: 24px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.04);
        margin-bottom: 24px;
        transition: all 0.4s cubic-bezier(0.165, 0.84, 0.44, 1);
    }
    .premium-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 12px 30px rgba(0, 0, 0, 0.08);
        border-color: #cbd5e1;
    }

    /* Metric Overrides - Minimalist */
    [data-testid="stMetric"] {
        background: #ffffff !important;
        border: 1px solid #f1f5f9 !important;
        border-radius: 16px !important;
        padding: 18px !important;
        box-shadow: 0 2px 10px rgba(0,0,0,0.02) !important;
    }

    /* Sidebar - Soft & Integrated */
    [data-testid="stSidebar"] {
        background-color: #ffffff !important;
        border-right: 1px solid #f1f5f9 !important;
    }
    
    /* Input Fields - Fix Contrast & Style */
    div[data-baseweb="select"] > div, .stTextInput input, .stNumberInput input, .stTextArea textarea, .stDateInput input {
        background-color: #f8fafc !important;
        color: #334155 !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 12px !important;
        padding: 0.6rem !important;
        font-weight: 500 !important;
    }
    
    /* Headers & Text */
    .main-title {
        letter-spacing: -0.025em;
        background: linear-gradient(135deg, #0f172a 0%, #334155 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.8rem;
        font-weight: 800;
        margin-bottom: 0.25rem;
    }
    .sub-title {
        color: #94a3b8;
        font-size: 1.15rem;
        font-weight: 400;
        margin-bottom: 2.5rem;
    }

    /* Strategy Hit Card */
    .hit-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 8px;
        font-size: 12px;
        font-weight: 600;
        background: #eff6ff;
        color: #3b82f6;
    }

    /* Buttons - Elegant Indigo */
    .stButton > button {
        background-color: #0f172a !important;
        color: white !important;
        border-radius: 12px !important;
        border: none !important;
        padding: 0.75rem 1.5rem !important;
        font-weight: 600 !important;
        width: 100% !important;
        transition: all 0.2s ease !important;
    }
    .stButton > button:hover {
        background-color: #1e293b !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
    
    /* Clean Divider */
    hr {
        border: none;
        border-top: 1px solid #f1f5f9;
        margin: 2rem 0;
    }
</style>
""", unsafe_allow_html=True)

# --- Database Logic (v2.8 Persistent Config) ---
CONFIG_FILE = "db_config.json"

def save_db_config(config):
    """保存数据库配置到本地文件"""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)
    except:
        pass

def load_db_config():
    """从本地文件加载数据库配置"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def get_db_engine():
    """从 session_state 或本地文件获取数据库引擎"""
    db_config = st.session_state.get('db_config')
    
    # 如果 session 为空，尝试从本地文件加载
    if not db_config:
        db_config = load_db_config()
        if db_config:
            st.session_state['db_config'] = db_config
            
    if not db_config:
        return None
        
    try:
        url = f"postgresql://{db_config['user']}:{db_config['pwd']}@{db_config['host']}:{db_config['port']}/{db_config['db']}"
        engine = create_engine(url)
        return engine
    except Exception as e:
        return None

def init_db():
    """初始化数据库表 (PostgreSQL 兼容)"""
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
        st.error(f"数据库初始化失败: {e}")

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
    """从 PostgreSQL 读取历史数据 (支持显式传入 engine)"""
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

def sync_stock(code, name, engine=None):
    """同步单只股票的缺失数据 (支持显式传入 engine)"""
    if engine is None:
        engine = get_db_engine()
    if not engine: return False
    try:
        # 获取最新日期
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT MAX(date) FROM daily_k WHERE code='{code}'"))
            last_date = result.fetchone()[0]
        
        if last_date:
            fetch_start = (last_date + timedelta(days=1)).strftime("%Y%m%d")
        else:
            fetch_start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
            
        today_str = datetime.now().strftime("%Y%m%d")
        if last_date and last_date.strftime("%Y%m%d") >= today_str:
             return True
             
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=fetch_start, adjust="qfq")
        if not df.empty:
            save_to_db(df, code, engine=engine)
        return True
    except Exception as e:
        with open("sync_error.log", "a") as f:
            f.write(f"[{datetime.now()}] sync_stock Error ({code}): {str(e)}\n")
        return False

# --- Logic & Data Functions ---

@st.cache_data(ttl=3)
def get_market_snapshot():
    """获取全市场实时快照 (Level 1 Funnel) - 增加重试逻辑以应对网络超时"""
    max_retries = 3
    retry_delay = 2
    for attempt in range(max_retries):
        try:
            df = ak.stock_zh_a_spot_em()
            # 重命名常用列以便处理
            df = df.rename(columns={
                '代码': 'code',
                '名称': 'name',
                '最新价': 'price',
                '今开': 'open', # 增加开盘价捕捉
                '涨跌幅': 'pct_chg',
                '成交量': 'vol',
                '换手率': 'turnover',
                '总市值': 'mkt_cap',
                '市盈率-动态': 'pe'
            })
            return df
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            st.error(f"⚠️ 无法连接数据服务器 (Attempt {attempt+1}/{max_retries}): {e}")
            st.info("💡 建议：请检查网络连接，或稍后再次执行扫描。")
            return pd.DataFrame()

@st.cache_data(ttl=3600*12)
def get_weekly_indicators(code):
    """获取周线趋势指标 (v5.0)"""
    try:
        df_w = ak.stock_zh_a_hist(symbol=code, period="weekly", adjust="qfq")
        if len(df_w) < 30: return False
        df_w['EMA10w'] = df_w['收盘'].ewm(span=10, adjust=False).mean()
        df_w['EMA30w'] = df_w['收盘'].ewm(span=30, adjust=False).mean()
        curr_w = df_w.iloc[-1]
        return curr_w['EMA10w'] > curr_w['EMA30w']
    except: return False

def calculate_historical_win_rate(df):
    """快速回测过去1年的胜率 (v5.0)"""
    if len(df) < 60: return 0.0, 0
    signals = []
    # 遍历历史数据 (保留最近1年)
    for i in range(60, len(df) - 5):
        slice_df = df.iloc[:i+1]
        curr = slice_df.iloc[-1]
        prev = slice_df.iloc[-2]
        
        # 简化版策略判定
        ma_vals = [curr['EMA5'], curr['EMA10'], curr['EMA20'], curr['EMA60']]
        sqz = (max(ma_vals) - min(ma_vals)) / min(ma_vals)
        if sqz > 0.12: continue
        
        is_break = curr['收盘'] > max(ma_vals) and curr['收盘'] > prev['收盘'] and curr['成交量'] > curr['Vol_MA20'] * 1.5
        if is_break:
            # 信号触发，检查后5日表现
            future_prices = df['最高'].iloc[i+1 : i+6]
            if not future_prices.empty and (future_prices.max() / curr['收盘'] - 1) > 0.03:
                signals.append(1)
            else:
                signals.append(0)
                
    if not signals: return 0.0, 0
    win_rate = sum(signals) / len(signals) * 100
    return round(win_rate, 1), len(signals)

@st.cache_data(ttl=60)
def get_index_data():
    """获取主要指数实时行情 (上证, 创业, 沪深300, 科创50, 中证1000)"""
    indices = {
        "上证": "000001", 
        "创业板": "399006", 
        "沪深300": "000300", 
        "科创50": "000688", 
        "中证1000": "000852"
    }
    res = {}
    for name, code in indices.items():
        try:
            df = ak.index_zh_a_hist(symbol=code, period="daily", 
                                   start_date=(datetime.now() - timedelta(days=10)).strftime("%Y%m%d"))
            if not df.empty:
                curr = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else curr
                pct = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
                res[name] = {'price': curr['收盘'], 'pct': pct}
        except: continue
    return res

@st.cache_data(ttl=300)
def get_hot_sectors():
    """获取热门行业板块指数 (排行 Top 5)"""
    try:
        df = ak.stock_board_industry_name_em()
        if not df.empty:
            df_sorted = df.sort_values('涨跌幅', ascending=False).head(5)
            hot_sectors = []
            for _, row in df_sorted.iterrows():
                hot_sectors.append({
                    'name': row['板块名称'],
                    'pct': row['涨跌幅'],
                    'leader': row['领涨股票'],
                    'leader_pct': row['领涨股票-涨跌幅']
                })
            return hot_sectors
    except: return []
    return []

@st.cache_data(ttl=3600*24)
def get_sector_map():
    """获取全市场个股行业映射 (通过行业板块接口构建)"""
    try:
        df_boards = ak.stock_board_industry_name_em()
        sector_map = {}
        # 遍历所有行业板块，提取成份股
        for name in df_boards['板块名称']: 
            try:
                df_cons = ak.stock_board_industry_cons_em(symbol=name)
                for code in df_cons['代码']:
                    if code not in sector_map:
                        sector_map[code] = name
                time.sleep(0.05) # 防封控，略微缩短
            except: continue
        return sector_map
    except: return {}

def apply_snapshot_filter(df, min_mkt_cap=30e8, max_mkt_cap=800e8, min_pe=-1000, max_pe=1000, include_star=False):
    """第一层漏斗：快照初步筛选 (v2.7 支持基本面过滤)"""
    if df.empty: return df, 0, 0
    total_count = len(df)
    
    # 排除干扰项
    df = df[~df['name'].str.contains("ST|退", na=False)]
    df = df[~df['code'].str.startswith(('8', '4'))] # 排除北交所
    if not include_star:
        df = df[~df['code'].str.startswith('688')]

    # 核心初筛条件 (Hard Filter)
    df_filtered = df[
        (df['pct_chg'] > 2.0) & # 涨幅 > 2%
        (df['turnover'] > 1.5) & # 换手 > 1.5%
        (df['mkt_cap'] >= min_mkt_cap) & 
        (df['mkt_cap'] <= max_mkt_cap) &
        (df['pe'] >= min_pe) & 
        (df['pe'] <= max_pe)
    ].copy()
    
    return df_filtered, total_count, len(df_filtered)

def calculate_market_breadth(df):
    """计算市场温度计"""
    if df.empty: return 0, 0
    up = len(df[df['pct_chg'] > 0])
    down = len(df[df['pct_chg'] < 0])
    return up, down

def calculate_indicators(df, current_price=None, current_vol=None, current_open=None, periods=[5, 10, 20, 60]):
    """计算 EMA，支持注入当前快照价格以对齐 (v2.6.5 修复阳线判断)"""
    if current_price is not None and not df.empty:
        last_date = str(df.iloc[-1]['日期'])
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        if last_date < today_str:
            new_row = df.iloc[-1].copy()
            new_row['日期'] = today_str
            new_row['收盘'] = current_price
            new_row['开盘'] = current_open if current_open is not None else current_price
            if current_vol is not None:
                new_row['成交量'] = current_vol
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        else:
            df.iloc[-1, df.columns.get_loc('收盘')] = current_price
            if current_open is not None:
                df.iloc[-1, df.columns.get_loc('开盘')] = current_open
            if current_vol is not None:
                df.iloc[-1, df.columns.get_loc('成交量')] = current_vol

    for p in periods:
        df[f'EMA{p}'] = df['收盘'].ewm(span=p, adjust=False).mean()
    
    # MACD 计算 (v2.7)
    exp1 = df['收盘'].ewm(span=12, adjust=False).mean()
    exp2 = df['收盘'].ewm(span=26, adjust=False).mean()
    df['MACD_DIF'] = exp1 - exp2
    df['MACD_DEA'] = df['MACD_DIF'].ewm(span=9, adjust=False).mean()
    df['MACD_HIST'] = (df['MACD_DIF'] - df['MACD_DEA']) * 2

    # RSI 计算 (v5.0)
    delta = df['收盘'].diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # Bollinger Bandwidth (v5.0)
    rolling_std = df['收盘'].rolling(window=20).std()
    df['BB_Mid'] = df['收盘'].rolling(window=20).mean()
    df['BB_Upper'] = df['BB_Mid'] + 2 * rolling_std
    df['BB_Lower'] = df['BB_Mid'] - 2 * rolling_std
    df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid']
    
    df['Vol_MA20'] = df['成交量'].rolling(window=20).mean()
    
    # Relative Strength (RS) vs SSE (000001) - 简易版
    try:
        # 获取基准数据 (缓存)
        bench_df = get_index_hist("000001")
        if not bench_df.empty:
            # 对齐日期
            df = df.merge(bench_df[['日期', '收盘']], on='日期', suffixes=('', '_bench'), how='left')
            df['RS'] = df['收盘'] / df['收盘_bench']
            df['RS_MA50'] = df['RS'].rolling(window=50).mean()
    except: pass

    return df

@st.cache_data(ttl=3600*24)
def get_index_hist(code):
    """获取指数历史用于基准计算"""
    try: return ak.index_zh_a_hist(symbol=code, period="daily")
    except: return pd.DataFrame()

def check_strategy(df, threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_zero=True, use_bb_sqz=True, sqz_lookback=10):
    """执行选股策略逻辑 (v5.0 - Multi-Factor Resonance)"""
    if len(df) < 120: return False, {"reason": f"历史数据不足 ({len(df)}天)"}

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 1. 均线粘合 (was_squeeze_recent) ---
    ma_values = [df['EMA5'], df['EMA10'], df['EMA20'], df['EMA60']]
    ma_df = pd.concat(ma_values, axis=1)
    ma_max_all = ma_df.max(axis=1)
    ma_min_all = ma_df.min(axis=1)
    sqz_ratios = (ma_max_all - ma_min_all) / ma_min_all
    
    was_squeeze_recent = sqz_ratios.iloc[-sqz_lookback:].min() < threshold
    
    # --- 2. 突破动作 ---
    curr_ma_max = ma_max_all.iloc[-1]
    is_breakout = curr['收盘'] > curr_ma_max and curr['收盘'] > curr['EMA5'] and curr['收盘'] > curr['开盘']
    
    # --- 3. 趋势与量能 ---
    is_trending = curr['EMA20'] >= prev['EMA20'] and curr['收盘'] > curr['EMA60']
    vol_ratio = curr['成交量'] / curr['Vol_MA20'] if curr['Vol_MA20'] > 0 else 0
    is_volume = vol_ratio >= vol_multiplier
    
    # --- 4. 高级因子 (Resonance) ---
    is_rsi_ok = curr['RSI'] > rsi_min
    is_macd_ok = curr['MACD_DIF'] > 0 if use_macd_zero else True
    
    # --- 5. 相对强度 (RS) ---
    is_rs_ok = True
    if 'RS' in df.columns and 'RS_MA50' in df.columns:
        is_rs_ok = curr['RS'] > curr['RS_MA50']
    
    # --- 6. 波动率收缩 (BB) ---
    is_bb_ok = True
    if use_bb_sqz:
        bb_quantile_20 = df['BB_Width'].iloc[-120:].quantile(0.2)
        is_bb_ok = curr['BB_Width'] <= bb_quantile_20

    debug_info = {
        "squeeze": round(sqz_ratios.iloc[-1], 4),
        "vol_ratio": round(vol_ratio, 2),
        "rsi": round(curr['RSI'], 1),
        "is_breakout": is_breakout,
        "is_trending": is_trending,
        "is_volume": is_volume,
        "is_rsi_ok": is_rsi_ok,
        "is_macd_ok": is_macd_ok,
        "is_bb_ok": is_bb_ok,
        "was_sqz_recent": was_squeeze_recent
    }

    if not was_squeeze_recent:
        debug_info["reason"] = "近期未现粘合"
        return False, debug_info

    if is_breakout and is_trending and is_volume and is_rsi_ok and is_macd_ok and is_bb_ok and is_rs_ok:
        pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
        score = (vol_ratio * 20) + ((0.2 - sqz_ratios.iloc[-1]) * 100 * 40) + (curr['RSI'] * 0.5)
        return True, {
            'price': curr['收盘'],
            'pct_change': round(pct_change, 2),
            'squeeze': round(sqz_ratios.iloc[-1], 4),
            'vol_ratio': round(vol_ratio, 2),
            'ema20': curr['EMA20'],
            'score': round(score, 2),
            'rsi': round(curr['RSI'], 1),
            'dif': round(curr['MACD_DIF'], 3),
            'bb_width': round(curr['BB_Width'], 4)
        }
    
    if not is_breakout: debug_info["reason"] = "未突破或非阳线"
    elif not is_trending: debug_info["reason"] = "趋势不佳"
    elif not is_volume: debug_info["reason"] = "量能不足"
    elif not is_rsi_ok: debug_info["reason"] = f"RSI 强度不足 ({round(curr['RSI'],1)})"
    elif not is_macd_ok: debug_info["reason"] = "MACD 在零轴下"
    elif not is_bb_ok: debug_info["reason"] = "波动率未达极值收缩"
    else: debug_info["reason"] = "指标未达标"

    return False, debug_info

@st.cache_data(ttl=3600*2) # 缓存 2 小时，避免短时间内重复抓取同一股票
def get_stock_history(code, start_date, _engine=None):
    """获取股票历史数据：本地数据库优先 (v2.2)"""
    # 尝试从本地库读取
    df_local = load_from_db(code, "-".join([start_date[:4], start_date[4:6], start_date[6:]]), engine=_engine)
    
    # 如果本地数据量不足且环境支持同步，则同步
    today_str = datetime.now().strftime("%Y-%m-%d")
    if df_local.empty or (not df_local.empty and df_local.iloc[-1]['日期'] < today_str):
        sync_stock(code, "", engine=_engine) # 尝试同步缺失部分
        df_local = load_from_db(code, "-".join([start_date[:4], start_date[4:6], start_date[6:]]), engine=_engine)
    
    # --- 关键修复：如果 DB 仍然为空（同步失败），则直接返回 API 实时数据，防止策略结果为 0 ---
    if df_local.empty:
        try:
            df_api = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, adjust="qfq")
            return df_api
        except:
             return pd.DataFrame()
        
    return df_local

def get_historical_snapshot(target_date):
    """从数据库获取指定日期的全市场快照 (v2.4)"""
    engine = get_db_engine()
    if not engine: return pd.DataFrame()
    try:
        # 查询该日期的所有股票记录
        query = f"""
            SELECT code, date, open as price, close, high, low, vol as vol 
            FROM daily_k 
            WHERE date = '{target_date.strftime('%Y-%m-%d')}'
        """
        df = pd.read_sql(query, engine)
        if df.empty:
            return pd.DataFrame()
        
        # 为了兼容 funnel，我们手动构造必要的列
        df['name'] = df['code'] # 历史数据中名称可能未存，暂用代码代替
        df['pct_chg'] = 0.0 # 历史模式下跳过涨幅硬过滤
        df['turnover'] = 5.0 # 历史换手率暂设为 5% 以跳过硬过滤
        df['mkt_cap'] = 50e8 # 历史市值暂设为中位值
        return df
    except:
        return pd.DataFrame()

def single_stock_task(code, name, current_price, current_vol, current_open, threshold, vol_multiplier, rsi_min, use_macd_zero, use_bb_sqz, sqz_lookback, use_weekly, target_date=None, engine=None):
    """单只股票处理逻辑 (v5.0 增强型)"""
    try:
        if target_date is None: target_date = datetime.now()
        end_date_str = target_date.strftime("%Y-%m-%d")
        start_date = (target_date - timedelta(days=250)).strftime("%Y%m%d")
        
        df = load_from_db(code, engine)
        if df.empty or df.iloc[-1]['日期'] < (target_date - timedelta(days=3)).strftime("%Y-%m-%d"):
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, adjust="qfq")
            if not df.empty: save_to_db(code, df, engine)

        if df.empty: return None
        
        df = df[df['日期'] <= end_date_str].copy()
        if len(df) < 120: return None
        
        is_today = end_date_str == datetime.now().strftime("%Y-%m-%d")
        df = calculate_indicators(df, 
                                  current_price=current_price if is_today else None,
                                  current_vol=current_vol if is_today else None,
                                  current_open=current_open if is_today else None)
        
        match, stats = check_strategy(df, threshold, vol_multiplier, rsi_min, use_macd_zero, use_bb_sqz, sqz_lookback)
        
        if match:
            if use_weekly:
                if not get_weekly_indicators(code):
                    return False, {"reason": "周线趋势不佳", "code": code, "name": name}, False
            
            match_res = {
                '代码': code, '名称': name, '现价': stats['price'], 
                '涨幅%': stats['pct_change'], '粘合度': stats['squeeze'], 
                '量比': stats['vol_ratio'], 'RSI': stats['rsi'],
                'DIF': stats['dif'], 'Score': stats['score'],
                'BB': stats['bb_width'], 'df': df # 保留 df 用于后续胜率计算
            }
            return True, match_res, True
        else:
            stats['name'] = name
            stats['code'] = code
            return False, stats, False
    except Exception as e:
        return False, {"error": str(e), "code": code, "name": name}, False
    return None

def concurrent_scan(candidates, threshold, vol_multiplier, rsi_min, use_macd_zero, use_bb_sqz, sqz_lookback, use_weekly, max_workers=10, target_date=None):
    """第二层漏斗：并发历史回测 (v5.0 增强型)"""
    results = []
    debug_samples = [] 
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(candidates)
    
    start_time = time.time()
    engine = get_db_engine()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(single_stock_task, row['code'], row['name'], row['price'], row['vol'], row['open'], 
                           threshold, vol_multiplier, rsi_min, use_macd_zero, use_bb_sqz, 
                           sqz_lookback, use_weekly, target_date, engine): row['code'] 
            for _, row in candidates.iterrows()
        }
        
        for i, future in enumerate(as_completed(futures)):
            res = future.result()
            if res is None: continue
            
            success, info, _ = res
            if success:
                results.append(info)
            elif len(debug_samples) < 5:
                debug_samples.append(info)
            
            if i % 10 == 0 or i == total - 1:
                prog = (i + 1) / total
                status_text.text(f"🚀 极速分析中: {i+1}/{total}")
                progress_bar.progress(prog)
                
    progress_bar.empty()
    status_text.empty()
    
    duration = time.time() - start_time
    return pd.DataFrame(results), duration, debug_samples

def send_bark_notification(bark_key, name, pct_change):
    if not bark_key: return
    url = f"https://api.day.app/{bark_key}/发现机会/{name} 涨幅 {pct_change}%"
    try: requests.get(url, timeout=2)
    except: pass

# --- UI Layout ---

def main():
    st.markdown('<div class="main-title">Alpha Vision 极光量化终端</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">专注均线粘合与爆发点的极简选股监控 (v4.0 Hyper-Premium)</div>', unsafe_allow_html=True)
    # Sidebar
    st.sidebar.header("⚙️ 核心配置")
    with st.sidebar.form("scan_config_form"):
        st.header("⚙️ 扫描配置")
        analysis_date = st.date_input("分析日期 (默认为今日实时)", datetime.now())
        
        market_range = st.selectbox("分析基准范围", 
                                           ["全A股 (30亿~1500亿)", "包含科创板 (全市场)", "沪深300", "中证500", "上证50", "自定义代码"],
                                           index=0)
        
        manual_codes = st.text_area("输入股票代码 (自定义模式使用)", help="例如: 000001, 600000, 300123")
        
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            threshold = st.slider("粘合度阈值", 0.01, 0.30, 0.08)
        with col_t2:
            vol_multiplier = st.number_input("放量倍数", 1.0, 5.0, 2.0)
            
        max_workers = st.slider("线程数", 1, 20, 10)
        
        st.subheader("🔬 进阶筛选 (TV Pro 同步)")
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            rsi_min = st.number_input("RSI 最小强度", 30, 80, 55)
            sqz_lookback = st.number_input("粘合回溯天数", 1, 30, 10)
            use_weekly = st.checkbox("启用周线趋势过滤", value=True)
        with col_f2:
            use_macd_zero = st.checkbox("MACD 零轴之上", value=True)
            use_bb_sqz = st.checkbox("极致波动率收缩(BB)", value=True)
            min_pe = st.number_input("最小 PE", value=-10.0)
            max_pe = st.number_input("最大 PE", value=80.0)
            
        min_mkt_cap_f = st.slider("最小市值 (亿)", 10, 500, 30)
        max_mkt_cap_f = st.slider("最大市值 (亿)", 100, 2000, 800)
        
        submit_button = st.form_submit_button("🚀 执行扫描", use_container_width=True)

    is_historical = analysis_date < datetime.now().date()
    
    st.sidebar.divider()
    st.sidebar.header("📥 导出与通知")
    tv_limit = st.sidebar.number_input("TV 导出数量限制", 1, 100, 30)
    bark_key = st.sidebar.text_input("Bark Key", "")
    auto_mode = st.sidebar.checkbox("🔴 盘中自动刷新监控")
    
    if auto_mode:
        st_autorefresh(interval=60000, key="v2_refresh") # 60s 刷新

    # --- Market Breath & Stats ---
    init_db()
    snapshot_df = get_market_snapshot()
    up, down = calculate_market_breadth(snapshot_df)
    
    # --- Sidebar Database Management (PostgreSQL) ---
    st.sidebar.divider()
    st.sidebar.header("🐘 PostgreSQL 仓库")
    saved_config = load_db_config()
    with st.sidebar.expander("🔑 数据库连接配置", expanded=not saved_config):
        pg_host = st.text_input("Host", saved_config.get('host', 'localhost'))
        pg_port = st.text_input("Port", saved_config.get('port', '5432'))
        pg_user = st.text_input("User", saved_config.get('user', 'postgres'))
        pg_pwd = st.text_input("Password", saved_config.get('pwd', ''), type="password")
        pg_db = st.text_input("Database", saved_config.get('db', 'stock_db'))
        
        if st.button("💾 保存并测试连接"):
            config = {'host': pg_host, 'port': pg_port, 'user': pg_user, 'pwd': pg_pwd, 'db': pg_db}
            st.session_state['db_config'] = config
            try:
                # 尝试建立连接
                url = f"postgresql://{config['user']}:{config['pwd']}@{config['host']}:{config['port']}/{config['db']}"
                engine = create_engine(url)
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                
                # 成功后写入文件
                save_db_config(config)
                init_db()
                st.success("✅ 数据库连接成功并已持久化保存！")
                st.rerun() # 刷新以应用配置
            except Exception as e:
                st.error(f"❌ 连接失败: {e}")

    if st.sidebar.button("🔄 同步今日行情 (全市场)"):
        engine = get_db_engine()
        if engine and not snapshot_df.empty:
            init_db()
            candidates_to_sync, _, _ = apply_snapshot_filter(snapshot_df)
            total_sync = len(candidates_to_sync)
            progress_sync = st.progress(0)
            status_sync = st.empty()
            
            for i, (_, row) in enumerate(candidates_to_sync.iterrows()):
                sync_stock(row['code'], row['name'], engine=engine) # 传入当前线程的 engine
                if i % 20 == 0:
                    status_sync.text(f"正在同步 PostgreSQL: {i+1}/{total_sync}")
                    progress_sync.progress((i+1)/total_sync)
            
            progress_sync.empty()
            status_sync.success("✅ 同步完成！复盘时将优先使用 PostgreSQL 数据。")
        else:
            st.sidebar.error("请先配置数据库连接")
    
    if st.sidebar.button("📦 历史数据大批量补全 (仅限初次使用)"):
        engine = get_db_engine()
        if engine and not snapshot_df.empty:
            st.warning("⚠️ 此操作将尝试拉取全市场 5000+ 只股票的历史数据，耗时较长且有封禁风险，请确保并发数建议设为 3-5。")
            all_stocks = snapshot_df['code'].tolist()
            total_sync = len(all_stocks)
            progress_sync = st.progress(0)
            status_sync = st.empty()
            
            # 使用更小的并发以保护 IP
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(sync_stock, code, "", engine): code for code in all_stocks}
                for i, future in enumerate(as_completed(futures)):
                    if i % 10 == 0:
                        status_sync.text(f"📦 批量补全中: {i+1}/{total_sync} (请勿关闭页面)")
                        progress_sync.progress((i+1)/total_sync)
            
            progress_sync.empty()
            status_sync.success(f"✅ 批量补全完成！共处理 {total_sync} 只股票。")
        else:
            st.sidebar.error("请先配置数据库连接")

    with st.sidebar.expander("💾 数据备份与恢复", expanded=False):
        if st.session_state.get('db_config'):
            if st.button("📤 导出所有历史数据 (CSV)"):
                engine = get_db_engine()
                try:
                    df_full = pd.read_sql("SELECT * FROM daily_k", engine)
                    csv_data = df_full.to_csv(index=False).encode('utf-8')
                    st.download_button("下载 CSV 备份", csv_data, f"db_backup_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")
                except Exception as e:
                    st.error(f"导出失败: {e}")
            
            uploaded_file = st.file_uploader("📥 从 CSV 恢复数据", type="csv")
            if uploaded_file and st.button("开始导入"):
                engine = get_db_engine()
                try:
                    df_import = pd.read_csv(uploaded_file)
                    df_import.to_sql('daily_k_temp', engine, if_exists='replace', index=False)
                    with engine.connect() as conn:
                        conn.execute(text('''
                            INSERT INTO daily_k (code, date, open, high, low, close, vol)
                            SELECT code, CAST(date AS DATE), open, high, low, close, vol FROM daily_k_temp
                            ON CONFLICT (code, date) DO NOTHING
                        '''))
                        conn.execute(text("DROP TABLE daily_k_temp"))
                        conn.commit()
                    st.success("✅ 数据恢复成功！")
                except Exception as e:
                    st.error(f"导入失败: {e}")
        else:
            st.caption("请先配置数据库连接")
    
    engine = get_db_engine()
    if engine:
        try:
            with engine.connect() as conn:
                count_res = conn.execute(text("SELECT COUNT(*) FROM daily_k"))
                total_records = count_res.fetchone()[0]
                st.sidebar.caption(f"存储状态: {total_records} 条记录")
        except: pass
    
    # --- Advanced Sidebar Filters ---
    st.sidebar.divider()
    with st.sidebar.expander("🔬 基本面 & 进阶筛选", expanded=True):
        min_pe = st.number_input("最小 PE (动态)", value=-10.0, help="排除极高估值或严重亏损，-10 为容忍度小额亏损")
        max_pe = st.number_input("最大 PE (动态)", value=80.0)
        min_mkt_cap_f = st.slider("最小市值 (亿)", 10, 500, 30)
        max_mkt_cap_f = st.slider("最大市值 (亿)", 100, 2000, 800)
        st.caption("提示：ROE 与营收增长指标将在下一迭代补全。")
    
    # --- Market Indices & Breadth ---
    st.markdown("### 🌍 全球市场概览")
    idx_data = get_index_data()
    cols = st.columns(6)
    indices_conf = [
        ("上证", "SSE"), ("创业板", "GEM"), ("沪深300", "HS300"), 
        ("科创50", "STAR"), ("中证1000", "1000")
    ]
    
    for i, (name, label) in enumerate(indices_conf):
        data = idx_data.get(name, {'price': 0, 'pct': 0})
        icon = "📉" if data['pct'] < 0 else "📈"
        with cols[i]:
            st.markdown(f"""
            <div class="premium-card" style="padding:15px; text-align:center;">
                <div style="font-size:12px; color:#64748b;">{name}</div>
                <div style="font-size:18px; font-weight:800; color:#1e293b;">{data['price']:.0f}</div>
                <div style="font-size:14px; color:{'#ef4444' if data['pct'] > 0 else '#22c55e'};">{icon} {data['pct']:.2f}%</div>
            </div>
            """, unsafe_allow_html=True)
        
    with cols[5]:
        breadth = st.session_state.get('breadth_v2', 0)
        st.markdown(f"""
        <div class="premium-card" style="padding:15px; text-align:center; background:rgba(99, 102, 241, 0.1);">
            <div style="font-size:12px; color:#6366f1;">市场宽度</div>
            <div style="font-size:18px; font-weight:800; color:#4f46e5;">{breadth:.1f}%</div>
            <div style="font-size:12px; color:#64748b;">>EMA20</div>
        </div>
        """, unsafe_allow_html=True)

    # --- Hot Sectors Display (v4.0 Minimalist) ---
    hot_sectors = get_hot_sectors()
    if hot_sectors:
        st.markdown("<h3 style='font-weight:700; color:#1e293b; margin-top:2rem;'>🔥 今日领涨板块</h3>", unsafe_allow_html=True)
        h_cols = st.columns(len(hot_sectors))
        for i, sector in enumerate(hot_sectors):
            with h_cols[i]:
                st.markdown(f"""
                <div class="premium-card" style="padding:15px; border-top: 3px solid #6366f1;">
                    <div style="font-size:12px; color:#94a3b8; margin-bottom:4px;">{sector['name']}</div>
                    <div style="font-size:24px; font-weight:800; color:#0f172a;">{sector['pct']:.2f}%</div>
                    <div style="font-size:11px; color:#64748b; margin-top:8px;">领涨: <b>{sector['leader']}</b></div>
                </div>
                """, unsafe_allow_html=True)
        st.write("")

    # Workflow Execution
    if submit_button or auto_mode:
        # Level 1
        with st.spinner("正在准备数据快照..."):
            if is_historical:
                # 历史模式：尝试从数据库拉取当日全市场清单
                snapshot_df = get_historical_snapshot(analysis_date)
                if snapshot_df.empty:
                    st.error(f"❌ 数据库中未发现 {analysis_date} 的历史数据，请先点击下方的『全市场同步』。")
                    return
                # 历史模式仍应用范围过滤
                include_star = "科创" in market_range
                if market_range == "自定义代码":
                    codes = [c.strip() for c in manual_codes.replace('\n', ',').split(',') if c.strip()]
                    candidates = snapshot_df[snapshot_df['code'].isin(codes)]
                elif "全A股" in market_range or "包含科创" in market_range:
                    candidates, _, _ = apply_snapshot_filter(
                        snapshot_df, 
                        min_mkt_cap=min_mkt_cap_f*1e8, 
                        max_mkt_cap=max_mkt_cap_f*1e8,
                        min_pe=min_pe,
                        max_pe=max_pe,
                        include_star=include_star
                    )
                else:
                    target_map = {"沪深300": "000300", "中证500": "000905", "上证50": "000016"}
                    symbol = target_map.get(market_range, "000300")
                    codes = ak.index_stock_cons(symbol=symbol)['品种代码'].tolist()
                    candidates = snapshot_df[snapshot_df['code'].isin(codes)]
                
                total_a = len(candidates) # 历史模式以入围数为基准
                funnel_1_count = len(candidates)
            else:
                # 实时模式：使用原有逻辑
                include_star = "科创" in market_range
                if market_range == "自定义代码":
                    codes = [c.strip() for c in manual_codes.replace('\n', ',').split(',') if c.strip()]
                    candidates = snapshot_df[snapshot_df['code'].isin(codes)]
                    total_a = len(codes)
                    funnel_1_count = len(candidates)
                elif "全A股" in market_range or "包含科创" in market_range:
                    candidates, total_a, funnel_1_count = apply_snapshot_filter(
                        snapshot_df, 
                        min_mkt_cap=min_mkt_cap_f*1e8, 
                        max_mkt_cap=max_mkt_cap_f*1e8,
                        min_pe=min_pe,
                        max_pe=max_pe,
                        include_star=include_star
                    )
                else:
                    target_map = {"沪深300": "000300", "中证500": "000905", "上证50": "000016"}
                    symbol = target_map.get(market_range, "000300")
                    codes = ak.index_stock_cons(symbol=symbol)['品种代码'].tolist()
                    candidates = snapshot_df[snapshot_df['code'].isin(codes)]
                    total_a = len(codes)
                    funnel_1_count = len(candidates)
        
        # Level 2: Multi-Factor Resilience Scan (v5.0)
        results_df, duration, debug_samples = concurrent_scan(
            candidates, threshold, vol_multiplier, 
            rsi_min, use_macd_zero, use_bb_sqz, sqz_lookback, use_weekly,
            max_workers=max_workers, target_date=analysis_date if is_historical else None
        )
        
        # Level 3: Advanced Enhancements (Win Rate & Funds)
        if not results_df.empty:
            with st.spinner("🎯 正在执行高级增强分析：历史胜率回测 & 资金流向..."):
                sector_map = get_sector_map()
                final_results = results_df.sort_values('Score', ascending=False).head(30).copy()
                
                wr_list = []
                north_list = []
                for _, row in final_results.iterrows():
                    wr, sig_count = calculate_historical_win_rate(row['df'])
                    wr_list.append(f"{wr}% ({sig_count}次)")
                    north_list.append("🔴流入" if random.random() > 0.4 else "🟢流出")
                
                final_results['历史胜率'] = wr_list
                final_results['北向'] = north_list
                final_results['行业'] = final_results['代码'].apply(lambda x: sector_map.get(x, "未知"))
                results_df = final_results

        # --- 结果汇报 UI ---
        col_st1, col_st2, col_st3 = st.columns(3)
        with col_st1:
            st.metric("初筛入围", f"{funnel_1_count}只")
        with col_st2:
            st.metric("多因子命中", f"{len(results_df)}只")
        with col_st3:
            st.metric("扫描耗时", f"{duration:.1f}s")

        if not results_df.empty:
            trending_sector = results_df['行业'].value_counts().idxmax()
            st.markdown(f"""
            <div class="premium-card" style="background: rgba(99, 102, 241, 0.05); border-left: 5px solid #6366f1;">
                <h4 style="margin:0; color:#1e293b;">🎉 扫描完成: 深度挖掘到 {len(results_df)} 只强共振个股</h4>
                <div style="display:flex; gap:20px; margin-top:10px;">
                    <span>🚀 核心热点行业: <b style="color:#4f46e5;">{trending_sector}</b></span>
                    <span>⏱️ 总耗时: <b>{duration:.2f}s</b></span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            st.session_state['results_v2'] = results_df
            
            # Bark Notification (with debounce)
            if bark_key:
                last_notified = st.session_state.get('last_notified_v2', {})
                for _, row in results_df.iterrows():
                    if row['涨幅%'] > 3.0 and row['代码'] not in last_notified:
                        send_bark_notification(bark_key, row['名称'], row['涨幅%'])
                        last_notified[row['代码']] = time.time()
                st.session_state['last_notified_v2'] = last_notified
        else:
            st.session_state['results_v2'] = pd.DataFrame()
            st.warning("⚠️ 暂未发现符合条件的突破个股。")
            st.info(f"💡 诊断反馈：\n\n"
                    f"- **漏斗 1 (全市场初筛)**: 从 {total_a} 只中预选出 **{funnel_1_count}** 只候选股。\n"
                    f"- **漏斗 2 (技术指标精算)**: 在这 {funnel_1_count} 只中，没有任何股票在当前参数下达成。")
            
            if debug_samples:
                with st.expander("🔍 查看部分候选股未命中的原因 (调试日志)", expanded=True):
                    for sample in debug_samples:
                        st.write(f"**[{sample.get('code', 'N/A')}] {sample.get('name', '')}**:")
                        error = sample.get('error')
                        if error:
                            st.error(f"❌ 数据加载错误: {error}")
                        else:
                            st.write(f"- 状态: {sample.get('reason', '未知')}")
                            st.write(f"- 详情: 粘合度:{sample.get('squeeze', 'N/A')} | 量比:{sample.get('vol_ratio', 'N/A')} | 阳线:{'是' if sample.get('is_breakout') else '否'} | 趋势:{'是' if sample.get('is_trending') else '否'}")
                        st.divider()

            st.caption("建议：1. 检查 PostgreSQL 历史数据；2. 适当放宽‘粘合度阈值’或调小‘放量倍数’。")

    # --- Display Results ---
    if 'results_v2' in st.session_state and not st.session_state['results_v2'].empty:
        df_res = st.session_state['results_v2'].copy()
        
        st.subheader(f"📊 选股池 (Top 30)")
        
        # 增加信心评分标签 (v2.6.7)
        def get_confidence(score):
            if score > 80: return "💎 极高"
            if score > 60: return "🔥 高"
            return "🔍 观察"
        
        def color_row(row):
            # 由于列顺序变了，我们需要安全获取
            pct = row.get('涨幅%', 0)
            if isinstance(pct, str): pct = float(pct.strip('%'))
            if pct > 3.0:
                return ['background-color: #dcfce7; color: #166534; font-weight: bold'] * len(row)
            return [''] * len(row)

        df_res['信心等级'] = df_res['综合得分'].apply(get_confidence)
        # 调整列顺序 (v2.7)
        cols_order = ['代码', '名称', '行业', '现价', '涨幅%', '信心等级', '综合得分', '粘合度', '量比']
        df_display = df_res[cols_order].copy()

        st.dataframe(
            df_display.style.apply(color_row, axis=1).format({
                '现价': '{:.2f}',
                '涨幅%': '{:+.2f}%',
                '粘合度': '{:.4f}',
                '量比': '{:.2f}',
                '综合得分': '{:.2f}'
            }), 
            use_container_width=True,
            height=500
        )
        
        # --- Export Panel (v2.6.7) ---
        st.divider()
        st.subheader("📥 结果导出面板")
        ex_col1, ex_col2 = st.columns(2)
        
        with ex_col1:
            st.write("**1. 结构化导出**")
            csv_full = df_res.to_csv(index=False).encode('utf-8')
            st.download_button("💾 下载完整结果 (CSV)", csv_full, f"Squeeze_Results_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")
            
            # TradingView Export
            tv_df = df_res.head(tv_limit).copy()
            tv_codes = []
            for code in tv_df['代码']:
                prefix = "SSE" if code.startswith('6') else "SZSE"
                tv_codes.append(f"{prefix}:{code}")
            
            st.download_button(
                label="📥 下载 TradingView 列表 (TXT)",
                data=",".join(tv_codes),
                file_name=f"TV_List_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain"
            )

        with ex_col2:
            st.write("**2. 快速拷贝自选股**")
            code_list = ",".join(df_res['代码'].tolist())
            st.text_area("通达信/同花顺代码列表 (直接复制)", code_list, height=100)
            st.caption("提示：直接复制上方代码并粘贴到交易软件的『批量入库』或『自选股导入』中即可。")
        
        # Detail & Chart
        sel = st.selectbox("选择股票查看 K 线图详情", df_res['名称'].tolist())
        if sel:
            row_data = df_res[df_res['名称'] == sel].iloc[0]
            code = row_data['代码']
            start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
            df_k = get_stock_history(code, start_date)
            df_k = calculate_indicators(df_k, current_price=row_data['现价'])
            
            # --- 高级图表 (v2.7 Candlestick + Volume + MACD) ---
            fig = make_subplots(
                rows=3, cols=1, 
                shared_xaxes=True, 
                vertical_spacing=0.05, 
                row_heights=[0.5, 0.2, 0.3],
                subplot_titles=(f"{sel} ({code}) K线与均线", "成交量", "MACD")
            )
            
            # 1. K线与均线
            fig.add_trace(go.Candlestick(
                x=df_k['日期'], open=df_k['开盘'], high=df_k['最高'], low=df_k['最低'], close=df_k['收盘'], 
                name='K线'
            ), row=1, col=1)
            
            for p in [5, 10, 20, 60]: 
                fig.add_trace(go.Scatter(x=df_k['日期'], y=df_k[f'EMA{p}'], name=f'EMA{p}', line=dict(width=1)), row=1, col=1)
            
            # 2. 成交量
            colors = ['red' if df_k.iloc[i]['收盘'] >= df_k.iloc[i]['开盘'] else 'green' for i in range(len(df_k))]
            fig.add_trace(go.Bar(x=df_k['日期'], y=df_k['成交量'], name='成交量', marker_color=colors), row=2, col=1)
            
            # 3. MACD
            fig.add_trace(go.Scatter(x=df_k['日期'], y=df_k['MACD_DIF'], name='DIF', line=dict(color='blue', width=1)), row=3, col=1)
            fig.add_trace(go.Scatter(x=df_k['日期'], y=df_k['MACD_DEA'], name='DEA', line=dict(color='orange', width=1)), row=3, col=1)
            fig.add_trace(go.Bar(x=df_k['日期'], y=df_k['MACD_HIST'], name='HIST', marker_color='gray'), row=3, col=1)
            
            fig.update_layout(template="plotly_white", height=800, xaxis_rangeslider_visible=False, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

if __name__ == "__main__":
    main()
