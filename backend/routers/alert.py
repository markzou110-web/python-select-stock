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
        
        alerts = []
        
        for idx, row in df_paper.iterrows():
            code = row['code']
            entry_price = float(row['entry_price'])
            name = row['name']
            trade_id = row['id']
            
            stock_hist = df_hist[df_hist['code'] == code].copy()
            if len(stock_hist) == 0:
                continue
                
            # 计算指标
            try:
                stock_labeled = calculate_indicators(stock_hist)
                if stock_labeled.empty:
                    continue
                latest_data = stock_labeled.iloc[-1]
            except Exception as e:
                logger.error(f"Error calculating indicators for {code}: {e}")
                continue
                
            current_price = float(latest_data['收盘'])
            pl_pct = (current_price - entry_price) / entry_price * 100
            ema20 = float(latest_data.get('EMA20', 0))
            
            reasons = []
            level = "none"
            suggestion = ""
            
            # 告警规则 1: 触及止损
            if pl_pct <= stop_loss_pct:
                reasons.append(f"跌破止损阈值 ({stop_loss_pct}%)")
                level = "critical"
                suggestion = "建议无条件平仓"
            
            # 告警规则 2: 跌破 20 日均线 (波段支撑)
            if current_price < ema20 and ema20 > 0:
                reasons.append(f"跌破波段支撑线 (MA20: {ema20:.2f})")
                if level != "critical":
                    level = "warning"
                    suggestion = "建议减仓或开启保护止损"
            
            # 告警规则 3: 高位大幅回落 (如果当前盈利较高但今日大跌)
            # 例如: 收盘距离最高价跌幅超过 5% 且带有长上影线
            try:
                body = abs(float(latest_data['收盘']) - float(latest_data['开盘']))
                upper_shadow = float(latest_data['最高']) - max(float(latest_data['收盘']), float(latest_data['开盘']))
                shadow_ratio = upper_shadow / body if body > 0 else 0
                day_drop = (float(latest_data['最高']) - current_price) / float(latest_data['最高']) * 100
                if shadow_ratio > 1.5 and day_drop > 4:
                    reasons.append("出现长上影线且高位回落，抛压较重")
                    if level != "critical":
                        level = "warning"
                        suggestion = "关注趋势反转风险，建议锁定部分利润"
            except Exception:
                pass
            
            if len(reasons) > 0:
                alerts.append({
                    "id": trade_id,
                    "code": code,
                    "name": name,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "pl_pct": round(pl_pct, 2),
                    "ema20": round(ema20, 2),
                    "level": level,  # 'warning' or 'critical'
                    "reasons": reasons,
                    "suggestion": suggestion,
                    "timestamp": datetime.now().isoformat()
                })
                
        # 按风险等级排序 (critical 优先于 warning)
        alerts.sort(key=lambda x: 0 if x['level'] == 'critical' else 1)
                
        return {"alerts": alerts}
        
    except Exception as e:
        logger.error(f"Error checking alerts: {e}")
        return {"alerts": []}
