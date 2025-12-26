from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from datetime import datetime, timedelta
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import random
import socket

socket.setdefaulttimeout(30) # 防止网络请求无限挂起

from core.db import get_db_engine, init_db, load_db_config
from core.data import get_market_snapshot, get_index_data, get_hot_sectors, get_sector_map
from core.indicators import calculate_indicators, get_weekly_indicators
from core.strategy import check_strategy, calculate_historical_win_rate

app = FastAPI(title="Alpha Vision API", version="5.1.0")

# CORS Setup for React Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, replace with specific frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import asyncio

@app.on_event("startup")
async def startup_event():
    print("🏗️ Initializing database...")
    init_db()
    # 异步预热核心缓存
    from core.data import get_index_data, get_hot_sectors
    loop = asyncio.get_event_loop()
    print("🔥 Pre-warming Index and Sector cache...")
    loop.run_in_executor(None, get_index_data)
    loop.run_in_executor(None, get_hot_sectors)
    # sector_map 极慢且涉及大量接口调用，延迟到首次扫描时生成，不在启动时抢占带宽

@app.get("/api/health")
def health_check():
    return {"status": "ok", "time": datetime.now().isoformat()}

@app.get("/api/market/indices")
def get_indices():
    """获取主要指数行情"""
    print("📡 Request: GET /api/market/indices")
    return get_index_data()

@app.get("/api/market/sectors")
def get_sectors():
    """获取热门行业板块"""
    print("📡 Request: GET /api/market/sectors")
    return get_hot_sectors()

@app.get("/api/scan")
def scan_market(
    threshold: float = 0.12,
    vol_multiplier: float = 1.5,
    rsi_min: int = 55,
    use_macd_zero: bool = True,
    use_bb_sqz: bool = True,
    sqz_lookback: int = 10,
    use_weekly: bool = True,
    market_range: str = "包含科创板",
    turnover_min: float = 3.0
):
    """全市场多因子共振扫描"""
    try:
        snapshot_df = get_market_snapshot()
        if snapshot_df.empty:
            raise HTTPException(status_code=503, detail="无法获取市场快照数据")
        
        # 初始过滤 (核心优化：只分析当日上涨且市值 > 80亿，且满足换手率要求的股票)
        candidates = snapshot_df[
            (snapshot_df['pct_chg'] > 0) & 
            (snapshot_df['mkt_cap'] > 8000000000) &
            (snapshot_df['turnover'] >= turnover_min)
        ].copy()
        
        # 1. 处理科创板过滤
        if "包含科创板" not in market_range:
            candidates = candidates[~candidates['code'].astype(str).str.startswith('688')]
            
        # 2. 处理成分股精确过滤
        index_map = {
            "沪深300": "000300",
            "上证50": "000016",
            "中证500": "000905",
            "中证1000": "000852"
        }
        
        target_index = None
        for key, val in index_map.items():
            if key in market_range:
                target_index = val
                break
                
        if target_index:
            try:
                import akshare as ak
                cons_df = ak.index_stock_cons(symbol=target_index)
                if not cons_df.empty:
                    cons_codes = cons_df['品种代码'].tolist()
                    candidates = candidates[candidates['code'].isin(cons_codes)]
            except Exception as e:
                print(f"⚠️ {market_range} filter failed: {e}")

        # 3. 安全检查：如果待扫描数量依然过多，提示用户缩小范围
        if len(candidates) > 800:
            raise HTTPException(
                status_code=400, 
                detail=f"待扫描股票过多 ({len(candidates)}只)，请在筛选中调高阈值或缩小市场范围，以防止超时。"
            )
            
        results = []
        engine = get_db_engine()
        
        # 核心优化：预热指数历史缓存，避免并发时重复拉取
        from core.data import get_index_hist
        get_index_hist("000001")
        
        print(f"🚀 Starting scan for {len(candidates)} candidates...")
        start_time = time.time()
        
        # 并发扫描逻辑 (降低并发数以减少 AkShare 连通错误)
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_stock = {
                executor.submit(
                    single_stock_task, 
                    row['code'], row['name'], row['price'], row['vol'], row['open'],
                    threshold, vol_multiplier, rsi_min, use_macd_zero, use_bb_sqz, sqz_lookback, use_weekly,
                    engine=engine
                ): row for _, row in candidates.iterrows()
            }
            
            for future in as_completed(future_to_stock):
                try:
                    res = future.result()
                    if res: results.append(res)
                except Exception as e:
                    print(f"⚠️ Task failed: {e}")
                    continue
        
        print(f"✅ Scan completed in {time.time() - start_time:.2f}s. Found {len(results)} matches.")
        
        # 排序并取 Top 30
        results = sorted(results, key=lambda x: x['Score'], reverse=True)[:30]
        
        # 补充增强数据 (行业, 胜率)
        sector_map = get_sector_map()
        for res in results:
            res['行业'] = sector_map.get(res['代码'], "未知")
            # 这里的 res 现在包含 df 吗？我们需要修改 single_stock_task 以返回数据或重新加载
            # 暂且返回基础结果
            
        return results
    except HTTPException as he:
        # 允许 HTTPException 直接通过，不再包装成 500
        raise he
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def single_stock_task(code, name, price, vol, open_price, threshold, vol_multiplier, rsi_min, use_macd_zero, use_bb_sqz, sqz_lookback, use_weekly, engine=None):
    from core.db import load_from_db
    import akshare as ak
    
    target_date = datetime.now()
    start_date = (target_date - timedelta(days=250)).strftime("%Y%m%d")
    end_date_str = target_date.strftime("%Y-%m-%d")
    
    df = load_from_db(code, (target_date - timedelta(days=360)).strftime("%Y-%m-%d"), engine)
    
    if df.empty or df.iloc[-1]['日期'] < (target_date - timedelta(days=3)).strftime("%Y-%m-%d"):
        try:
            print(f"📉 [{code}] Fetching fresh data...")
            df_new = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, adjust="qfq")
            if isinstance(df_new, pd.DataFrame) and not df_new.empty:
                df = df_new
                from core.db import save_to_db
                save_to_db(df, code, engine) 
                time.sleep(random.uniform(0.1, 0.3))
        except Exception as e:
            print(f"❌ [{code}] Hist fetch error: {e}")
            return None
            
    if df.empty or len(df) < 120: 
        print(f"⚠️ [{code}] Insufficient data ({len(df)})")
        return None
    
    try:
        df = calculate_indicators(df, current_price=price, current_vol=vol, current_open=open_price)
        match, stats = check_strategy(df, threshold, vol_multiplier, rsi_min, use_macd_zero, use_bb_sqz, sqz_lookback)
        
        if match:
            print(f"✨ [{code}] Resonance Match!")
            if use_weekly:
                if not get_weekly_indicators(code): 
                    print(f"⏩ [{code}] Weekly trend failed")
                    return None
            
            # 增加胜率
            wr, sig_count = calculate_historical_win_rate(df)
            stats['历史胜率'] = f"{wr}%"
            stats['信号次数'] = sig_count
            stats['代码'] = code
            stats['名称'] = name
            return stats
    except Exception as e:
        print(f"❌ [{code}] Analysis error: {e}")
        return None
    
    return None

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
