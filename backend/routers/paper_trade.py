"""
Paper trading router - simulated trading management endpoints.

Extracted from api.py.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from typing import Dict, Any, List
from datetime import datetime, timedelta
import pandas as pd

from core.logging_config import logger
from core.db import get_db_engine, validate_stock_code, save_failure_sample, load_from_db
from core.data import get_cached_data, get_market_snapshot, get_sector_map, get_stale_cache
from core.indicators import calculate_indicators
from core.price_action import analyze_price_action
from core.analytics import run_monte_carlo, calculate_rolling_performance, calculate_risk_metrics, calculate_pnl_attribution
from core.risk_engine import compute_paper_risk_levels, safe_float, track_high_since_entry
from core.risk_constants import (
    FIXED_STOP_LOSS_PCT, FIXED_STOP_LOSS_RATIO,
    TAKE_PROFIT_PCT, TAKE_PROFIT_RATIO,
    TIME_STOP_WARNING_DAYS, TIME_STOP_REVIEW_DAYS,
    TIME_STOP_FORCE_DAYS, TIME_STOP_REVIEW_LOSS_PCT,
    FIRST_PROFIT_TAKE_MARK, FIRST_PROFIT_TAKE_RATIO,
)
from core.portfolio_risk import evaluate_portfolio_risk_budget
from core.operation_plan import alert_priority, build_position_decision_snapshot, evaluate_operation_trigger, operation_bands, position_health_score, pre_trade_check, price_instruction, safe_num
from core.audit_log import record_lifecycle_event
from schemas.paper_trade import PaperTradeCreate, PaperTradeClose

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])


def _empty_mode_stats() -> Dict[str, Any]:
    return {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0,
        "avg_pl_pct": 0,
        "total_pl_pct": 0,
        "avg_hold_days": 0,
    }


def _empty_paper_trade_response() -> Dict[str, Any]:
    return {
        "trades": [],
        "stats": {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "flat": 0,
            "win_rate": 0,
            "avg_pl_pct": 0,
            "total_pl_pct": 0,
            "avg_hold_days": 0,
            "max_drawdown": 0,
            "profit_factor": 0,
            "best_trade": None,
            "worst_trade": None,
            "sector_distribution": [],
            "monte_carlo": None,
            "rolling_performance": [],
            "risk_metrics": None,
            "pnl_attribution": None,
        },
        "stats_by_mode": {
            "SIMULATED": _empty_mode_stats(),
            "REAL": _empty_mode_stats(),
        },
    }


def _optional_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _optional_int(value: Any) -> int | None:
    value = _optional_value(value)
    return int(value) if value is not None else None


def _optional_float(value: Any) -> float | None:
    value = _optional_value(value)
    return float(value) if value is not None else None


def _optional_str(value: Any) -> str | None:
    value = _optional_value(value)
    return str(value) if value is not None else None


def _resolve_trade_theme_and_logic(engine, trade: PaperTradeCreate, industry: str) -> tuple[str, str]:
    theme = trade.theme
    rise_logic = trade.rise_logic
    try:
        with engine.connect() as conn:
            if trade.watchlist_id is not None:
                watchlist_row = conn.execute(text("""
                    SELECT theme, industry, rise_logic, reason
                    FROM watchlist
                    WHERE id = :id
                """), {"id": trade.watchlist_id}).mappings().first()
            else:
                watchlist_row = conn.execute(text("""
                    SELECT theme, industry, rise_logic, reason
                    FROM watchlist
                    WHERE code = :code
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT 1
                """), {"code": trade.code}).mappings().first()
        if watchlist_row:
            theme = theme or watchlist_row.get("theme") or watchlist_row.get("industry")
            rise_logic = rise_logic or watchlist_row.get("rise_logic") or watchlist_row.get("reason")
    except Exception as exc:
        logger.warning(f"Failed to inherit trade theme and logic for {trade.code}: {exc}")

    return (
        theme or industry or "未知题材",
        rise_logic or trade.entry_reason_snapshot or trade.remark or "待补充上涨逻辑",
    )


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


def _local_price_action_summary(engine, code: str) -> Dict[str, Any]:
    """加载本地日线并计算 price action 摘要。

    返回的 dict 会额外带上 ``latest_atr``（最近一根 K 线的 ATR），
    供 compute_paper_risk_levels 用作自适应止损输入，使实盘止损与回测一致。
    """
    try:
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        df = load_from_db(code, start_date, engine)
        if df.empty:
            return {}
        with_indicators = calculate_indicators(df, periods=[5, 10, 20, 60])
        summary = analyze_price_action(with_indicators)
        # 计算 ATR 列已在 calculate_indicators 中产出（true_range.rolling(14).mean()）。
        # 若列存在且非空，把最新值附加到摘要里，供风控引擎收紧初始止损。
        if "ATR" in with_indicators.columns and not with_indicators.empty:
            try:
                atr_val = float(with_indicators["ATR"].iloc[-1])
                if atr_val == atr_val:  # 排除 NaN
                    summary["latest_atr"] = atr_val
            except (TypeError, ValueError, IndexError):
                pass
        return summary
    except Exception as exc:
        logger.warning(f"Local price action unavailable for {code}: {exc}")
        return {}


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


def _ensure_trade_journal_table(engine) -> None:
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS trade_journal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_time TIMESTAMP,
                    source VARCHAR(50),
                    code VARCHAR(20),
                    name VARCHAR(50),
                    trade_id INTEGER,
                    advice TEXT,
                    action_taken TEXT,
                    trigger_price FLOAT,
                    guard_price FLOAT,
                    stop_price FLOAT,
                    result_note TEXT
                )
            """))
        else:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS trade_journal_events (
                    id SERIAL PRIMARY KEY,
                    event_time TIMESTAMP,
                    source VARCHAR(50),
                    code VARCHAR(20),
                    name VARCHAR(50),
                    trade_id INTEGER,
                    advice TEXT,
                    action_taken TEXT,
                    trigger_price FLOAT,
                    guard_price FLOAT,
                    stop_price FLOAT,
                    result_note TEXT
                )
            """))
        conn.commit()


def _build_trade_plan(row: Dict[str, Any], current_price: float, high_since_entry: float, risk: Dict[str, Any]) -> Dict[str, Any]:
    entry = safe_num(row.get("entry_price"))
    active_stop = safe_num(risk.get("active_stop_price") or risk.get("stop_price"))
    structure_stop = safe_num(risk.get("structure_stop_price") or risk.get("initial_stop_price"))
    trigger = max(current_price * 1.02, high_since_entry)
    guard = max(active_stop, trigger * 0.985)
    entry_date = pd.to_datetime(row.get("entry_date") or datetime.now())
    policy = _time_stop_policy(row.get("strategy_type"))
    time_stop_date = (entry_date + pd.tseries.offsets.BDay(policy["force_days"])).date().isoformat()
    stop_buffer = (current_price - active_stop) / current_price * 100 if current_price > 0 and active_stop > 0 else 0
    pl_pct = (current_price - entry) / entry * 100 if entry > 0 else 0
    health = position_health_score(pl_pct=pl_pct, stop_buffer_pct=stop_buffer)
    instruction = price_instruction(
        trigger=trigger,
        guard=guard,
        active_stop=active_stop,
        structure_stop=structure_stop,
        confirmed=False,
        profitable=pl_pct > 0,
        trigger_action="放量突破后小幅加仓",
    )
    plan = {
        "entry_price": round(entry, 2),
        "current_price": round(current_price, 2),
        "add_trigger_price": round(trigger, 2),
        "add_guard_price": round(guard, 2),
        "active_stop_price": round(active_stop, 2) if active_stop > 0 else None,
        "structure_stop_price": round(structure_stop, 2) if structure_stop > 0 else None,
        "time_stop_date": time_stop_date,
        "health": health,
        "instruction": instruction,
        "bands": operation_bands(trigger=trigger, guard=guard, active_stop=active_stop, structure_stop=structure_stop),
    }
    plan["decision_snapshot"] = build_position_decision_snapshot(
        current_price=current_price,
        entry_price=entry,
        risk=risk,
        plan=plan,
        entry_date=row.get("entry_date"),
        price_source="paper_cached_price",
        price_updated_at=row.get("updated_at"),
    )
    return plan


def _wind_control_decision(
    curr_price: float,
    risk_levels: Dict[str, Any],
    time_stop: Dict[str, Any] | None,
    decision_snapshot: Dict[str, Any] | None = None,
    entry_price: float = 0.0,
) -> Dict[str, Any]:
    if decision_snapshot:
        action = decision_snapshot.get("action")
        if action == "CLOSE" and decision_snapshot.get("executable"):
            return {"reason": decision_snapshot.get("trigger") or "统一决策快照触发退出", "should_close": True}
        # 分批止盈 / 减仓保护：REDUCE 且可执行（T+1 已过）且当前盈利时，触发部分平仓。
        # 盈亏判定用 entry_price vs curr_price，避免对亏损仓位砍仓（亏损应走止损路径）。
        if action == "REDUCE" and decision_snapshot.get("executable"):
            in_profit = bool(entry_price > 0 and curr_price > entry_price)
            if in_profit:
                return {
                    "reason": decision_snapshot.get("trigger") or "减仓保护",
                    "should_close": False,
                    "should_reduce": True,
                }
            # 亏损中的 REDUCE 降级为预警，不真正减仓
            return {"reason": decision_snapshot.get("trigger") or "", "should_close": False}
        if action in {"CLOSE", "REDUCE", "REVIEW"}:
            return {"reason": decision_snapshot.get("trigger") or "", "should_close": False}
    stop_level = safe_num(risk_levels.get("active_stop_price"))
    if stop_level > 0 and curr_price <= stop_level:
        return {
            "reason": f"触发执行风控价 ¥{stop_level:.2f} ({risk_levels.get('risk_stage') or '风险控制'})",
            "should_close": True,
        }
    if time_stop:
        return {
            "reason": str(time_stop.get("reason") or ""),
            "should_close": bool(time_stop.get("should_close")),
        }
    return {"reason": "", "should_close": False}


def _send_operation_trigger_notification(alerts: List[Dict[str, Any]]) -> None:
    if not alerts:
        return
    title = f"Alpha Vision 实盘价位触发 {len(alerts)} 条"
    lines = []
    for alert in alerts[:8]:
        trigger = alert.get("trigger") or {}
        priority = alert_priority(trigger.get("level"), trigger.get("kind"))
        lines.append(f"{priority['emoji']} [{priority['priority']} {priority['label']}] {alert['name']}({alert['code']}) 现价 {alert['current_price']:.2f}")
        lines.append(f"   └ {trigger.get('action')}")
        if alert.get("instruction"):
            lines.append(f"   └ 指令: {alert['instruction']}")
    if len(alerts) > 8:
        lines.append(f"另有 {len(alerts) - 8} 条触发，请打开实盘持仓查看。")
    send_paper_trade_notification(title, "\n".join(lines))


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

        # 改动 #12：日内亏损熔断。当日已实现亏损超过 daily_loss_limit_pct 时硬阻止新开仓
        # （与上面的"warning 可 force 跳过"不同，熔断是硬限制，不可被 force 绕过）。
        from core.portfolio_risk import evaluate_daily_loss_circuit_breaker
        loss_breaker = evaluate_daily_loss_circuit_breaker(engine)
        if loss_breaker.get("halted"):
            return {
                "status": "halt",
                "detail": loss_breaker.get("message", "日内亏损熔断，暂停新开仓"),
                "daily_loss_pct": loss_breaker.get("daily_loss_pct"),
                "daily_loss_limit_pct": loss_breaker.get("daily_loss_limit_pct"),
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

        planned_price = float(trade.planned_entry_price or trade.price)
        actual_price = float(trade.actual_entry_price or trade.price)
        slippage_pct = (actual_price - planned_price) / planned_price * 100 if planned_price > 0 else 0
        resolved_theme, resolved_rise_logic = _resolve_trade_theme_and_logic(engine, trade, new_industry)
        with engine.connect() as conn:
            result = conn.execute(text('''
                INSERT INTO paper_trading (
                    code, name, entry_price, entry_date, current_price, high_since_entry,
                    status, strategy_type, remark, theme, rise_logic, trade_mode,
                    entry_source, entry_signal_date, entry_reason_snapshot,
                    pa_trade_action, pa_trade_setup, pa_entry_condition, pa_invalidation, pa_risk_pct,
                    logic_status, planned_entry_price, actual_entry_price, entry_slippage_pct,
                    position_pct, shares, capital_used, execution_note, plan_adherence, watchlist_id
                )
                VALUES (
                    :code, :name, :price, :date, :price, :price,
                    'OPEN', :strategy_type, :remark, :theme, :rise_logic, :trade_mode,
                    :entry_source, CAST(:entry_signal_date AS DATE), :entry_reason_snapshot,
                    :pa_trade_action, :pa_trade_setup, :pa_entry_condition, :pa_invalidation, :pa_risk_pct,
                    'UNVERIFIED', :planned_entry_price, :actual_entry_price, :entry_slippage_pct,
                    :position_pct, :shares, :capital_used, :execution_note, :plan_adherence, :watchlist_id
                )
                ON CONFLICT (code, entry_date) DO NOTHING
                RETURNING id
            '''), {
                "code": trade.code,
                "name": trade.name,
                "price": trade.price,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "strategy_type": trade.strategy_type,
                "remark": trade.remark,
                "theme": resolved_theme,
                "rise_logic": resolved_rise_logic,
                "trade_mode": trade.trade_mode,
                "entry_source": trade.entry_source or "manual_current_price",
                "entry_signal_date": trade.entry_signal_date or datetime.now().strftime("%Y-%m-%d"),
                "entry_reason_snapshot": trade.entry_reason_snapshot or trade.remark,
                "pa_trade_action": trade.pa_trade_action,
                "pa_trade_setup": trade.pa_trade_setup,
                "pa_entry_condition": trade.pa_entry_condition,
                "pa_invalidation": trade.pa_invalidation,
                "pa_risk_pct": trade.pa_risk_pct,
                "planned_entry_price": planned_price,
                "actual_entry_price": actual_price,
                "entry_slippage_pct": slippage_pct,
                "position_pct": trade.position_pct,
                "shares": trade.shares,
                "capital_used": trade.capital_used,
                "execution_note": trade.execution_note,
                "plan_adherence": trade.plan_adherence,
                "watchlist_id": trade.watchlist_id,
            })
            trade_id = result.scalar()
            conn.commit()
        if trade_id is None:
            return {
                "status": "error",
                "detail": "该股票今天已有拟合实盘记录，请勿重复加入。",
            }
        record_lifecycle_event(
            "PAPER_OPENED",
            source=trade.entry_source or "manual_current_price",
            code=trade.code,
            name=trade.name,
            watchlist_id=trade.watchlist_id,
            trade_id=trade_id,
            strategy_type=trade.strategy_type,
            theme=resolved_theme,
            payload={"planned_price": planned_price, "actual_price": actual_price, "slippage_pct": round(slippage_pct, 3)},
        )

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
        body += (
            f"\n操作指令：>{tp_price:.2f}: 分批止盈/不追加；"
            f"<{stop_price:.2f}: 固定止损复核"
        )
        send_paper_trade_notification(title, body)

        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error adding paper trade: {e}")
        return {"status": "error", "detail": "Internal server error"}


@router.get("/plans")
def get_open_trade_plans() -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return {"items": []}
    try:
        df = pd.read_sql(text("SELECT * FROM paper_trading WHERE status = 'OPEN' ORDER BY trade_mode DESC, entry_date DESC"), engine)
        if df.empty:
            return {"items": []}
        items = []
        for _, row in df.iterrows():
            entry = safe_float(row.get("entry_price"))
            current = safe_float(row.get("current_price"), entry)
            high = track_high_since_entry(
                entry,
                safe_float(row.get("high_since_entry"), entry),
                current,
                current,
                row.get("entry_date"),
            )
            risk = compute_paper_risk_levels(entry, high, current, _local_price_action_summary(engine, str(row.get("code") or "")))
            plan = _build_trade_plan(row.to_dict(), current, high, risk)
            items.append({
                "id": int(row["id"]),
                "code": row["code"],
                "name": row["name"],
                "trade_mode": row.get("trade_mode") or "SIMULATED",
                "strategy_type": row.get("strategy_type"),
                "plan": plan,
            })
        return {"items": items}
    except Exception as exc:
        logger.error(f"Open trade plans error: {exc}")
        return {"items": [], "error": str(exc)}


@router.post("/check-operation-triggers")
def check_operation_triggers(notify: bool = True, trade_mode: str = "REAL") -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return {"status": "error", "alerts": []}
    try:
        if trade_mode.upper() == "ALL":
            df = pd.read_sql(text("SELECT * FROM paper_trading WHERE status = 'OPEN' ORDER BY trade_mode DESC, entry_date DESC"), engine)
        else:
            df = pd.read_sql(
                text("SELECT * FROM paper_trading WHERE status = 'OPEN' AND trade_mode = :trade_mode ORDER BY entry_date DESC"),
                engine,
                params={"trade_mode": trade_mode.upper()},
            )
        if df.empty:
            return {"status": "success", "alerts": [], "checked": 0, "notification": False}

        snapshot_map: Dict[str, Dict[str, float]] = {}
        try:
            snapshot = get_market_snapshot()
            if not snapshot.empty:
                for _, row in snapshot.iterrows():
                    code = str(row.get("code") or "")
                    price = safe_float(row.get("price"))
                    if code and price > 0:
                        snapshot_map[code] = {
                            "price": price,
                            "high": safe_float(row.get("high"), price) or price,
                        }
        except Exception as exc:
            logger.warning(f"Operation trigger snapshot unavailable: {exc}")
        if not snapshot_map:
            logger.warning("Operation trigger Bark skipped: live market snapshot unavailable.")
            return {
                "status": "success",
                "checked": int(len(df)),
                "alerts": [],
                "notification": False,
                "reason": "live_snapshot_unavailable",
            }

        alerts: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            code = str(row.get("code") or "")
            entry = safe_float(row.get("entry_price"))
            live = snapshot_map.get(code, {})
            if not live:
                continue
            current = safe_float(live.get("price"))
            if current <= 0:
                continue
            high = track_high_since_entry(
                entry,
                safe_float(row.get("high_since_entry"), entry),
                current,
                safe_float(live.get("high"), current),
                row.get("entry_date"),
            )
            risk = compute_paper_risk_levels(entry, high, current, _local_price_action_summary(engine, code))
            plan = _build_trade_plan(row.to_dict(), current, high, risk)
            trigger = evaluate_operation_trigger(current, plan)
            if not trigger.get("triggered"):
                continue
            alert = {
                "id": int(row["id"]),
                "code": code,
                "name": row.get("name"),
                "trade_mode": row.get("trade_mode") or "SIMULATED",
                "current_price": round(current, 2),
                "plan": plan,
                "trigger": trigger,
                "priority": alert_priority(trigger.get("level"), trigger.get("kind")),
                "instruction": plan.get("instruction"),
            }
            alerts.append(alert)

        if alerts and notify:
            _send_operation_trigger_notification(alerts)
            try:
                _ensure_trade_journal_table(engine)
                with engine.connect() as conn:
                    conn.execute(text("""
                        INSERT INTO trade_journal_events (
                            event_time, source, code, name, trade_id, advice, action_taken,
                            trigger_price, guard_price, stop_price, result_note
                        ) VALUES (
                            :event_time, 'operation_trigger', :code, :name, :trade_id, :advice, NULL,
                            :trigger_price, :guard_price, :stop_price, :result_note
                        )
                    """), [
                        {
                            "event_time": datetime.now(),
                            "code": alert["code"],
                            "name": alert["name"],
                            "trade_id": alert["id"],
                            "advice": alert["trigger"].get("action"),
                            "trigger_price": alert["plan"].get("add_trigger_price"),
                            "guard_price": alert["plan"].get("add_guard_price"),
                            "stop_price": alert["plan"].get("active_stop_price"),
                            "result_note": alert["instruction"],
                        }
                        for alert in alerts
                    ])
                    conn.commit()
            except Exception as exc:
                logger.warning(f"Failed to journal operation triggers: {exc}")

        return {
            "status": "success",
            "checked": int(len(df)),
            "alerts": alerts,
            "notification": bool(alerts and notify),
        }
    except Exception as exc:
        logger.error(f"Check operation triggers error: {exc}")
        return {"status": "error", "alerts": [], "detail": str(exc)}


@router.post("/pre-trade-check")
def run_pre_trade_check(payload: Dict[str, Any]) -> Dict[str, Any]:
    current = safe_num(payload.get("current_price"))
    plan = payload.get("plan") or {}
    if not plan and payload.get("trade_id"):
        engine = get_db_engine()
        if not engine:
            return {"status": "error", "detail": "Database error"}
        try:
            df = pd.read_sql(text("SELECT * FROM paper_trading WHERE id = :id"), engine, params={"id": int(payload["trade_id"])})
            if df.empty:
                return {"status": "error", "detail": "Trade not found"}
            row = df.iloc[0]
            entry = safe_float(row.get("entry_price"))
            current = current or safe_float(row.get("current_price"), entry)
            high = max(safe_float(row.get("high_since_entry"), entry), current)
            risk = compute_paper_risk_levels(entry, high, current, _local_price_action_summary(engine, str(row.get("code") or "")))
            plan = _build_trade_plan(row.to_dict(), current, high, risk)
        except Exception as exc:
            logger.error(f"Pre-trade plan resolve error: {exc}")
            return {"status": "error", "detail": str(exc)}

    result = pre_trade_check(
        current_price=current,
        plan=plan,
        market_status=str(payload.get("market_status") or ""),
        sector_phase=str(payload.get("sector_phase") or ""),
        volume_confirmed=bool(payload.get("volume_confirmed")),
        close_confirmed=bool(payload.get("close_confirmed")),
        high_open_pct=safe_num(payload.get("high_open_pct")),
        pullback_warning=bool(payload.get("pullback_warning")),
        portfolio_warnings=payload.get("portfolio_warnings") or [],
    )
    return {"status": "success", "check": result}


@router.post("/journal")
def add_trade_journal_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return {"status": "error", "detail": "Database error"}
    try:
        _ensure_trade_journal_table(engine)
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO trade_journal_events (
                    event_time, source, code, name, trade_id, advice, action_taken,
                    trigger_price, guard_price, stop_price, result_note
                ) VALUES (
                    :event_time, :source, :code, :name, :trade_id, :advice, :action_taken,
                    :trigger_price, :guard_price, :stop_price, :result_note
                )
            """), {
                "event_time": datetime.now(),
                "source": payload.get("source") or "manual",
                "code": payload.get("code"),
                "name": payload.get("name"),
                "trade_id": payload.get("trade_id"),
                "advice": payload.get("advice"),
                "action_taken": payload.get("action_taken"),
                "trigger_price": payload.get("trigger_price"),
                "guard_price": payload.get("guard_price"),
                "stop_price": payload.get("stop_price"),
                "result_note": payload.get("result_note"),
            })
            conn.commit()
        return {"status": "success"}
    except Exception as exc:
        logger.error(f"Add trade journal event error: {exc}")
        return {"status": "error", "detail": str(exc)}


