"""
Paper trading router - simulated trading management endpoints.

Extracted from api.py.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from typing import Dict, Any
from datetime import datetime, timedelta
import pandas as pd

from core.logging_config import logger
from core.db import get_db_engine, validate_stock_code, save_failure_sample
from core.data import get_market_snapshot, get_sector_map
from core.analytics import run_monte_carlo, calculate_rolling_performance, calculate_risk_metrics, calculate_pnl_attribution
from core.risk_engine import compute_paper_risk_levels, safe_float
from core.risk_constants import (
    FIXED_STOP_LOSS_PCT, FIXED_STOP_LOSS_RATIO,
    TAKE_PROFIT_PCT, TAKE_PROFIT_RATIO,
    TIME_STOP_WARNING_DAYS, TIME_STOP_REVIEW_DAYS,
    TIME_STOP_FORCE_DAYS, TIME_STOP_REVIEW_LOSS_PCT
)
from core.portfolio_risk import evaluate_portfolio_risk_budget
from schemas.paper_trade import PaperTradeCreate, PaperTradeClose

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])


def _time_stop_policy(strategy_type: str | None) -> Dict[str, Any]:
    strategy = (strategy_type or "").lower()
    if strategy == "squeeze":
        return {"warning_days": 7, "review_days": 10, "force_days": 12, "label": "均线粘合/蓄势"}
    if strategy in {"price_action", "brooks", "pa"}:
        return {"warning_days": 7, "review_days": 10, "force_days": 15, "label": "价格行为"}
    return {
        "warning_days": TIME_STOP_WARNING_DAYS,
        "review_days": TIME_STOP_REVIEW_DAYS,
        "force_days": TIME_STOP_FORCE_DAYS,
        "label": "短线共振",
    }


def _fallback_business_hold_days(entry_date: pd.Timestamp, now: datetime) -> int:
    start = entry_date.date() + timedelta(days=1)
    end = now.date()
    if start > end:
        return 0
    return len(pd.bdate_range(start=start, end=end))


def _count_holding_trading_days(engine, code: str, entry_date: pd.Timestamp, now: datetime) -> int:
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT date
                FROM daily_k
                WHERE code = :code AND date > :entry_date AND date <= :today
                ORDER BY date ASC
            """), {
                "code": code,
                "entry_date": entry_date.date(),
                "today": now.date(),
            }).fetchall()
        if rows:
            return len(rows)
    except Exception as exc:
        logger.warning(f"Failed to count trading hold days for {code}: {exc}")
    return _fallback_business_hold_days(entry_date, now)


def _evaluate_time_stop(hold_trading_days: int, pl_pct: float, strategy_type: str | None) -> Dict[str, Any] | None:
    if pl_pct > 0:
        return None

    policy = _time_stop_policy(strategy_type)
    if hold_trading_days >= policy["force_days"]:
        return {
            "reason": (
                f"时间止损确认: 持仓 {hold_trading_days} 个交易日仍未盈利 "
                f"({pl_pct:.1f}%)，建议平仓或移出实盘持仓"
            ),
            "should_close": True,
            "severity": "close",
        }
    if hold_trading_days >= policy["review_days"]:
        action = "亏损加重，建议减仓/退出候选" if pl_pct <= TIME_STOP_REVIEW_LOSS_PCT else "建议人工复核"
        return {
            "reason": (
                f"时间止损复核: {policy['label']}策略持仓 {hold_trading_days} 个交易日未盈利 "
                f"({pl_pct:.1f}%)，{action}"
            ),
            "should_close": False,
            "severity": "review",
        }
    if hold_trading_days >= policy["warning_days"]:
        return {
            "reason": (
                f"时间止损预警: {policy['label']}策略持仓 {hold_trading_days} 个交易日未盈利 "
                f"({pl_pct:.1f}%)，暂不自动平仓"
            ),
            "should_close": False,
            "severity": "warning",
        }
    return None


def send_paper_trade_notification(title: str, body: str):
    """Sends a push notification via the Notifier, handling both async and sync loops."""
    from core.notifier import notifier
    import asyncio
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
            
        if loop and loop.is_running():
            loop.create_task(notifier.send(title, body, channels=["bark"]))
        else:
            asyncio.run(notifier.send(title, body, channels=["bark"]))
    except Exception as exc:
        logger.error(f"Failed to send paper trading notification: {exc}")


