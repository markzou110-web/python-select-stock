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
from schemas.paper_trade import PaperTradeCreate

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])


@router.post("/add")
def add_paper_trade(trade: PaperTradeCreate) -> Dict[str, Any]:
    """Add a paper trade entry"""
    if not validate_stock_code(trade.code):
        return {"status": "error", "detail": "Invalid stock code format"}

    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        with engine.connect() as conn:
            conn.execute(text('''
                INSERT INTO paper_trading (code, name, entry_price, entry_date, current_price, status, strategy_type)
                VALUES (:code, :name, :price, :date, :price, 'OPEN', :strategy_type)
                ON CONFLICT (code, entry_date) DO NOTHING
            '''), {
                "code": trade.code,
                "name": trade.name,
                "price": trade.price,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "strategy_type": trade.strategy_type
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
        df = pd.read_sql("SELECT * FROM paper_trading ORDER BY entry_date DESC", engine)
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
                SELECT DISTINCT ON (code) code, "收盘" as latest_price, "日期" as latest_date
                FROM daily_k
                WHERE code IN ({placeholders})
                ORDER BY code, "日期" DESC
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
            except:
                pass

        # --- 计算每笔交易的盈亏 ---
        trades = []
        sector_map_data = {}

        # 获取行业映射
        try:
            sector_df = pd.read_sql("SELECT code, industry FROM stock_basic WHERE industry IS NOT NULL", engine)
            sector_map_data = dict(zip(sector_df['code'], sector_df['industry']))
        except:
            pass

        # 收集需要批量更新的记录
        price_updates = []

        for _, row in df.iterrows():
            code = row['code']
            status = row.get('status', 'OPEN')
            entry_price = float(row['entry_price'])
            entry_date = pd.to_datetime(row['entry_date'])

            # --- 价格与日期处理 ---
            if status == 'CLOSED':
                current_price = float(row.get('close_price') or row.get('current_price') or entry_price)
                close_date = pd.to_datetime(row.get('close_date') or datetime.now())
                hold_days = (close_date - entry_date).days
            else:
                current_price = price_map.get(code, entry_price)  # fallback to entry
                hold_days = (datetime.now() - entry_date).days

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
                "close_price": round(float(row['close_price']), 2) if row.get('close_price') is not None else None,
                "close_date": str(row['close_date']) if row.get('close_date') is not None else None
            }
            trades.append(trade_data)

            # 收集 OPEN 状态的 current_price 更新
            if status == 'OPEN' and current_price != row.get('current_price'):
                price_updates.append({"price": current_price, "id": int(row['id'])})

        # 批量更新价格
        if price_updates:
            try:
                with engine.connect() as conn:
                    for u in price_updates:
                        conn.execute(text("UPDATE paper_trading SET current_price = :price WHERE id = :id"), u)
                    conn.commit()
            except Exception as e:
                logger.warning(f"Batch price update failed: {e}")

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

        stats = {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "flat": flat,
            "win_rate": round(wins / total * 100) if total > 0 else 0,
            "avg_pl_pct": round(avg_pl, 2),
            "total_pl_pct": round(sum(t['pl_pct'] for t in trades), 2),
            "avg_hold_days": round(avg_hold, 1),
            "best_trade": {"name": best['name'], "pl_pct": best['pl_pct']} if best else None,
            "worst_trade": {"name": worst['name'], "pl_pct": worst['pl_pct']} if worst else None,
            "sector_distribution": sector_distribution
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
def close_paper_trade(id: int, data: dict) -> Dict[str, Any]:
    """平仓: 记录卖出价格和日期，将交易标记为 CLOSED"""
    close_price = data.get("close_price")
    if close_price is None:
        raise HTTPException(status_code=400, detail="必须提供 close_price")

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