@router.get("/journal")
def list_trade_journal_events(limit: int = 100) -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return {"items": []}
    try:
        _ensure_trade_journal_table(engine)
        df = pd.read_sql(
            text("SELECT * FROM trade_journal_events ORDER BY event_time DESC LIMIT :limit"),
            engine,
            params={"limit": max(1, min(int(limit), 500))},
        )
        return {"items": df.where(pd.notna(df), None).to_dict("records")}
    except Exception as exc:
        logger.error(f"List trade journal events error: {exc}")
        return {"items": [], "error": str(exc)}


@router.get("/journal/summary")
def get_trade_journal_summary(days: int = 30) -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return {"summary": {}, "by_source": [], "pending_feedback": []}
    try:
        _ensure_trade_journal_table(engine)
        start_time = datetime.now() - timedelta(days=max(1, min(int(days), 365)))
        df = pd.read_sql(
            text("""
                SELECT *
                FROM trade_journal_events
                WHERE event_time >= :start_time
                ORDER BY event_time DESC
            """),
            engine,
            params={"start_time": start_time},
        )
        if df.empty:
            return {"summary": {"events": 0, "feedback_rate": 0}, "by_source": [], "pending_feedback": []}

        has_action = df["action_taken"].fillna("").astype(str).str.strip().ne("")
        has_result = df["result_note"].fillna("").astype(str).str.strip().ne("")
        by_source = []
        for source, group in df.groupby("source", dropna=False):
            group_has_action = group["action_taken"].fillna("").astype(str).str.strip().ne("")
            by_source.append({
                "source": source or "unknown",
                "events": int(len(group)),
                "feedback_rate": round(float(group_has_action.mean() * 100), 1) if len(group) else 0,
            })
        by_source.sort(key=lambda item: item["events"], reverse=True)
        pending = df.loc[~has_action].head(10)
        return {
            "summary": {
                "events": int(len(df)),
                "feedback_rate": round(float(has_action.mean() * 100), 1),
                "result_note_rate": round(float(has_result.mean() * 100), 1),
                "pending_feedback": int((~has_action).sum()),
            },
            "by_source": by_source,
            "pending_feedback": pending.where(pd.notna(pending), None).to_dict("records"),
        }
    except Exception as exc:
        logger.error(f"Trade journal summary error: {exc}")
        return {"summary": {}, "by_source": [], "pending_feedback": [], "error": str(exc)}


