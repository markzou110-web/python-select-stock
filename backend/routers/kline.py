from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
import pandas as pd
from datetime import datetime, timedelta

from core.logging_config import logger
from core.db import get_db_engine
from sqlalchemy import text

router = APIRouter(prefix="/api", tags=["kline"])

@router.get("/kline/{code}")
def get_kline_data(code: str, days: int = 400, strategy_type: str = "squeeze"):
    """
    获取单只股票的 K 线数据，并根据当前选股策略计算前端图表所需的指标与买卖点标记。
    """
    logger.info(f"Fetching kline data for {code} over {days} days with strategy: {strategy_type}")
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

        # 3. 动态加载策略特有的买卖点明细 (均线粘合、多指标共振或Azul共识突破)
        from core.strategy import get_signal_details
        signals = get_signal_details(df, strategy_type=strategy_type)
        
        # 记录已添加标记的日期，避免多线程重叠
        added_dates = set()

        for b in signals.get("buy_signals", []):
            time_str = b["time"]
            if time_str not in added_dates:
                markers_data.append({
                    "time": time_str,
                    "position": "belowBar",
                    "color": "#2196F3", # Blue for Buy
                    "shape": "arrowUp",
                    "text": "Buy"
                })
                added_dates.add(time_str)
                
        for s in signals.get("sell_signals", []):
            time_str = s["time"]
            if time_str not in added_dates:
                # 止损标记红色，止盈/超时标记绿色
                color = "#e91e63" if "止损" in s["reason"] else "#4caf50"
                markers_data.append({
                    "time": time_str,
                    "position": "aboveBar",
                    "color": color,
                    "shape": "arrowDown",
                    "text": s["reason"]
                })
                added_dates.add(time_str)

        return {
            "code": code,
            "candlestick": candlestick_data,
            "rf_filter": rf_filter_data,
            "markers": markers_data,
            "trailing_stops": signals.get("trailing_stops", [])
        }
        
    except Exception as e:
        logger.error(f"Error generating kline data for {code}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
