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
        from core.price_action import build_price_action_annotations
        # Calculate all indicators including Pine Script indicators (Range Filter, QQE)
        df = calculate_indicators(df, enable_pine_indicators=True)
        
        # 核心防崩保障：对计算后的 DataFrame 按日期强制去重并按日期严格升序排列，规避任何指标合并导致的时序紊乱或重复
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.drop_duplicates(subset=['日期']).sort_values('日期').reset_index(drop=True)
        df['日期'] = df['日期'].dt.strftime("%Y-%m-%d")
        
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
                    "text": "long" if strategy_type == "tv_zp" else "买点"
                })
                added_dates.add(time_str)
                
        for s in signals.get("sell_signals", []):
            time_str = s["time"]
            if time_str not in added_dates:
                # 止损标记红色，止盈/超时标记绿色
                color = "#e91e63" if "止损" in s["reason"] else "#4caf50"
                reason = s.get("reason", "")
                text_label = "short" if strategy_type == "tv_zp" else (
                    "回测移动止盈" if "移动止盈" in reason else ("回测止损" if "止损" in reason else "回测卖点")
                )
                markers_data.append({
                    "time": time_str,
                    "position": "aboveBar",
                    "color": color,
                    "shape": "arrowDown",
                    "text": text_label
                })
                added_dates.add(time_str)

        # 4. Al Brooks-style price action annotations
        price_action = build_price_action_annotations(df)
        for marker in price_action.get("markers", []):
            markers_data.append(marker)

        # 确保 markers 严格按照时间升序排列，解决 lightweight-charts 的 Assertion failed 崩溃问题
        markers_data.sort(key=lambda x: x["time"])

        # 确保 移动风控线 严格按时间升序且日期唯一（若同一天有多条重叠轨迹，取最低保底止损价）
        trailing_stops_dict = {}
        for ts in signals.get("trailing_stops", []):
            t = ts["time"]
            val = ts["value"]
            if t not in trailing_stops_dict or val < trailing_stops_dict[t]:
                trailing_stops_dict[t] = val
        
        trailing_stops_data = [{"time": t, "value": v} for t, v in sorted(trailing_stops_dict.items())]

        return {
            "code": code,
            "candlestick": candlestick_data,
            "rf_filter": rf_filter_data,
            "markers": markers_data,
            "trailing_stops": trailing_stops_data,
            "price_action": price_action.get("summary", {}),
            "price_action_lines": price_action.get("lines", [])
        }
        
    except Exception as e:
        logger.error(f"Error generating kline data for {code}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