@router.post("/journal/{event_id}/feedback")
def update_trade_journal_feedback(event_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return {"status": "error", "detail": "Database error"}
    try:
        _ensure_trade_journal_table(engine)
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE trade_journal_events
                SET action_taken = :action_taken,
                    result_note = :result_note
                WHERE id = :id
            """), {
                "id": int(event_id),
                "action_taken": payload.get("action_taken"),
                "result_note": payload.get("result_note"),
            })
            conn.commit()
        return {"status": "success"}
    except Exception as exc:
        logger.error(f"Update trade journal feedback error: {exc}")
        return {"status": "error", "detail": str(exc)}


@router.get("/list")
def list_paper_trades(refresh: bool = False) -> Dict[str, Any]:
    """List all paper trades with live P&L tracking"""
    engine = get_db_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="拟合实盘数据库暂不可用，已保留前端最后成功数据")
    try:
        df = pd.read_sql(text("SELECT * FROM paper_trading ORDER BY entry_date DESC"), engine)
        if df.empty:
            return _empty_paper_trade_response()

        # --- 获取最新价格 ---
        codes = df['code'].unique().tolist()
        open_codes = df[df.get('status', 'OPEN') == 'OPEN']['code'].unique().tolist()

        # 方法1: OPEN 持仓优先从实时快照获取盘中价格
        open_rows = df[df.get('status', 'OPEN') == 'OPEN']
        price_map = {
            str(row['code']): safe_float(row.get('current_price'), safe_float(row.get('entry_price')))
            for _, row in open_rows.iterrows()
        }
        high_map = {
            str(row['code']): safe_float(row.get('high_since_entry'), price_map.get(str(row['code']), 0))
            for _, row in open_rows.iterrows()
        }
        if open_codes:
            try:
                snapshot = get_market_snapshot() if refresh else get_cached_data("market_snapshot", 300)
                if snapshot is None and not refresh:
                    snapshot = get_stale_cache("market_snapshot")
                if snapshot is not None and not snapshot.empty:
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
                high_since_entry = track_high_since_entry(
                    entry_price,
                    high_since_entry,
                    current_price,
                    current_high,
                    entry_date,
                )
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
                "close_price": round(_optional_float(row.get('close_price')), 2) if _optional_float(row.get('close_price')) is not None else None,
                "close_date": _optional_str(row.get('close_date')),
                "close_source": _optional_value(row.get('close_source')),
                "closed_by": _optional_value(row.get('closed_by')),
                "updated_at": _optional_str(row.get('updated_at')),
                "remark": _optional_value(row.get('remark')),
                "theme": _optional_value(row.get('theme')) or industry,
                "rise_logic": _optional_value(row.get('rise_logic')) or _optional_value(row.get('remark')) or _optional_value(row.get('entry_reason_snapshot')),
                "logic_status": _optional_value(row.get('logic_status')) or "UNVERIFIED",
                "planned_entry_price": safe_float(row.get('planned_entry_price'), entry_price),
                "actual_entry_price": safe_float(row.get('actual_entry_price'), entry_price),
                "entry_slippage_pct": safe_float(row.get('entry_slippage_pct')),
                "position_pct": _optional_float(row.get('position_pct')),
                "shares": _optional_int(row.get('shares')),
                "capital_used": _optional_float(row.get('capital_used')),
                "execution_note": _optional_value(row.get('execution_note')),
                "plan_adherence": _optional_value(row.get('plan_adherence')) or "UNKNOWN",
                "watchlist_id": _optional_int(row.get('watchlist_id')),
                "trade_mode": _optional_value(row.get('trade_mode')) or 'SIMULATED',
                "entry_source": _optional_value(row.get('entry_source')),
                "entry_signal_date": _optional_str(row.get('entry_signal_date')),
                "entry_reason_snapshot": _optional_value(row.get('entry_reason_snapshot')),
                "pa_trade_action": _optional_value(row.get('pa_trade_action')),
                "pa_trade_setup": _optional_value(row.get('pa_trade_setup')),
                "pa_entry_condition": _optional_value(row.get('pa_entry_condition')),
                "pa_invalidation": _optional_value(row.get('pa_invalidation')),
                "pa_risk_pct": _optional_float(row.get('pa_risk_pct')),
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
                return _empty_mode_stats()
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
        raise HTTPException(status_code=500, detail="拟合实盘查询失败，请查看后端日志") from e


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
        record_lifecycle_event(
            "REAL_CONVERTED",
            source="paper_trade",
            code=code,
            name=name,
            trade_id=id,
            strategy_type=t_map.get("strategy_type"),
            theme=t_map.get("theme"),
            payload={"entry_price": entry_price},
        )

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
                    execution_note = COALESCE(:execution_note, execution_note),
                    logic_status = CASE WHEN :close_price >= entry_price THEN 'CONFIRMED' ELSE 'INVALIDATED' END,
                    logic_last_review_at = :updated_at,
                    updated_at = :updated_at
                WHERE id = :id
            """), {
                "close_price": float(close_price),
                "close_date": datetime.now().strftime("%Y-%m-%d"),
                "execution_note": data.execution_note,
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
        record_lifecycle_event(
            "TRADE_CLOSED",
            source="paper_trade",
            code=code,
            name=name,
            trade_id=id,
            strategy_type=t_map.get("strategy_type"),
            theme=t_map.get("theme"),
            payload={"close_price": float(close_price), "pnl_pct": round(pl_pct, 2)},
        )

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
    2. 时间风控: 按交易日分层预警/复核/确认平仓

    大盘环境用于限制新增仓位和辅助人工判断，不单独触发自动平仓。
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

        closed_count = 0
        warned_real_count = 0
        warned_simulated_count = 0
        alerts = []
        close_updates = []  # 收集批量更新参数
        reduce_updates = []  # 收集分批止盈/减仓的拆行参数
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
            high_since_entry = track_high_since_entry(
                entry_price,
                high_since_entry,
                curr_price,
                curr_high,
                entry_date,
                now,
            )
            pl_pct = (curr_price - entry_price) / entry_price * 100
            
            # --- 风控逻辑判定 (按优先级, 使用统一风控引擎) ---
            pa_summary = _local_price_action_summary(engine, str(code))
            # 把 price action 摘要中的 latest_atr 提取出来，喂给风控引擎做自适应止损，
            # 使实盘 active_stop_price 与回测行为一致（只收紧不放宽）。
            latest_atr = safe_float(pa_summary.get("latest_atr")) or None
            # 弱市（bear/volatile）时进一步收紧已有仓位止损（regime 已在循环外加载）。
            regime_status = regime.get("regime") if isinstance(regime, dict) else None
            risk_levels = compute_paper_risk_levels(entry_price, high_since_entry, curr_price, pa_summary, atr=latest_atr, market_regime=regime_status)
            time_stop = _evaluate_time_stop(
                hold_trading_days,
                pl_pct,
                row.get("strategy_type"),
            )
            plan = _build_trade_plan(row.to_dict(), curr_price, high_since_entry, risk_levels)
            # 已减仓标记：通过 remark 中是否含首笔止盈标记判断，避免重复触发分批止盈。
            existing_remark = str(row.get("remark") or "")
            already_reduced = bool(existing_remark and FIRST_PROFIT_TAKE_MARK in existing_remark)
            decision_snapshot = build_position_decision_snapshot(
                current_price=curr_price,
                entry_price=entry_price,
                risk=risk_levels,
                plan=plan,
                time_stop=time_stop,
                entry_date=entry_date,
                price_source="market_snapshot",
                price_updated_at=now,
                now=now,
                already_reduced=already_reduced,
            )
            decision = _wind_control_decision(curr_price, risk_levels, time_stop, decision_snapshot, entry_price=entry_price)
            reason = decision["reason"]
            should_close = decision["should_close"]
            should_reduce = decision.get("should_reduce", False)

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
                elif should_reduce:
                    # 分批止盈 / 减仓保护：把剩余 shares 减半（最小留 100 股），
                    # 并新增一行 CLOSED 记录已落袋的那部分。剩余仓位继续用移动止损跟踪。
                    current_shares = int(row.get('shares') or 0)
                    if current_shares >= 200:  # 至少 200 股才能拆出有意义的一半
                        reduce_shares = int(current_shares * FIRST_PROFIT_TAKE_RATIO)
                        reduce_shares = (reduce_shares // 100) * 100  # 对齐到整手
                        if reduce_shares >= 100:
                            remaining_shares = current_shares - reduce_shares
                            reduce_updates.append({
                                "orig_id": int(row['id']),
                                "remaining_shares": remaining_shares,
                                "orig_remark": f"{existing_remark}；{FIRST_PROFIT_TAKE_MARK}({close_date_str})".strip("；"),
                                # 新 CLOSED 行复制原行关键字段，shares 为减仓部分
                                "code": str(row['code']),
                                "name": str(row.get('name') or ''),
                                "entry_price": float(entry_price),
                                "entry_date": row['entry_date'],
                                "strategy_type": str(row.get('strategy_type') or ''),
                                "trade_mode": str(trade_mode),
                                "close_price": float(curr_price),
                                "close_date": close_date_str,
                                "remark": f"{FIRST_PROFIT_TAKE_MARK}：{reason}",
                                "close_shares": reduce_shares,
                                "u": datetime.now(),
                            })
                            alerts.append(f"{row['name']}({code}) 模拟仓分批止盈减仓 {reduce_shares} 股（剩余 {remaining_shares} 股）: {reason}")
                            mode_label = "模拟仓分批止盈"
                            action_line = f"模拟仓已减仓 {reduce_shares} 股锁定利润，剩余 {remaining_shares} 股继续持有。"
                        else:
                            # shares 过少无法整手拆分，降级为预警
                            warned_simulated_count += 1
                            alerts.append(f"{row['name']}({code}) 模拟仓减仓预警（份额不足整手）: {reason}")
                            mode_label = "模拟仓风控预警"
                            action_line = "模拟仓份额不足以整手减仓，请人工复核。"
                    else:
                        warned_simulated_count += 1
                        alerts.append(f"{row['name']}({code}) 模拟仓减仓预警（份额不足）: {reason}")
                        mode_label = "模拟仓风控预警"
                        action_line = "模拟仓份额不足 200 股，无法自动减仓，请人工复核。"
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

        # 批量执行分批止盈/减仓的拆行：原行 shares 减半并保留 OPEN，新插入一行 CLOSED 记录已落袋部分。
        if reduce_updates:
            with engine.connect() as conn:
                for r in reduce_updates:
                    # 1) 原行减半并标记，保留 OPEN 让剩余仓位继续被移动止损跟踪
                    conn.execute(text("""
                        UPDATE paper_trading
                        SET shares = :remaining_shares,
                            remark = :orig_remark,
                            updated_at = :u
                        WHERE id = :orig_id
                    """), {
                        "remaining_shares": r["remaining_shares"],
                        "orig_remark": r["orig_remark"],
                        "u": r["u"],
                        "orig_id": r["orig_id"],
                    })
                    # 2) 新增一行 CLOSED 记录已减仓落袋的部分（复用现有列，无 schema 变更）
                    conn.execute(text("""
                        INSERT INTO paper_trading
                            (code, name, entry_price, entry_date, current_price, high_since_entry,
                             status, close_price, close_date, close_source, closed_by,
                             strategy_type, trade_mode, shares, remark, updated_at)
                        VALUES
                            (:code, :name, :entry_price, :entry_date, :close_price, :entry_price,
                             'CLOSED', :close_price, :close_date, 'wind_control_partial', 'system',
                             :strategy_type, :trade_mode, :close_shares, :remark, :u)
                    """), {
                        "code": r["code"],
                        "name": r["name"],
                        "entry_price": r["entry_price"],
                        "entry_date": r["entry_date"],
                        "close_price": r["close_price"],
                        "close_date": r["close_date"],
                        "strategy_type": r["strategy_type"],
                        "trade_mode": r["trade_mode"],
                        "close_shares": r["close_shares"],
                        "remark": r["remark"],
                        "u": r["u"],
                    })
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
        df = pd.read_sql(text("""
            SELECT p.*, COALESCE(s.industry, '未知') AS industry
            FROM paper_trading p
            LEFT JOIN stock_basic s ON s.code = p.code
            ORDER BY p.entry_date ASC
        """), engine)
        if df.empty:
            return {
                "risk_metrics": {},
                "attribution": {"by_industry": [], "by_strategy": []},
                "rolling_performance": []
            }
        
        trades = df.to_dict('records')
        for trade in trades:
            entry_price = safe_float(trade.get("entry_price"))
            exit_price = safe_float(
                trade.get("close_price") if trade.get("status") == "CLOSED" else trade.get("current_price"),
                entry_price,
            )
            trade["pl_pct"] = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0.0
        
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
