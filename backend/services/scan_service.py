import time
import random
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import HTTPException
from sqlalchemy import text

from core.db import get_db_engine, load_from_db, save_to_db
from core.data import get_market_snapshot, safe_ak_call, get_index_hist, get_sector_map, get_northbound_flow
from core.money_flow import get_individual_fund_flow as get_main_flow
from core.strategy import check_strategy, check_range_filter_strategy
from core.indicators import calculate_indicators

def single_stock_task(code, name, price, vol, open_price, threshold, vol_multiplier, rsi_min, use_macd_filter, use_bb_sqz, sqz_lookback, use_weekly, use_rs_filter=True, pe=0, turnover=0, local_only=False, engine=None, strategy="Resonance", rf_period=100, rf_multiplier=3.0, only_signals=False, use_money_flow=False, money_flow_days=3, scan_date=None):
    """
    Analyzes a single stock based on technical indicators and strategy rules.
    """
    target_date = datetime.strptime(scan_date, "%Y-%m-%d") if scan_date else datetime.now()
    start_date = (target_date - timedelta(days=250)).strftime("%Y%m%d")
    
    df = load_from_db(code, (target_date - timedelta(days=360)).strftime("%Y-%m-%d"), engine)
    
    if df.empty and local_only:
        return {"reason": "本地数据缺失 (Local-Only 模式已开启)"}

    if df.empty or df.iloc[-1]['日期'] < (target_date - timedelta(days=3)).strftime("%Y-%m-%d"):
        if local_only:
            if df.empty: return {"reason": "数据库无此代码数据"}
            print(f"⚠️ [{code}] Using stale local data (Local-Only)")
        else:
            try:
                print(f"📉 [{code}] Fetching fresh data...")
                df_new = safe_ak_call("stock_zh_a_hist", symbol=code, period="daily", start_date=start_date, adjust="qfq")
                if isinstance(df_new, pd.DataFrame) and not df_new.empty:
                    df = df_new
                    save_to_db(df, code, engine) 
                    time.sleep(random.uniform(0.1, 0.3))
            except Exception as e:
                print(f"❌ [{code}] Hist fetch error: {e}")
                return {"reason": f"接口请求失败: {str(e)}"}
            
    if df.empty or len(df) < 120: 
        return {"reason": f"历史数据不足({len(df)})"}
    
    try:
        df = calculate_indicators(df, current_price=price, current_vol=vol, current_open=open_price)
        
        if strategy == "Combined":
            rf_match, rf_stats = check_range_filter_strategy(
                df, period=rf_period, multiplier=rf_multiplier, rsi_min=rsi_min
            )
            res_match, res_stats = check_strategy(
                df, threshold=threshold, vol_multiplier=vol_multiplier, rsi_min=rsi_min,
                use_macd_filter=use_macd_filter, use_bb_sqz=use_bb_sqz,
                sqz_lookback=sqz_lookback, use_rs_filter=use_rs_filter,
                use_money_flow_filter=use_money_flow, money_flow_days=money_flow_days
            )
            
            match = rf_match and res_match
            if match:
                stats = res_stats.copy()
                stats.update(rf_stats)
                stats['Score'] = round((res_stats['Score'] + rf_stats['Score']) / 2, 2)
            else:
                reasons = []
                if not res_match: reasons.append(res_stats.get('reason', 'Resonance未放量/粘合'))
                if not rf_match: reasons.append(rf_stats.get('reason', 'RF未上穿'))
                stats = {"reason": " & ".join(reasons)}
        elif strategy == "Range Filter":
            match, stats = check_range_filter_strategy(df, period=rf_period, multiplier=rf_multiplier, rsi_min=rsi_min)
        else:
            match, stats = check_strategy(
                df, threshold=threshold, vol_multiplier=vol_multiplier, rsi_min=rsi_min,
                use_macd_filter=use_macd_filter, use_bb_sqz=use_bb_sqz,
                sqz_lookback=sqz_lookback, use_rs_filter=use_rs_filter,
                use_money_flow_filter=use_money_flow, money_flow_days=money_flow_days
            )
        
        if match:
            stats['代码'] = code
            stats['名称'] = name
            
            if use_weekly:
                # Add weekly analysis if needed... (Simplified for now)
                pass
                
            return stats
        else:
            return stats # Returns dict with 'reason'
            
    except Exception as e:
        return {"reason": f"分析出错: {str(e)}"}