@router.post("/add")
def add_paper_trade(trade: PaperTradeCreate) -> Dict[str, Any]:
    """Add a paper trade entry with sector concentration check"""
    if not validate_stock_code(trade.code):
        return {"status": "error", "detail": "Invalid stock code format"}

    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        budget_check = evaluate_portfolio_risk_budget(engine, trade.model_dump(), force=bool(trade.force))
        if budget_check["status"] == "warning":
            return {
                "status": "warning",
                "detail": "组合风险预算触发，请确认是否继续。",
                "warnings": budget_check["warnings"],
                "summary": budget_check["summary"],
                "budget": budget_check["budget"],
            }

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
            if not trade.force and current_count >= MAX_SECTOR_POSITIONS:
                return {
                    "status": "warning",
                    "detail": f"行业 [{new_industry}] 已有 {current_count} 个持仓，"
                              f"超过集中度上限 {MAX_SECTOR_POSITIONS}。确认是否继续？",
                    "industry": new_industry,
                    "current_count": current_count
                }

        with engine.connect() as conn:
            conn.execute(text('''
                INSERT INTO paper_trading (
                    code, name, entry_price, entry_date, current_price, high_since_entry,
                    status, strategy_type, remark, trade_mode,
                    entry_source, entry_signal_date, entry_reason_snapshot,
                    pa_trade_action, pa_trade_setup, pa_entry_condition, pa_invalidation, pa_risk_pct
                )
                VALUES (
                    :code, :name, :price, :date, :price, :price,
                    'OPEN', :strategy_type, :remark, :trade_mode,
                    :entry_source, CAST(:entry_signal_date AS DATE), :entry_reason_snapshot,
                    :pa_trade_action, :pa_trade_setup, :pa_entry_condition, :pa_invalidation, :pa_risk_pct
                )
                ON CONFLICT (code, entry_date) DO NOTHING
            '''), {
                "code": trade.code,
                "name": trade.name,
                "price": trade.price,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "strategy_type": trade.strategy_type,
                "remark": trade.remark,
                "trade_mode": trade.trade_mode,
                "entry_source": trade.entry_source or "manual_current_price",
                "entry_signal_date": trade.entry_signal_date or datetime.now().strftime("%Y-%m-%d"),
                "entry_reason_snapshot": trade.entry_reason_snapshot or trade.remark,
                "pa_trade_action": trade.pa_trade_action,
                "pa_trade_setup": trade.pa_trade_setup,
                "pa_entry_condition": trade.pa_entry_condition,
                "pa_invalidation": trade.pa_invalidation,
                "pa_risk_pct": trade.pa_risk_pct,
            })
            conn.commit()

        # 发送 Bark 实时推送 — 根据交易模式区分标题（使用统一风控常量）
        stop_price = round(trade.price * FIXED_STOP_LOSS_RATIO, 2)
        tp_price = round(trade.price * TAKE_PROFIT_RATIO, 2)
        mode_label = "实盘买入" if trade.trade_mode == "REAL" else "模拟仓买入"
        title = f"【{mode_label}】{trade.name} ({trade.code})"
        body = (
            f"交易模式：{'🔴 实盘' if trade.trade_mode == 'REAL' else '🔵 模拟盘'}\n"
            f"入场价格：¥{trade.price:.2f}\n"
            f"价格来源：{trade.entry_source or 'manual_current_price'}\n"
            f"信号日期：{trade.entry_signal_date or datetime.now().strftime('%Y-%m-%d')}\n"
            f"固定止损：¥{stop_price:.2f} ({FIXED_STOP_LOSS_PCT}%)\n"
            f"目标止盈：¥{tp_price:.2f} (+{TAKE_PROFIT_PCT}%)\n"
            f"交易备注：{trade.remark or '无'}"
        )
        send_paper_trade_notification(title, body)

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
        open_codes = df[df.get('status', 'OPEN') == 'OPEN']['code'].unique().tolist()

        # 方法1: OPEN 持仓优先从实时快照获取盘中价格
        price_map = {}
        high_map = {}
        if open_codes:
            try:
                snapshot = get_market_snapshot()
                if not snapshot.empty:
                    for code in open_codes:
                        match = snapshot[snapshot['code'] == code]
                        if not match.empty:
                            price_map[code] = float(match.iloc[0]['price'])
                            high_map[code] = float(match.iloc[0].get('high') or match.iloc[0]['price'])
            except Exception as e:
                logger.warning(f"Failed to fetch live market snapshot for paper trading price tracking: {e}")

        # 方法2: 实时源缺失时，从 daily_k 表回退最新收盘价
        try:
            placeholders = ','.join([f':code_{i}' for i in range(len(codes))])
            params = {f"code_{i}": c for i, c in enumerate(codes)}
            price_df = pd.read_sql(text(f"""
                SELECT DISTINCT ON (code) code, close as latest_price, high, date as latest_date
                FROM daily_k
                WHERE code IN ({placeholders})
                ORDER BY code, date DESC
            """), engine, params=params)
            for _, row in price_df.iterrows():
                if row['code'] not in price_map:
                    price_map[row['code']] = float(row['latest_price'])
                    high_map[row['code']] = float(row.get('high') or row['latest_price'])
        except Exception as e:
            logger.warning(f"Paper trading: DB price fetch failed: {e}")

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
            entry_price = safe_float(row['entry_price'])
            entry_date = pd.to_datetime(row['entry_date'])
            high_since_entry = safe_float(row.get('high_since_entry'), entry_price)

            # --- 价格与日期处理 ---
            if status == 'CLOSED':
                current_price = float(row.get('close_price') or row.get('current_price') or entry_price)
                close_date = pd.to_datetime(row.get('close_date') or datetime.now())
                hold_days = (close_date - entry_date).days
            else:
                current_price = price_map.get(code, entry_price)  # fallback to entry
                hold_days = (datetime.now() - entry_date).days
                
                # --- 移动止损数据更新 ---
                current_high = high_map.get(code, current_price)
                high_since_entry = max(high_since_entry, current_price, current_high)
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
                "close_source": row.get('close_source') if row.get('close_source') is not None else None,
                "closed_by": row.get('closed_by') if row.get('closed_by') is not None else None,
                "updated_at": str(row.get('updated_at')) if row.get('updated_at') is not None else None,
                "remark": row.get('remark') if row.get('remark') is not None else None,
                "trade_mode": row.get('trade_mode', 'SIMULATED') or 'SIMULATED',
                "entry_source": row.get('entry_source') if row.get('entry_source') is not None else None,
                "entry_signal_date": str(row.get('entry_signal_date')) if row.get('entry_signal_date') is not None else None,
                "entry_reason_snapshot": row.get('entry_reason_snapshot') if row.get('entry_reason_snapshot') is not None else None,
                "pa_trade_action": row.get('pa_trade_action') if row.get('pa_trade_action') is not None else None,
                "pa_trade_setup": row.get('pa_trade_setup') if row.get('pa_trade_setup') is not None else None,
                "pa_entry_condition": row.get('pa_entry_condition') if row.get('pa_entry_condition') is not None else None,
                "pa_invalidation": row.get('pa_invalidation') if row.get('pa_invalidation') is not None else None,
                "pa_risk_pct": safe_float(row.get('pa_risk_pct')) if row.get('pa_risk_pct') is not None else None,
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

        # --- 按交易模式分组统计 ---
        def _calc_mode_stats(mode_trades):
            if not mode_trades:
                return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "avg_pl_pct": 0, "total_pl_pct": 0, "avg_hold_days": 0}
            m_wins = sum(1 for t in mode_trades if t['pl_pct'] > 0)
            m_losses = sum(1 for t in mode_trades if t['pl_pct'] < 0)
            m_total = len(mode_trades)
            return {
                "total": m_total,
                "wins": m_wins,
                "losses": m_losses,
                "win_rate": round(m_wins / m_total * 100) if m_total > 0 else 0,
                "avg_pl_pct": round(sum(t['pl_pct'] for t in mode_trades) / m_total, 2),
                "total_pl_pct": round(sum(t['pl_pct'] for t in mode_trades), 2),
                "avg_hold_days": round(sum(t['hold_days'] for t in mode_trades) / m_total, 1)
            }

        sim_trades = [t for t in trades if t.get('trade_mode') == 'SIMULATED']
        real_trades = [t for t in trades if t.get('trade_mode') == 'REAL']
        stats_by_mode = {
            "SIMULATED": _calc_mode_stats(sim_trades),
            "REAL": _calc_mode_stats(real_trades)
        }

        return {"trades": trades, "stats": stats, "stats_by_mode": stats_by_mode}
    except Exception as e:
        logger.error(f"Error listing paper trades: {e}")
        import traceback
        traceback.print_exc()
        return {"trades": [], "stats": {}}


