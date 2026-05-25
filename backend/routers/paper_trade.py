"""
Paper trading router - simulated trading management endpoints.

Extracted from api.py.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from typing import Dict, Any
from datetime import datetime
import pandas as pd

from core.logging_config import logger
from core.db import get_db_engine, validate_stock_code
from core.data import get_market_snapshot, get_sector_map
from core.analytics import run_monte_carlo, calculate_rolling_performance, calculate_risk_metrics, calculate_pnl_attribution
from schemas.paper_trade import PaperTradeCreate, PaperTradeClose

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])


@router.post("/add")
def add_paper_trade(trade: PaperTradeCreate) -> Dict[str, Any]:
    """Add a paper trade entry with sector concentration check"""
    if not validate_stock_code(trade.code):
        return {"status": "error", "detail": "Invalid stock code format"}

    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        # --- 行业集中度控制 (Sector Exposure Control) ---
        MAX_SECTOR_POSITIONS = 2  # 同行业最多 2 个持仓
        sector_map = get_sector_map()
        new_industry = sector_map.get(trade.code, '未知')
        
        open_df = pd.read_sql(text("SELECT code FROM paper_trading WHERE status = :status"), engine, params={"status": "OPEN"})
        if not open_df.empty and new_industry != '未知':
            sector_counts = {}
            for code in open_df['code']:
                ind = sector_map.get(code, '未知')
                sector_counts[ind] = sector_counts.get(ind, 0) + 1
            
            current_count = sector_counts.get(new_industry, 0)
            if current_count >= MAX_SECTOR_POSITIONS:
                return {
                    "status": "warning",
                    "detail": f"行业 [{new_industry}] 已有 {current_count} 个持仓，"
                              f"超过集中度上限 {MAX_SECTOR_POSITIONS}。确认是否继续？",
                    "industry": new_industry,
                    "current_count": current_count
                }

        with engine.connect() as conn:
            conn.execute(text('''
                INSERT INTO paper_trading (code, name, entry_price, entry_date, current_price, status, strategy_type, remark)
                VALUES (:code, :name, :price, :date, :price, 'OPEN', :strategy_type, :remark)
                ON CONFLICT (code, entry_date) DO NOTHING
            '''), {
                "code": trade.code,
                "name": trade.name,
                "price": trade.price,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "strategy_type": trade.strategy_type,
                "remark": trade.remark
            })
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error adding paper trade: {e}")
        return {"status": "error", "detail": "Internal server error"}


@router.get("/list")
def list_paper_trades() -> Dict[str, Any]:
    """List all paper trades with live P&L tracking"""
    engine = get_db_engine()
    if not engine:
        return {"trades": [], "stats": {}}
    try:
        df = pd.read_sql(text("SELECT * FROM paper_trading ORDER BY entry_date DESC"), engine)
        if df.empty:
            return {"trades": [], "stats": {"total_trades": 0, "win_rate": 0, "total_pl_pct": 0, "avg_hold_days": 0}}

        # --- 获取最新价格 ---
        codes = df['code'].unique().tolist()

        # 方法1: 从 daily_k 表获取最新收盘价
        price_map = {}
        try:
            placeholders = ','.join([f':code_{i}' for i in range(len(codes))])
            params = {f"code_{i}": c for i, c in enumerate(codes)}
            price_df = pd.read_sql(text(f"""
                SELECT DISTINCT ON (code) code, close as latest_price, date as latest_date
                FROM daily_k
                WHERE code IN ({placeholders})
                ORDER BY code, date DESC
            """), engine, params=params)
            for _, row in price_df.iterrows():
                price_map[row['code']] = float(row['latest_price'])
        except Exception as e:
            logger.warning(f"Paper trading: DB price fetch failed: {e}")

        # 方法2: 尝试从实时快照补充缺失的
        if len(price_map) < len(codes):
            try:
                snapshot = get_market_snapshot()
                if not snapshot.empty:
                    for code in codes:
                        if code not in price_map:
                            match = snapshot[snapshot['code'] == code]
                            if not match.empty:
                                price_map[code] = float(match.iloc[0]['price'])
            except Exception as e:
                logger.warning(f"Failed to fetch market snapshot for paper trading price tracking: {e}")

        # --- 计算每笔交易的盈亏 ---
        trades = []
        sector_map_data = {}

        # 获取行业映射
        try:
            sector_df = pd.read_sql(text("SELECT code, industry FROM stock_basic WHERE industry IS NOT NULL"), engine)
            sector_map_data = dict(zip(sector_df['code'], sector_df['industry']))
        except Exception:
            pass

        # 收集需要批量更新的记录
        price_updates = []

        for _, row in df.iterrows():
            code = row['code']
            status = row.get('status', 'OPEN')
            entry_price = float(row['entry_price'])
            entry_date = pd.to_datetime(row['entry_date'])
            high_since_entry = float(row.get('high_since_entry') or entry_price)

            # --- 价格与日期处理 ---
            if status == 'CLOSED':
                current_price = float(row.get('close_price') or row.get('current_price') or entry_price)
                close_date = pd.to_datetime(row.get('close_date') or datetime.now())
                hold_days = (close_date - entry_date).days
            else:
                current_price = price_map.get(code, entry_price)  # fallback to entry
                hold_days = (datetime.now() - entry_date).days
                
                # --- 移动止损数据更新 ---
                high_since_entry = max(high_since_entry, current_price)
                # 即使价格没变，我们也需要 high_since_entry 来更新
                price_updates.append({"price": current_price, "high": high_since_entry, "id": int(row['id'])})

            pl = current_price - entry_price
            pl_pct = (pl / entry_price * 100) if entry_price > 0 else 0

            industry = sector_map_data.get(code, '未知')

            trade_data = {
                "id": int(row['id']),
                "code": code,
                "name": row['name'],
                "entry_price": round(entry_price, 2),
                "current_price": round(current_price, 2),
                "entry_date": str(row['entry_date']),
                "pl": round(pl, 2),
                "pl_pct": round(pl_pct, 2),
                "hold_days": max(0, hold_days),
                "industry": industry,
                "status": status,
                "high_since_entry": round(high_since_entry, 2) if status == 'OPEN' else None,
                "close_price": round(float(row['close_price']), 2) if row.get('close_price') is not None else None,
                "close_date": str(row['close_date']) if row.get('close_date') is not None else None,
                "remark": row.get('remark') if row.get('remark') is not None else None
            }
            trades.append(trade_data)

        # 批量更新价格与最高点
        if price_updates:
            try:
                with engine.connect() as conn:
                    conn.execute(
                        text("UPDATE paper_trading SET current_price = :price, high_since_entry = :high WHERE id = :id"),
                        price_updates,
                    )
                    conn.commit()
            except Exception as e:
                logger.warning(f"Batch paper trading update failed: {e}")

        # --- 汇总统计 ---
        wins = sum(1 for t in trades if t['pl_pct'] > 0)
        losses = sum(1 for t in trades if t['pl_pct'] < 0)
        flat = sum(1 for t in trades if t['pl_pct'] == 0)
        total = len(trades)
        avg_pl = sum(t['pl_pct'] for t in trades) / total if total > 0 else 0
        avg_hold = sum(t['hold_days'] for t in trades) / total if total > 0 else 0

        # 按板块汇总胜率
        sector_stats = {}
        for t in trades:
            ind = t['industry']
            if ind not in sector_stats:
                sector_stats[ind] = {"wins": 0, "total": 0}
            sector_stats[ind]["total"] += 1
            if t['pl_pct'] > 0:
                sector_stats[ind]["wins"] += 1

        sector_distribution = [
            {"name": k, "value": round(v["wins"] / v["total"] * 100) if v["total"] > 0 else 0, "count": v["total"]}
            for k, v in sector_stats.items()
        ]
        sector_distribution.sort(key=lambda x: x["value"], reverse=True)

        # 最大单笔盈利/亏损
        best = max(trades, key=lambda t: t['pl_pct']) if trades else None
        worst = min(trades, key=lambda t: t['pl_pct']) if trades else None

        # 最大回撤计算
        cumulative_returns = []
        running_sum = 0
        for t in sorted(trades, key=lambda x: x['entry_date']):
            running_sum += t['pl_pct']
            cumulative_returns.append(running_sum)
        
        max_drawdown = 0
        if cumulative_returns:
            peak = -9999
            for val in cumulative_returns:
                if val > peak: peak = val
                drawdown = peak - val
                if drawdown > max_drawdown: max_drawdown = drawdown

        # 盈亏比
        gain_trades = [t['pl_pct'] for t in trades if t['pl_pct'] > 0]
        loss_trades = [abs(t['pl_pct']) for t in trades if t['pl_pct'] < 0]
        profit_factor = round(sum(gain_trades) / sum(loss_trades), 2) if loss_trades and sum(loss_trades) > 0 else (9.9 if gain_trades else 0)

        stats = {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "flat": flat,
            "win_rate": round(wins / total * 100) if total > 0 else 0,
            "avg_pl_pct": round(avg_pl, 2),
            "total_pl_pct": round(sum(t['pl_pct'] for t in trades), 2),
            "avg_hold_days": round(avg_hold, 1),
            "max_drawdown": round(max_drawdown, 2),
            "profit_factor": profit_factor,
            "best_trade": {"name": best['name'], "pl_pct": best['pl_pct']} if best else None,
            "worst_trade": {"name": worst['name'], "pl_pct": worst['pl_pct']} if worst else None,
            "sector_distribution": sector_distribution,
            "monte_carlo": run_monte_carlo([t['pl_pct'] for t in trades]),
            "rolling_performance": calculate_rolling_performance(trades),
            "risk_metrics": calculate_risk_metrics(trades),
            "pnl_attribution": calculate_pnl_attribution(trades)
        }

        return {"trades": trades, "stats": stats}
    except Exception as e:
        logger.error(f"Error listing paper trades: {e}")
        import traceback
        traceback.print_exc()
        return {"trades": [], "stats": {}}


@router.delete("/remove/{id}")
def remove_paper_trade(id: int) -> Dict[str, str]:
    """Remove a paper trade by ID"""
    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM paper_trading WHERE id = :id"), {"id": id})
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error removing paper trade: {e}")
        return {"status": "error"}


@router.post("/close/{id}")
def close_paper_trade(id: int, data: PaperTradeClose) -> Dict[str, Any]:
    """平仓: 记录卖出价格和日期，将交易标记为 CLOSED"""
    close_price = data.close_price

    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        with engine.connect() as conn:
            # 检查交易是否存在
            result = conn.execute(text("SELECT * FROM paper_trading WHERE id = :id"), {"id": id})
            trade = result.fetchone()
            if not trade:
                raise HTTPException(status_code=404, detail="交易记录不存在")

            conn.execute(text("""
                UPDATE paper_trading
                SET close_price = :close_price, close_date = :close_date, status = 'CLOSED'
                WHERE id = :id
            """), {
                "close_price": float(close_price),
                "close_date": datetime.now().strftime("%Y-%m-%d"),
                "id": id
            })
            conn.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing paper trade: {e}")
        return {"status": "error", "detail": str(e)}


@router.post("/wind-control")
def run_wind_control() -> Dict[str, Any]:
    """风控：自动扫描所有持仓，根据多重止损逻辑触发卖出通知/平仓
    
    止损规则优先级:
    1. 固定止损: 跌破入场成本 -9%
    2. ATR 移动止盈: 从持仓期最高价回撤 -8%
    3. 大盘极端风控: 双指数破位时强制清仓
    4. 时间止损: 持仓超过 N 天且未盈利 → 自动平仓
    """
    TIME_STOP_DAYS = 5  # 时间止损天数
    
    engine = get_db_engine()
    if not engine: return {"status": "error"}
    
    try:
        df = pd.read_sql(text("SELECT * FROM paper_trading WHERE status = :status"), engine, params={"status": "OPEN"})
        if df.empty: return {"status": "success", "closed_count": 0}
        
        from core.data import get_market_snapshot
        snapshot = get_market_snapshot()
        if snapshot.empty: return {"status": "error", "detail": "市场行情不可用"}
        
        # 获取当前大盘环境
        from core.data import get_market_regime
        regime = get_market_regime()
        regime_status = regime.get("status", "UNKNOWN")

        closed_count = 0
        alerts = []
        close_updates = []  # 收集批量更新参数
        close_date_str = datetime.now().strftime("%Y-%m-%d")

        for _, row in df.iterrows():
            code = row['code']
            entry_price = float(row['entry_price'])
            entry_date = pd.to_datetime(row['entry_date'])
            high_since_entry = float(row['high_since_entry'] or entry_price)
            hold_days = (datetime.now() - entry_date).days
            
            # 获取当前行情
            match = snapshot[snapshot['code'] == code]
            if match.empty: continue
            curr_price = float(match.iloc[0]['price'])
            pl_pct = (curr_price - entry_price) / entry_price * 100
            
            # --- 风控逻辑判定 (按优先级) ---
            stop_level = high_since_entry * 0.92 # 默认跌破高点 8% 触发
            fixed_stop = entry_price * 0.91 # 默认固定止损 9%
            
            reason = ""
            if curr_price <= fixed_stop:
                reason = "触发固定止盈止损线 (入场成本 -9%)"
            elif curr_price <= stop_level:
                reason = "触发 ATR 移动止盈线 (高点回撤 -8%)"
            elif regime_status == "CRITICAL":
                reason = "大盘极度走弱 (双指数破位)，强制清仓避险"
            elif hold_days >= TIME_STOP_DAYS and pl_pct <= 0:
                reason = f"时间止损: 持仓 {hold_days} 天未盈利 ({pl_pct:.1f}%)"

            if reason:
                # 生成更详细的智能备注
                remark = f"{reason}。卖出时大盘状态：{regime.get('desc', 'N/A')}。"
                close_updates.append({
                    "p": curr_price,
                    "d": close_date_str,
                    "r": remark,
                    "id": int(row['id'])
                })
                closed_count += 1
                alerts.append(f"{row['name']}({code}) {reason}")
        
        # 批量执行所有平仓更新 (一次连接，一次 commit)
        if close_updates:
            with engine.connect() as conn:
                conn.execute(text("""
                    UPDATE paper_trading 
                    SET close_price = :p, close_date = :d, status = 'CLOSED', remark = :r
                    WHERE id = :id
                """), close_updates)
                conn.commit()

        return {"status": "success", "closed_count": closed_count, "alerts": alerts}
    except Exception as e:
        logger.error(f"Wind control error: {e}")
        return {"status": "error"}

@router.get("/portfolio/stats")
def get_portfolio_stats() -> Dict[str, Any]:
    """获取组合分析统计数据"""
    engine = get_db_engine()
    if not engine: return {"error": "Database error"}
    
    try:
        # 1. 获取所有交易记录
        df = pd.read_sql(text("SELECT * FROM paper_trading ORDER BY entry_date ASC"), engine)
        if df.empty:
            return {
                "risk_metrics": {},
                "attribution": {"by_industry": [], "by_strategy": []},
                "rolling_performance": []
            }
        
        trades = df.to_dict('records')
        
        # 2. 计算指标
        risk_metrics = calculate_risk_metrics(trades)
        attribution = calculate_pnl_attribution(trades)
        rolling_performance = calculate_rolling_performance(trades)
        
        # 3. 补充持仓热力图数据 (按行业)
        # 获取行业分布 (OPEN 持仓)
        open_df = df[df['status'] == 'OPEN'].copy()
        sector_dist = []
        if not open_df.empty:
            sector_counts = open_df['industry'].value_counts()
            for sector, count in sector_counts.items():
                sector_dist.append({
                    "name": sector,
                    "value": int(count)
                })
        
        return {
            "risk_metrics": risk_metrics,
            "attribution": attribution,
            "rolling_performance": rolling_performance,
            "sector_distribution": sector_dist
        }
    except Exception as e:
        logger.error(f"Error calculating portfolio stats: {e}")
        return {"error": str(e)}
