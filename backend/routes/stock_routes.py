from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from typing import List, Optional
from datetime import datetime, timedelta
import pandas as pd
from pathlib import Path

from models.api_models import ScanRequest
from services.scan_service import run_market_scan
from core.data import get_index_data, get_market_snapshot, get_sector_map
from core.db import get_db_engine, text
from utils.json_utils import sanitize_recursive

router = APIRouter(prefix="/api", tags=["stock"])

@router.get("/market/indices")
def get_indices():
    """获取主要指数行情"""
    return get_index_data()

@router.get("/market/sectors")
def get_sectors():
    """获取热门行业板块"""
    # Original implementation was empty
    return []

@router.get("/scan/dates")
def get_scan_dates_route():
    """获取所有有选股记录的日期"""
    from core.db import get_scan_dates, get_db_engine
    engine = get_db_engine()
    return get_scan_dates(engine)

@router.get("/scan/history")
def get_scan_history_route(date: str = Query(..., description="查询日期 (YYYY-MM-DD)")):
    """获取指定日期的选股记录"""
    from core.db import get_scan_history_by_date, get_db_engine
    engine = get_db_engine()
    results = get_scan_history_by_date(date, engine)
    return sanitize_recursive(results)

@router.post("/scan")
def scan_stocks(req: ScanRequest):
    """Execution of stock scanning based on strategy."""
    results = run_market_scan(
        threshold=req.threshold,
        vol_multiplier=req.vol_multiplier,
        rsi_min=req.rsi_min,
        use_macd_filter=req.use_macd_filter,
        use_bb_sqz=req.use_bb_sqz,
        sqz_lookback=req.sqz_lookback,
        use_weekly=req.use_weekly,
        market_range=req.market_range,
        turnover_min=req.turnover_min,
        mkt_cap_min=req.mkt_cap_min,
        use_rs_filter=req.use_rs_filter,
        local_only=req.local_only,
        strategy=req.strategy,
        rf_period=req.rf_period,
        rf_multiplier=req.rf_multiplier,
        only_signals=req.only_signals,
        use_money_flow=req.use_money_flow,
        money_flow_days=req.money_flow_days,
        scan_date=req.scan_date
    )
    return sanitize_recursive(results)

@router.get("/stock/detail")
def get_stock_detail(code: str):
    """获取股票详细数据（K线、指标、资金流）"""
    from core.db import load_from_db
    from core.indicators import calculate_indicators
    
    engine = get_db_engine()
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    df = load_from_db(code, start_date, engine)
    
    if df.empty:
        raise HTTPException(status_code=404, detail="Stock data not found")
        
    df = calculate_indicators(df)
    data = df.tail(100).to_dict(orient="records")
    return sanitize_recursive(data)

@router.get("/stock/{code}/kline")
async def get_stock_kline(code: str, local_only: bool = False):
    """获取个股 K 线数据供前端绘图"""
    from core.db import load_from_db, save_to_db
    from core.indicators import calculate_indicators
    from core.data import safe_ak_call
    
    engine = get_db_engine()
    target_date = datetime.now()
    start_date_str = (target_date - timedelta(days=300)).strftime("%Y-%m-%d")
    
    df = load_from_db(code, start_date_str, engine)
    
    if df.empty and not local_only:
        try:
            start_fetch = (target_date - timedelta(days=300)).strftime("%Y%m%d")
            df = safe_ak_call("stock_zh_a_hist", symbol=code, period="daily", start_date=start_fetch, adjust="qfq")
            if not df.empty:
                save_to_db(df, code, engine)
        except Exception as e:
            print(f"❌ K-line fetch error for {code}: {e}")
            
    if df.empty:
        return {"code": code, "data": []}
        
    df = calculate_indicators(df, periods=[20, 120, 250])
    
    mapping = {'日期': 'time', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'value'}
    ak_mapping = {'日期': 'time', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'}
    
    col_map = mapping if '收盘' in df.columns else ak_mapping
    plot_df = df.rename(columns=col_map)
    plot_df['time'] = plot_df['time'].astype(str)
    
    cols = ['time', 'open', 'high', 'low', 'close', 'value' if 'value' in plot_df.columns else 'volume', 'EMA20', 'EMA120', 'EMA250']
    records = plot_df[cols].tail(200).to_dict('records')
    
    return {
        "code": code,
        "name": df.iloc[0]['name'] if 'name' in df.columns else "未知",
        "data": records
    }

@router.get("/scan/export")
def export_scan_results(
    date: str = Query(None, description="导出日期 (YYYY-MM-DD)"),
    format: str = Query("csv", description="导出格式: csv 或 excel")
):
    from core.db import get_scan_dates, get_scan_history_by_date
    engine = get_db_engine()
    
    try:
        if not date:
            dates = get_scan_dates(engine)
            if not dates:
                raise HTTPException(status_code=404, detail="暂无扫描结果")
            date = dates[0]

        results = get_scan_history_by_date(date, engine)
        if not results:
            raise HTTPException(status_code=404, detail=f"未找到 {date} 的扫描结果")

        df = pd.DataFrame(results)
        column_mapping = {
            '代码': '代码', '名称': '名称', '现价': '现价', '涨幅%': '涨跌幅(%)',
            '量比': '量比', '换手率': '换手率(%)', 'PE': '市盈率', 'RSI': 'RSI',
            'DIF': 'MACD_DIF', 'BB': '布林带宽度', '粘合度': '均线粘合度',
            'Score': '评分', '行业': '行业', '历史胜率': '历史胜率(%)',
            '信号次数': '信号次数', '北向': '北向资金流向', '北向净流入': '北向净流入(亿)',
            '共振': '板块共振'
        }

        export_columns = [col for col in column_mapping.keys() if col in df.columns]
        df_export = df[export_columns].copy()
        df_export.columns = [column_mapping[col] for col in export_columns]

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir = Path("/tmp")

        if format.lower() == "excel":
            filename = f"scan_results_{date}_{timestamp}.xlsx"
            filepath = temp_dir / filename
            df_export.to_excel(filepath, index=False, engine='openpyxl')
        else:
            filename = f"scan_results_{date}_{timestamp}.csv"
            filepath = temp_dir / filename
            df_export.to_csv(filepath, index=False, encoding='utf-8-sig')

        return FileResponse(path=str(filepath), filename=filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")