def run_market_scan(
    threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True, use_bb_sqz=False,
    sqz_lookback=10, use_weekly=True, market_range="全市场(除科创)", turnover_min=3.0,
    mkt_cap_min=0.0, use_rs_filter=True, local_only=True, strategy="Resonance",
    rf_period=100, rf_multiplier=3.0, only_signals=False, use_money_flow=False,
    money_flow_days=3, scan_date=None
):
    """
    Performs a market-wide scan for stocks matching defined criteria.
    """
    try:
        snapshot_df = pd.DataFrame()
        engine = get_db_engine()
        
        if scan_date:
             print(f"🕒 Historical Scan Mode: Targeting {scan_date}...")
             with engine.connect() as conn:
                 query = text(f"""
                     SELECT d.code, b.name, d.close as price, d.open, d.high, d.low, d.vol, 
                            1.0 as pct_chg, 5.0 as turnover, 5000000000.0 as mkt_cap 
                     FROM daily_k d
                     LEFT JOIN stock_basic b ON d.code = b.code
                     WHERE d.date = '{scan_date}'
                 """)
                 snapshot_df = pd.read_sql(query, engine)
                 if snapshot_df.empty:
                     raise HTTPException(status_code=400, detail=f"数据库中未找到 {scan_date} 的完整数据")
        
        elif not local_only:
            try:
                snapshot_df = get_market_snapshot()
            except:
                print("⚠️ Network snapshot failed.")

        if snapshot_df.empty:
            print(f"🔄 Switching to LOCAL DB mode...")
            with engine.connect() as conn:
                date_query = text("SELECT date FROM daily_k GROUP BY date HAVING COUNT(*) > 3000 ORDER BY date DESC LIMIT 1")
                max_date_res = conn.execute(date_query).fetchone()
                
            if max_date_res:
                max_date = max_date_res[0]
                query = text(f"SELECT d.code, b.name, d.close as price, d.open, d.high, d.low, d.vol, 1.0 as pct_chg, 5.0 as turnover, 5000000000.0 as mkt_cap FROM daily_k d LEFT JOIN stock_basic b ON d.code = b.code WHERE d.date = '{max_date}'")
                snapshot_df = pd.read_sql(query, engine)
            
        if snapshot_df.empty:
             raise HTTPException(status_code=503, detail="无法获取市场数据。")
        
        snapshot_df['code_str'] = snapshot_df['code'].astype(str)
        snapshot_df['name_str'] = snapshot_df['name'].astype(str)
        
        is_not_st = ~snapshot_df['name_str'].str.contains('ST|退', case=False)
        is_not_bj = ~snapshot_df['code_str'].str.startswith(('8', '4', '920'))
        
        candidates = snapshot_df[
            (snapshot_df['pct_chg'] > 0) & is_not_st & is_not_bj &
            (snapshot_df['mkt_cap'] >= mkt_cap_min * 100000000) &
            (snapshot_df['turnover'] >= turnover_min)
        ].copy()
        
        if "包含科创板" not in market_range:
            candidates = candidates[~candidates['code'].astype(str).str.startswith('688')]
            
        index_map = {"沪深300": "000300", "上证50": "000016", "中证500": "000905", "中证1000": "000852"}
        target_index = next((val for key, val in index_map.items() if key in market_range), None)
        
        if target_index:
            cons_df = safe_ak_call("index_stock_cons", symbol=target_index)
            if not cons_df.empty:
                candidates = candidates[candidates['code'].isin(cons_df['品种代码'].tolist())]

        max_allowed = 6000
        if len(candidates) > max_allowed:
            raise HTTPException(status_code=400, detail=f"待扫描股票过多 ({len(candidates)}只)")
            
        results = []
        get_index_hist("000001") # Warm up
        
        print(f"🚀 Starting scan for {len(candidates)} candidates...")
        workers = 15 if local_only else 8
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_stock = {
                executor.submit(
                    single_stock_task, 
                    row['code'], row['name'], row['price'], row['vol'], row['open'],
                    threshold, vol_multiplier, rsi_min, use_macd_filter, use_bb_sqz, sqz_lookback, use_weekly, use_rs_filter,
                    pe=row.get('pe', 0), turnover=row.get('turnover', 0),
                    local_only=local_only, engine=engine,
                    strategy=strategy, rf_period=rf_period, rf_multiplier=rf_multiplier,
                    only_signals=only_signals, use_money_flow=use_money_flow,
                    money_flow_days=money_flow_days, scan_date=scan_date
                ): row for _, row in candidates.iterrows()
            }
            
            for future in as_completed(future_to_stock):
                res = future.result()
                if isinstance(res, dict) and 'Score' in res:
                    results.append(res)
        
        if only_signals:
            results = [r for r in results if r.get('is_signal')]

        results = sorted(results, key=lambda x: x.get('Score', 0), reverse=True)[:30]
        
        # Supplement details (Industry, Fund Flows)
        sector_map = get_sector_map()
        
        def supplement_details(res):
            code = res['代码']
            res['行业'] = sector_map.get(code, "未知")
            try:
                # Parallelize flow fetching
                res['north_flow'] = get_northbound_flow(code)
                res['main_flow'] = get_main_flow(code, days=money_flow_days)
            except:
                res['north_flow'] = None
                res['main_flow'] = 0
            return res

        print(f"💰 Fetching fund flow data for {len(results)} results in parallel...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(supplement_details, results))

        return results
        
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