@router.post("/convert/{id}")
def convert_trade_mode(id: int) -> Dict[str, Any]:
    """将模拟盘交易转为实盘交易"""
    engine = get_db_engine()
    if not engine:
        return {"status": "error", "detail": "Database unavailable"}
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT * FROM paper_trading WHERE id = :id"), {"id": id})
            trade = result.fetchone()
            if not trade:
                raise HTTPException(status_code=404, detail="交易记录不存在")

            t_map = trade._mapping
            current_mode = t_map.get("trade_mode", "SIMULATED") or "SIMULATED"
            if current_mode == "REAL":
                return {"status": "warning", "detail": "该交易已经是实盘记录"}

            conn.execute(text("""
                UPDATE paper_trading SET trade_mode = 'REAL' WHERE id = :id
            """), {"id": id})
            conn.commit()

        # 发送 Bark 推送通知
        name = t_map["name"]
        code = t_map["code"]
        entry_price = float(t_map["entry_price"])
        title = f"【模拟转实盘】{name} ({code})"
        body = (
            f"交易模式：🔵 模拟盘 → 🔴 实盘\n"
            f"入场价格：¥{entry_price:.2f}\n"
            f"该记录已标记为真实交易"
        )
        send_paper_trade_notification(title, body)

        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting trade mode: {e}")
        return {"status": "error", "detail": str(e)}


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

            t_map = trade._mapping
            code = t_map["code"]
            name = t_map["name"]
            entry_price = float(t_map["entry_price"])
            trade_mode = t_map.get("trade_mode", "SIMULATED") or "SIMULATED"

            conn.execute(text("""
                UPDATE paper_trading
                SET close_price = :close_price,
                    close_date = :close_date,
                    status = 'CLOSED',
                    close_source = 'manual',
                    closed_by = 'user',
                    updated_at = :updated_at
                WHERE id = :id
            """), {
                "close_price": float(close_price),
                "close_date": datetime.now().strftime("%Y-%m-%d"),
                "updated_at": datetime.now(),
                "id": id
            })
            conn.commit()

        # 发送 Bark 实时推送 — 根据交易模式区分
        pl_pct = (float(close_price) - entry_price) / entry_price * 100
        if pl_pct < 0:
            save_failure_sample({
                "code": code,
                "name": name,
                "sample_date": datetime.now().strftime("%Y-%m-%d"),
                "strategy_type": t_map.get("strategy_type"),
                "failure_type": "manual_loss_close",
                "reason": t_map.get("entry_reason_snapshot") or t_map.get("remark") or "手动亏损平仓",
                "pnl_pct": round(pl_pct, 2),
                "source": "paper_trade_close",
            }, engine)
        mode_label = "实盘平仓" if trade_mode == "REAL" else "模拟仓平仓"
        title = f"【{mode_label}】{name} ({code})"
        body = (
            f"交易模式：{'🔴 实盘' if trade_mode == 'REAL' else '🔵 模拟盘'}\n"
            f"买入价格：¥{entry_price:.2f}\n"
            f"平仓价格：¥{float(close_price):.2f}\n"
            f"累计盈亏：{pl_pct:+.2f}%"
        )
        send_paper_trade_notification(title, body)

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
    1. 统一执行风控: 初始/结构/保本/移动风控取当前有效位
    2. 大盘极端风控: 双指数破位时强制清仓
    3. 时间风控: 按交易日分层预警/复核/确认平仓
    """
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
        warned_real_count = 0
        warned_simulated_count = 0
        alerts = []
        close_updates = []  # 收集批量更新参数
        close_date_str = datetime.now().strftime("%Y-%m-%d")

        for _, row in df.iterrows():
            code = row['code']
            entry_price = safe_float(row['entry_price'])
            entry_date = pd.to_datetime(row['entry_date'])
            high_since_entry = safe_float(row.get('high_since_entry'), entry_price)
            now = datetime.now()
            hold_trading_days = _count_holding_trading_days(engine, code, entry_date, now)
            
            # 获取当前行情
            match = snapshot[snapshot['code'] == code]
            if match.empty: continue
            curr_price = float(match.iloc[0]['price'])
            curr_high = safe_float(match.iloc[0].get('high'), curr_price)
            high_since_entry = max(high_since_entry, curr_high, curr_price)
            pl_pct = (curr_price - entry_price) / entry_price * 100
            
            # --- 风控逻辑判定 (按优先级, 使用统一风控引擎) ---
            risk_levels = compute_paper_risk_levels(entry_price, high_since_entry, curr_price)
            stop_level = risk_levels["active_stop_price"]
            
            reason = ""
            should_close = True
            if curr_price <= stop_level:
                reason = f"触发执行风控价 ¥{stop_level:.2f} ({risk_levels['risk_stage']})"
            elif regime_status == "CRITICAL":
                reason = "大盘极度走弱 (双指数破位)，强制清仓避险"
            else:
                time_stop = _evaluate_time_stop(
                    hold_trading_days,
                    pl_pct,
                    row.get("strategy_type"),
                )
                if time_stop:
                    reason = time_stop["reason"]
                    should_close = bool(time_stop["should_close"])

            if reason:
                # 生成更详细的智能备注
                remark = f"{reason}。卖出时大盘状态：{regime.get('desc', 'N/A')}。"
                # 发送 Bark 风控平仓推送 — 根据交易模式区分
                trade_mode = row.get('trade_mode', 'SIMULATED') or 'SIMULATED'
                if trade_mode == "REAL":
                    warned_real_count += 1
                    alerts.append(f"{row['name']}({code}) 实盘风控预警: {reason}")
                    mode_label = "实盘风控预警"
                    action_line = "系统不会自动平仓，请人工确认是否卖出。"
                elif should_close:
                    close_updates.append({
                        "p": curr_price,
                        "d": close_date_str,
                        "r": remark,
                        "u": datetime.now(),
                        "id": int(row['id'])
                    })
                    closed_count += 1
                    alerts.append(f"{row['name']}({code}) 模拟仓自动平仓: {reason}")
                    mode_label = "模拟仓风控平仓"
                    action_line = "模拟仓已按风控规则自动平仓。"
                else:
                    warned_simulated_count += 1
                    alerts.append(f"{row['name']}({code}) 模拟仓风控预警: {reason}")
                    mode_label = "模拟仓风控预警"
                    action_line = "模拟仓暂不自动平仓，请人工复核。"
                title = f"【{mode_label}】{row['name']} ({code})"
                body = (
                    f"交易模式：{'🔴 实盘' if trade_mode == 'REAL' else '🔵 模拟盘'}\n"
                    f"处理方式：{action_line}\n"
                    f"风控原因：{reason}\n"
                    f"买入价格：¥{entry_price:.2f}\n"
                    f"当前价格：¥{curr_price:.2f}\n"
                    f"当前收益：{pl_pct:+.2f}%\n"
                    f"大盘状态：{regime.get('desc', 'N/A')}"
                )
                send_paper_trade_notification(title, body)
        
        # 批量执行所有平仓更新 (一次连接，一次 commit)
        if close_updates:
            with engine.connect() as conn:
                conn.execute(text("""
                    UPDATE paper_trading 
                    SET close_price = :p,
                        close_date = :d,
                        status = 'CLOSED',
                        remark = :r,
                        close_source = 'wind_control_auto',
                        closed_by = 'system',
                        updated_at = :u
                    WHERE id = :id
                """), close_updates)
                conn.commit()

        return {
            "status": "success",
            "closed_count": closed_count,
            "warned_real_count": warned_real_count,
            "warned_simulated_count": warned_simulated_count,
            "alerts": alerts
        }
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
