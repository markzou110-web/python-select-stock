from fastapi import APIRouter
from typing import Dict, Any, List
from sqlalchemy import text
import pandas as pd
from datetime import datetime

from core.logging_config import logger
from core.db import get_db_engine
from core.indicators import calculate_indicators

router = APIRouter(prefix="/api/alert", tags=["alert"])

@router.get("/list")
def list_alerts(stop_loss_pct: float = -8.0) -> Dict[str, Any]:
    """
    检查持仓（OPEN 状态）标的的风险并生成告警
    风险定义：
    1. 跌破 EMA20：短期趋势走坏
    2. 触及止损：当前价低于入场价的设定的 stop_loss_pct
    """
    engine = get_db_engine()
    if not engine:
        return {"alerts": []}
        
    try:
        # 1. 获取所有状态为 OPEN 的实盘标的
        df_paper = pd.read_sql("SELECT * FROM paper_trading WHERE status = 'OPEN'", engine)
        if df_paper.empty:
            return {"alerts": []}
            
        codes = df_paper['code'].unique().tolist()
        placeholders = ','.join([f':code_{i}' for i in range(len(codes))])
        params = {f"code_{i}": c for i, c in enumerate(codes)}
        
        # 2. 拉取这些标的最近 40 天的 K 线数据来计算 EMA20
        # 确保按 code 和 date 排序
        query = text(f"""
            SELECT code, date as "日期", close as "收盘", open as "开盘", high as "最高", low as "最低", vol as "成交量"
            FROM daily_k
            WHERE code IN ({placeholders})
            ORDER BY code, date ASC
        """)
        
        df_hist = pd.read_sql(query, engine, params=params)
        
        from core.strategy import evaluate_exit_signals
        
        alerts = []
        
        for idx, row in df_paper.iterrows():
            code = row['code']
            entry_price = float(row['entry_price'])
            name = row['name']
            trade_id = row['id']
            high_since_entry = float(row.get('high_since_entry') or entry_price)
            
            stock_hist = df_hist[df_hist['code'] == code].copy()
            if len(stock_hist) < 20:
                continue
                
            # 计算指标
            try:
                # 传入 current_price 以便包含今日实时涨跌
                stock_labeled = calculate_indicators(stock_hist)
                if stock_labeled.empty:
                    continue
                
                latest_data = stock_labeled.iloc[-1]
                current_price = float(latest_data['收盘'])
                pl_pct = (current_price - entry_price) / entry_price * 100
                
                # 调用新的核心评估引擎
                exit_signals = evaluate_exit_signals(
                    stock_labeled, 
                    entry_price, 
                    high_since_entry, 
                    stop_loss_pct=stop_loss_pct
                )
                
                if exit_signals:
                    # 汇总信号
                    reasons = [s['reason'] for s in exit_signals if s['level'] != 'none']
                    if not reasons: # 如果只有 none 级别的信号
                        reasons = [exit_signals[0]['reason']]
                        
                    # 取最高风险等级
                    levels = [s['level'] for s in exit_signals]
                    top_level = "critical" if "critical" in levels else ("warning" if "warning" in levels else "none")
                    
                    # 取第一个建议
                    suggestion = next((s['suggestion'] for s in exit_signals if s['level'] == top_level), exit_signals[0]['suggestion'])

                    alerts.append({
                        "id": trade_id,
                        "code": code,
                        "name": name,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "pl_pct": round(pl_pct, 2),
                        "level": top_level,
                        "reasons": reasons,
                        "suggestion": suggestion,
                        "timestamp": datetime.now().isoformat()
                    })
                    
            except Exception as e:
                logger.error(f"Error processing alert for {code}: {e}")
                continue
                
        # 按风险等级排序 (critical 优先于 warning)
        level_map = {"critical": 0, "warning": 1, "none": 2}
        alerts.sort(key=lambda x: level_map.get(x['level'], 9))
                
        return {"alerts": alerts}
        
    except Exception as e:
        logger.error(f"Error checking alerts: {e}")
        return {"alerts": []}
