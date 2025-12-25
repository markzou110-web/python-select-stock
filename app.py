import streamlit as st
import akshare as ak
import pandas as pd
import numpy as np
import plotly.graph_objects as go
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
    /* Main Background - High Contrast Light */
    .stApp {
        background-color: #ffffff;
        color: #1e293b;
    }
    
    /* Metric Cards - Light Mode */
    [data-testid="stMetric"] {
        background: #f8fafc;
        padding: 20px;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    
    /* Custom Status Card - Light Mode */
    .status-card {
        padding: 24px;
        border-radius: 16px;
        background: #f1f5f9;
        border: 1px solid #cbd5e1;
        margin-bottom: 24px;
        color: #0f172a;
    }
    
    /* Headers - High Contrast */
    h1, h2, h3 {
        color: #0f172a !important;
        font-weight: 800 !important;
    }
    
    /* Sidebar - Light/Gray Contrast */
    [data-testid="stSidebar"] {
        background-color: #f8fafc;
        border-right: 1px solid #e2e8f0;
    }
    
    /* Global Text Clarity */
    p, span, label {
        color: #334155 !important;
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
                '总市值': 'mkt_cap'
            })
            return df
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            st.error(f"⚠️ 无法连接数据服务器 (Attempt {attempt+1}/{max_retries}): {e}")
            st.info("💡 建议：请检查网络连接，或稍后再次执行扫描。")
            return pd.DataFrame()

