from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
import pandas as pd
from datetime import datetime, timedelta

from core.logging_config import logger
from core.db import get_db_engine
from sqlalchemy import text

router = APIRouter(prefix="/api", tags=["kline"])

@router.get("/kline/{code}")
def get_kline_data(code: str, days: int = 400):
    """
    获取单只股票的 K 线数据，并计算前端图表所需的指标（如 Range Filter 和买卖点）。
    """
    logger.info(f"Fetching kline data for {code} over {days} days")
    try:
        engine = get_db_engine()
        
        # Calculate start date
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        query = text("""
            SELECT date as "日期", close as "收盘", open as "开盘", 
                   high as "最高", low as "最低", vol as "成交量"
            FROM daily_k
            WHERE code = :code AND date >= :start_date
            ORDER BY date ASC
        """)
        
        with engine.connect() as conn:
            df = pd.read_sql(query, conn, params={"code": code, "start_date": start_date})
            
        if df.empty:
            raise HTTPException(status_code=404, detail="No historical data found for this stock.")
            
        from core.indicators import calculate_indicators
        # Calculate all indicators including Pine Script indicators (Range Filter, QQE)
        df = calculate_indicators(df, enable_pine_indicators=True)
        
        # Prepare data for lightweight-charts
        candlestick_data = []
        rf_filter_data = []
        markers_data = []
        
        for index, row in df.iterrows():
            date_str = str(row['日期'])
            
            # 1. Candlestick
            candlestick_data.append({
                "time": date_str,
                "open": float(row['开盘']),
                "high": float(row['最高']),
                "low": float(row['最低']),
                "close": float(row['收盘']),
            })
            
            # 2. Range Filter
            if 'RF_Filter' in df.columns and pd.notna(row['RF_Filter']):
                rf_filter_data.append({
                    "time": date_str,
                    "value": float(row['RF_Filter'])
                })
                
            # 3. Buy/Sell Markers (from Pine Strategy logic or core_signals)
            # Find crossover points for buy signals (simplification based on RF_Upward)
            if 'RF_Upward' in df.columns and row.get('RF_Upward', False):
                # Check if it's the exact crossover point (previous day was not upward)
                prev_upward = df['RF_Upward'].iloc[index - 1] if index > 0 else False
                if not prev_upward:
                    markers_data.append({
                        "time": date_str,
                        "position": "belowBar",
                        "color": "#2196F3", # Blue for Buy
                        "shape": "arrowUp",
                        "text": "Buy"
                    })
                    
            if 'RF_Downward' in df.columns and row.get('RF_Downward', False):
                prev_downward = df['RF_Downward'].iloc[index - 1] if index > 0 else False
                if not prev_downward:
                    markers_data.append({
                        "time": date_str,
                        "position": "aboveBar",
                        "color": "#e91e63", # Red for Sell
                        "shape": "arrowDown",
                        "text": "Sell"
                    })

        return {
            "code": code,
            "candlestick": candlestick_data,
            "rf_filter": rf_filter_data,
            "markers": markers_data
        }
        
    except Exception as e:
        logger.error(f"Error generating kline data for {code}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