def apply_snapshot_filter(df, min_mkt_cap=30e8, max_mkt_cap=800e8):
    """第一层漏斗：快照初步筛选 (v2.1 市值范围 30亿~800亿)"""
    if df.empty: return df, 0, 0
    total_count = len(df)
    
    # 排除干扰项
    df = df[~df['name'].str.contains("ST|退", na=False)]
    df = df[~df['code'].str.startswith(('8', '4'))] # 排除北交所
    df = df[~df['code'].str.startswith('688')] # 剔除科创板

    # 核心初筛条件 (Hard Filter)
    df_filtered = df[
        (df['pct_chg'] > 2.0) & # 涨幅 > 2%
        (df['turnover'] > 2.0) & # 换手 > 2%
        (df['mkt_cap'] >= min_mkt_cap) & 
        (df['mkt_cap'] <= max_mkt_cap)
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
    
    df['Vol_MA20'] = df['成交量'].rolling(window=20).mean()
    return df

def check_strategy(df, threshold=0.08, vol_multiplier=2.0):
    """执行选股策略逻辑 (v2.6.5 修复诊断数据完整性)"""
    if len(df) < 60: return False, {"reason": f"历史数据不足 ({len(df)}天)"}

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    ma_values = [curr['EMA5'], curr['EMA10'], curr['EMA20'], curr['EMA60']]
    max_ma = max(ma_values)
    min_ma = min(ma_values)
    squeeze = (max_ma - min_ma) / min_ma
    
    is_breakout = curr['收盘'] > max_ma and curr['收盘'] > curr['EMA5'] and curr['收盘'] > curr['开盘']
    is_trending = curr['EMA20'] >= prev['EMA20'] and curr['收盘'] > curr['EMA60']
    vol_ratio = curr['成交量'] / curr['Vol_MA20'] if curr['Vol_MA20'] > 0 else 0
    is_volume = vol_ratio >= vol_multiplier

    debug_info = {
        "squeeze": round(squeeze, 4),
        "vol_ratio": round(vol_ratio, 2),
        "is_breakout": is_breakout,
        "is_trending": is_trending,
        "is_volume": is_volume,
        "curr_price": curr['收盘'],
        "max_ma": round(max_ma, 2)
    }

    if squeeze > threshold:
        debug_info["reason"] = "粘合度不达标"
        return False, debug_info

    if is_breakout and is_trending and is_volume:
        pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
        score = (vol_ratio * 40) + ((0.2 - squeeze) * 100 * 60)
        return True, {
            'price': curr['收盘'],
            'pct_change': round(pct_change, 2),
            'squeeze': round(squeeze, 4),
            'vol_ratio': round(vol_ratio, 2),
            'ema20': curr['EMA20'],
            'score': round(score, 2)
        }
    
    if not is_breakout: debug_info["reason"] = "未突破均线或非阳线"
    elif not is_trending: debug_info["reason"] = "趋势不佳 (EMA20未平收或未在EMA60之上)"
    elif not is_volume: debug_info["reason"] = "量能不足"
    else: debug_info["reason"] = "未知指标未达标"

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

def single_stock_task(code, name, current_price, current_vol, current_open, threshold, vol_multiplier, target_date=None, engine=None):
    """单只股票的分析任务 (v2.6.5 修复阳线判断逻辑)"""
    try:
        # 防封机制 1：随机微小延迟 (100ms - 500ms)
        time.sleep(random.uniform(0.1, 0.5))
        
        # 确定回测的结束时间
        if target_date is None:
            end_date_str = datetime.now().strftime("%Y-%m-%d")
            start_date_delta = 120
        else:
            end_date_str = target_date.strftime("%Y-%m-%d")
            start_date_delta = 180 # 回测建议拉长一点数据
            
        start_date = (datetime.strptime(end_date_str, "%Y-%m-%d") - timedelta(days=start_date_delta)).strftime("%Y%m%d")
        df = get_stock_history(code, start_date, _engine=engine)
        
        if df.empty: return None
        
        # 截断数据到目标日期
        df = df[df['日期'] <= end_date_str].copy()
        if len(df) < 60: return None
        
        # 如果是今天，则注入实时价；如果是历史，则直接用库内收盘价
        is_today = end_date_str == datetime.now().strftime("%Y-%m-%d")
        df = calculate_indicators(df, 
                                  current_price=current_price if is_today else None,
                                  current_vol=current_vol if is_today else None,
                                  current_open=current_open if is_today else None)
        
        match, stats = check_strategy(df, threshold, vol_multiplier)
        
        if match:
            return {
                '代码': code, '名称': name, '现价': stats['price'], 
                '涨幅%': stats['pct_change'], '粘合度': stats['squeeze'], 
                '量比': stats['vol_ratio'], 'ema20': stats['ema20'],
                '综合得分': stats['score']
            }
        else:
            # 返回失败原因以便诊断
            stats['name'] = name
            stats['code'] = code
            return False, stats
    except Exception as e:
        return False, {"error": str(e), "code": code, "name": name}
    return None

def concurrent_scan(candidates, threshold, vol_multiplier, max_workers=10, target_date=None):
    """第二层漏斗：并发历史回测 (Level 2 Funnel)"""
    results = []
    debug_samples = [] # 存储前几个候选股的失败原因
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(candidates)
    
    start_time = time.time()
    
    # 提前获取 engine 以便传给子线程
    engine = get_db_engine()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(single_stock_task, row['code'], row['name'], row['price'], row['vol'], row['open'], threshold, vol_multiplier, target_date, engine): row['code'] 
            for _, row in candidates.iterrows()
        }
        
        for i, future in enumerate(as_completed(futures)):
            res = future.result()
            if isinstance(res, dict) and '代码' in res:
                results.append(res)
            elif isinstance(res, tuple) and res[0] is False and len(debug_samples) < 3:
                # 记录失败详情供调试
                debug_samples.append(res[1])
            
            # 更新进度
            if i % 10 == 0 or i == total - 1:
                prog = (i + 1) / total
                status_text.text(f"🚀 精确分析中: {i+1}/{total}")
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
    st.title("🚀 A股均线粘合监控 [全功能闭环版 v2.6]")
    
    # Sidebar
    st.sidebar.header("⚙️ 核心配置")
    analysis_date = st.sidebar.date_input("分析日期 (默认为今日实时)", datetime.now())
    is_historical = analysis_date < datetime.now().date()
    
    market_range = st.sidebar.selectbox("分析范围", ["全A股 (30亿~800亿)", "沪深300", "中证500", "上证50"])
    threshold = st.sidebar.slider("粘合度阈值 (更小越紧密)", 0.01, 0.30, 0.08)
    vol_multiplier = st.sidebar.number_input("放量倍数 (要求显著入场)", 1.0, 5.0, 2.0)
    max_workers = st.sidebar.slider("并发线程数 (防封建议 <= 10)", 1, 20, 10)
    
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
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("上涨家数", f"📈 {up}", delta=f"{up-down}" if up > down else f"-{down-up}", delta_color="normal")
    with col2:
        st.metric("下跌家数", f"📉 {down}")
    with col3:
        if up < 1500:
            st.error("🥶 市场冰点，慎重出手")
        elif up > 3000:
            st.success("🔥 市场火热，积极寻找机会")
        else:
            st.info("⚖️ 市场震荡中")

    # Workflow Execution
    if st.sidebar.button("🚀 执行扫描") or auto_mode:
        # Level 1
        with st.spinner("正在准备数据快照..."):
            if is_historical:
                # 历史模式：尝试从数据库拉取当日全市场清单
                snapshot_df = get_historical_snapshot(analysis_date)
                if snapshot_df.empty:
                    st.error(f"❌ 数据库中未发现 {analysis_date} 的历史数据，请先点击下方的『全市场同步』。")
                    return
                candidates, total_a, funnel_1_count = snapshot_df, len(snapshot_df), len(snapshot_df)
            else:
                # 实时模式：使用原有逻辑
                if "全A股" in market_range:
                    candidates, total_a, funnel_1_count = apply_snapshot_filter(snapshot_df)
                else:
                    if market_range == "沪深300": codes = ak.index_stock_cons(symbol="000300")['品种代码'].tolist()
                    elif market_range == "中证500": codes = ak.index_stock_cons(symbol="000905")['品种代码'].tolist()
                    else: codes = ak.index_stock_cons(symbol="000016")['品种代码'].tolist()
                    candidates = snapshot_df[snapshot_df['code'].isin(codes)]
                    total_a = len(codes)
                    funnel_1_count = len(candidates)
        
        # Level 2
        results_df, duration, debug_samples = concurrent_scan(candidates, threshold, vol_multiplier, max_workers, target_date=analysis_date if is_historical else None)
        
        # --- 诊断汇报 (v2.6.3) ---
        col_st1, col_st2, col_st3 = st.columns(3)
        with col_st1:
            st.metric("初筛入围 (漏斗1)", f"{funnel_1_count}只")
        with col_st2:
            st.metric("精选命中 (漏斗2)", f"{len(results_df)}只")
        with col_st3:
            st.metric("扫描耗时", f"{duration:.1f}s")

        if not results_df.empty:
            # Score Sorting & Truncation (Top 30)
            initial_count = len(results_df)
            results_df = results_df.sort_values(by='综合得分', ascending=False).head(30)
            final_count = len(results_df)

            # Stats UI Update
            st.markdown(f"""
            <div class="status-card">
                <h4>🎉 扫描完成: 已从 {initial_count} 只候选股中精选出 Top {final_count} 只</h4>
                <p>总耗时: <b>{duration:.2f}s</b> | 效率: <b>{total_a/duration:.1f} 只/秒</b> | 筛选强度: <b>{final_count/total_a*100:.2f}%</b></p>
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
        
        def color_row(row):
            if row['涨幅%'] > 3.0 and row['现价'] > row['ema20']:
                return ['background-color: #dcfce7; color: #166534; font-weight: bold'] * len(row)
            return [''] * len(row)

        st.dataframe(
            df_res.style.apply(color_row, axis=1).format({
                '现价': '{:.2f}',
                '涨幅%': '{:+.2f}%',
                '粘合度': '{:.4f}',
                '量比': '{:.2f}',
                '综合得分': '{:.2f}'
            }), 
            use_container_width=True,
            height=600
        )
        
        # TradingView Export
        tv_df = df_res.head(tv_limit).copy()
        tv_codes = []
        for code in tv_df['代码']:
            prefix = "SSE" if code.startswith('6') else "SZSE"
            tv_codes.append(f"{prefix}:{code}")
        
        st.download_button(
            label="📥 下载 TradingView 列表",
            data=",".join(tv_codes),
            file_name=f"TV_Squeeze_v2_{datetime.now().strftime('%Y%m%d')}.txt",
            mime="text/plain"
        )
        
        # Detail & Chart
        sel = st.selectbox("选择股票查看 K 线图详情", df_res['名称'].tolist())
        if sel:
            row_data = df_res[df_res['名称'] == sel].iloc[0]
            code = row_data['代码']
            start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
            df_k = get_stock_history(code, start_date)
            df_k = calculate_indicators(df_k, current_price=row_data['现价'])
            
            fig = go.Figure(data=[go.Candlestick(x=df_k['日期'], open=df_k['开盘'], high=df_k['最高'], low=df_k['最低'], close=df_k['收盘'], name='K线')])
            for p in [5, 10, 20, 60]: fig.add_trace(go.Scatter(x=df_k['日期'], y=df_k[f'EMA{p}'], name=f'EMA{p}', line=dict(width=1)))
            fig.update_layout(title=f"{sel} ({code}) 详情", template="plotly_white", height=500, xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)

if __name__ == "__main__":
    main()
