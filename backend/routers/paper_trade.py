"""
Paper trading router - simulated trading management endpoints.

Extracted from api.py.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from typing import Dict, Any, List
from datetime import datetime, timedelta
import json
import pandas as pd

from core.logging_config import logger
from core.db import get_db_engine, validate_stock_code, save_failure_sample, load_from_db, get_setting
from core.data import get_cached_data, get_market_snapshot, get_sector_map, get_stale_cache, is_snapshot_stale
from core.indicators import calculate_indicators
from core.price_action import analyze_price_action
from core.analytics import run_monte_carlo, calculate_rolling_performance, calculate_risk_metrics, calculate_pnl_attribution
from core.risk_engine import compute_paper_risk_levels, compute_paper_risk_levels_with_context, safe_float, track_high_since_entry
from core.risk_constants import (
    FIXED_STOP_LOSS_PCT, FIXED_STOP_LOSS_RATIO,
    TAKE_PROFIT_PCT, TAKE_PROFIT_RATIO,
    TIME_STOP_WARNING_DAYS, TIME_STOP_REVIEW_DAYS,
    TIME_STOP_FORCE_DAYS, TIME_STOP_REVIEW_LOSS_PCT,
    TIME_STOP_PROFIT_EXEMPT_PCT, TIME_STOP_REVIEW_REDUCE_RATIO,
    FIRST_PROFIT_TAKE_MARK, FIRST_PROFIT_TAKE_RATIO, FIRST_PROFIT_TAKE_PCT,
    EARLY_WARN_MILD_PCT, EARLY_WARN_MODERATE_PCT, EARLY_WARN_TIER_COOLDOWN_DAYS,
    WIND_CONTROL_INTERVAL_URGENT_MINUTES, URGENT_STOP_BUFFER_PCT,
    SIGNAL_REVERSE_SELL_ENABLED,
    MA_STRATEGY_TAKE_PROFIT_PCT,
)
from core.portfolio_risk import evaluate_portfolio_risk_budget, evaluate_floating_loss_circuit_breaker
from core.operation_plan import alert_priority, build_position_decision_snapshot, evaluate_operation_trigger, operation_bands, position_health_score, pre_trade_check, price_instruction, safe_num
from core.audit_log import record_lifecycle_event
from schemas.paper_trade import PaperTradeCreate, PaperTradeClose

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])


def _normalize_signal_sources(value: Any) -> List[str]:
    if not value:
        return []
    raw = value if isinstance(value, (list, tuple, set)) else str(value).replace(",", "+").split("+")
    return [source for source in ("ma", "zp") if source in {str(item).strip().lower() for item in raw}]


def _tv_position_state(sources: List[str], same_day_dual: bool = False) -> tuple[str | None, float | None]:
    if set(sources) == {"ma", "zp"}:
        return ("A", 1.0) if same_day_dual else ("B", 0.6)
    if sources == ["ma"]:
        return "B", 0.6
    if sources == ["zp"]:
        return "C", 0.25
    return None, None


def _resolve_trade_signal_sources(engine, trade: PaperTradeCreate) -> List[str]:
    direct = _normalize_signal_sources(trade.signal_sources)
    if direct or trade.strategy_type != "tv_dual":
        return direct
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT price_action_detail
            FROM scan_history
            WHERE code = :code AND strategy_type = 'tv_dual'
            ORDER BY date DESC
            LIMIT 1
        """), {"code": trade.code}).fetchone()
    detail = row[0] if row else {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except (TypeError, ValueError):
            detail = {}
    return _normalize_signal_sources(detail.get("signal_sources") if isinstance(detail, dict) else None)


def _upgrade_open_tv_position(
    engine,
    trade: PaperTradeCreate,
    incoming: List[str],
) -> Dict[str, Any] | None:
    if not incoming:
        return None
    with engine.begin() as conn:
        existing = conn.execute(text("""
            SELECT id, code, name, strategy_type, theme, watchlist_id,
                   entry_signal_date, signal_sources, execution_tier, risk_unit
            FROM paper_trading
            WHERE code = :code AND status = 'OPEN' AND trade_mode = :trade_mode
            ORDER BY entry_date DESC, id DESC
            LIMIT 1
        """), {"code": trade.code, "trade_mode": trade.trade_mode}).mappings().first()
        if not existing:
            return None
        current = _normalize_signal_sources(existing.get("signal_sources"))
        merged = _normalize_signal_sources(current + incoming)
        if set(merged) == set(current):
            return {
                "status": "error",
                "detail": "该股票已有相同信号来源的持仓，请勿重复开仓。",
            }
        existing_date = str(existing.get("entry_signal_date") or "")[:10]
        incoming_date = str(trade.entry_signal_date or datetime.now().date())[:10]
        tier, risk_unit = _tv_position_state(
            merged,
            same_day_dual=bool(existing_date and existing_date == incoming_date),
        )
        upgraded_at = datetime.now()
        conn.execute(text("""
            UPDATE paper_trading
            SET signal_sources = :sources,
                execution_tier = :tier,
                risk_unit = :risk_unit,
                source_upgraded_at = :upgraded_at,
                updated_at = :upgraded_at
            WHERE id = :trade_id
        """), {
            "sources": "+".join(merged),
            "tier": tier,
            "risk_unit": risk_unit,
            "upgraded_at": upgraded_at,
            "trade_id": existing["id"],
        })

    record_lifecycle_event(
        "POSITION_SIGNAL_UPGRADED",
        source=trade.entry_source or "signal_confirmation",
        code=trade.code,
        name=trade.name,
        watchlist_id=existing.get("watchlist_id"),
        trade_id=existing["id"],
        strategy_type=existing.get("strategy_type") or trade.strategy_type,
        theme=existing.get("theme"),
        payload={
            "previous_sources": current,
            "incoming_sources": incoming,
            "signal_sources": merged,
            "execution_tier": tier,
            "risk_unit": risk_unit,
            "kept_original_entry": True,
        },
    )
    send_paper_trade_notification(
        f"【持仓信号升级】{trade.name} ({trade.code})",
        f"原持仓不重复加仓；信号来源升级为 {'+'.join(merged).upper()}，执行层级 {tier}，风险单位 {risk_unit:g}。",
    )
    return {
        "status": "upgraded",
        "trade_id": int(existing["id"]),
        "signal_sources": merged,
        "execution_tier": tier,
        "risk_unit": risk_unit,
    }


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


def _real_trade_execution_warnings(trade: PaperTradeCreate) -> List[str]:
    if trade.trade_mode != "REAL":
        return []
    warnings: List[str] = []
    if trade.planned_entry_price is None:
        warnings.append("实盘买入缺少计划价，无法复盘是否按确认价执行")
    if not trade.entry_signal_date:
        warnings.append("实盘买入缺少系统信号日期，无法匹配Bark/扫描样本")
    if trade.plan_adherence == "UNKNOWN":
        warnings.append("实盘买入未标记计划遵守情况，执行胜率无法归因")
    if not trade.entry_source:
        warnings.append("实盘买入缺少入口来源，无法区分Bark、观察池或手工追单")
    return warnings


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
    # 改动 B4：原逻辑 pl_pct > 0 就完全豁免，导致 +0.5% 横盘 20 天的僵尸仓无人管。
    # 改为 pl_pct > TIME_STOP_PROFIT_EXEMPT_PCT(2%) 才豁免——2%以下都算"未达预期"。
    # 这释放被僵尸仓占用的仓位配额，避免上班族"周末复盘发现全是微盈横盘票"。
    if pl_pct > TIME_STOP_PROFIT_EXEMPT_PCT:
        return None

    policy = _time_stop_policy(strategy_type)
    if hold_trading_days >= policy["force_days"]:
        return {
            "reason": (
                f"时间止损确认: 持仓 {hold_trading_days} 个交易日仍未达预期盈利 "
                f"({pl_pct:.1f}%)，建议平仓或移出实盘持仓"
            ),
            "should_close": True,
            "severity": "close",
        }
    if hold_trading_days >= policy["review_days"]:
        # 改动 B4：微盈震荡仓（0 < pl_pct <= 2%）在 review 档触发减仓，而非只预警。
        # 亏损中（pl_pct <= TIME_STOP_REVIEW_LOSS_PCT）维持原"建议减仓/退出"逻辑。
        if 0 < pl_pct <= TIME_STOP_PROFIT_EXEMPT_PCT:
            action = f"微盈横盘({pl_pct:.1f}%)，建议减仓 {int(TIME_STOP_REVIEW_REDUCE_RATIO*100)}% 释放仓位"
            return {
                "reason": (
                    f"时间止损复核: {policy['label']}策略持仓 {hold_trading_days} 个交易日仅微盈 "
                    f"({pl_pct:.1f}%)，{action}"
                ),
                "should_close": False,
                "should_reduce": True,
                "reduce_ratio": TIME_STOP_REVIEW_REDUCE_RATIO,
                "severity": "review",
            }
        action = "亏损加重，建议减仓/退出候选" if pl_pct <= TIME_STOP_REVIEW_LOSS_PCT else "建议人工复核"
        return {
            "reason": (
                f"时间止损复核: {policy['label']}策略持仓 {hold_trading_days} 个交易日未达预期 "
                f"({pl_pct:.1f}%)，{action}"
            ),
            "should_close": False,
            "severity": "review",
        }
    if hold_trading_days >= policy["warning_days"]:
        return {
            "reason": (
                f"时间止损预警: {policy['label']}策略持仓 {hold_trading_days} 个交易日未达预期 "
                f"({pl_pct:.1f}%)，暂不自动平仓"
            ),
            "should_close": False,
            "severity": "warning",
        }
    return None


# 改动(上班族Bark)：分级预警去重状态。key=f"{code}:tier{级别}"，value=日期字符串。
# 每级每天最多推一次；进程重启后重置（sentinel 是常驻线程，可接受）。
_tier_alert_sent: Dict[str, str] = {}

# 改动 B2：实盘止损告警升级状态。key=f"{code}:real_stop"，value=当日已推送次数。
# 首次触发普通推送；后续每次 tick 仍未平仓 → 升级为"未处理·第N次"。
# 进程重启后重置（同 _tier_alert_sent 模式，sentinel 常驻可接受）。
_real_stop_alert_state: Dict[str, int] = {}
_REAL_STOP_ALERT_DATE: Dict[str, str] = {}  # 记录推送日期，跨日重置


def _tier_early_warning(*, code: str, name: str, trade_mode: str,
                        entry_price: float, curr_price: float, pl_pct: float,
                        regime_desc: str, snapshot=None) -> None:
    """持仓浮亏分级预警（-3% 轻度 / -5% 中度）。

    仅对亏损持仓（pl_pct < 0）触发；每级每天最多推一次（去重防 30 分钟循环刷屏）。
    -9% 紧急预警由原有止损逻辑处理，此处不重复。
    snapshot: curr_price 来源的同一份快照（用其 attrs 标注新鲜度，避免重新抓取导致
              价格与时间戳不同源）。
    """
    if pl_pct >= 0 or entry_price <= 0:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    # 判定级别：-5% 以下为中度，-3% 以下为轻度
    if pl_pct <= EARLY_WARN_MODERATE_PCT:
        tier, label, advice = 2, "🟡中度预警", "浮亏较大，建议下班后评估是否减仓保护"
    elif pl_pct <= EARLY_WARN_MILD_PCT:
        tier, label, advice = 1, "⚠️轻度预警", "持仓开始恶化，请留意后续走势"
    else:
        return
    dedupe_key = f"{code}:tier{tier}"
    last = _tier_alert_sent.get(dedupe_key)
    if last == today:
        return  # 同级今日已推，防刷屏
    _tier_alert_sent[dedupe_key] = today
    try:
        title = f"【{label}】{name} ({code})"
        body = (
            f"交易模式：{'🔴 实盘' if trade_mode == 'REAL' else '🔵 模拟盘'}\n"
            f"建议动作：{advice}\n"
            f"买入价格：¥{entry_price:.2f}\n"
            f"当前价格：¥{curr_price:.2f}\n"
            f"当前浮亏：{pl_pct:+.2f}%\n"
            f"大盘状态：{regime_desc}"
        )
        # P1：标注行情新鲜度，用 curr_price 同源的 snapshot attrs（避免重新抓取导致价格与时间戳矛盾）
        try:
            from core.data import format_freshness
            body = body + "\n" + format_freshness(snapshot)
        except Exception:
            pass
        send_paper_trade_notification(title, body)
    except Exception as exc:
        logger.warning(f"分级预警推送异常({code},tier{tier}): {exc}")


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
    signal_sources = _normalize_signal_sources(row.get("signal_sources"))
    if signal_sources:
        active_stop = entry * (1 + FIXED_STOP_LOSS_PCT / 100)
        structure_stop = active_stop
    else:
        active_stop = safe_num(risk.get("active_stop_price") or risk.get("stop_price"))
        structure_stop = safe_num(risk.get("structure_stop_price") or risk.get("initial_stop_price"))
    trigger = max(current_price * 1.02, high_since_entry)
    guard = max(active_stop, trigger * 0.985)
    entry_date = pd.to_datetime(row.get("entry_date") or datetime.now())
    policy = _time_stop_policy(row.get("strategy_type"))
    time_stop_date = None if signal_sources else (
        entry_date + pd.tseries.offsets.BDay(policy["force_days"])
    ).date().isoformat()
    stop_buffer = (current_price - active_stop) / current_price * 100 if current_price > 0 and active_stop > 0 else 0
    pl_pct = (current_price - entry) / entry * 100 if entry > 0 else 0
    health = position_health_score(pl_pct=pl_pct, stop_buffer_pct=stop_buffer)
    if signal_sources:
        policies = []
        if "ma" in signal_sources:
            policies.append("MA按+15%目标或收盘跌破EMA20后次日开盘退出")
        if "zp" in signal_sources:
            policies.append("ZP按short，或盈利+15%后EMA20破位，次日开盘退出")
        instruction = f"固定{FIXED_STOP_LOSS_PCT:g}%保护止损；{'；'.join(policies)}"
    else:
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
        "signal_sources": signal_sources,
        "health": health,
        "instruction": instruction,
        "bands": operation_bands(trigger=trigger, guard=guard, active_stop=active_stop, structure_stop=structure_stop),
    }
    if signal_sources:
        plan["decision_snapshot"] = {
            "action": "HOLD",
            "label": "TV来源策略持有",
            "trigger": instruction,
            "executable": False,
            "t1_locked": False,
        }
    else:
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
        # 改动 B4：time_stop 可能携带 should_reduce（微盈横盘减仓），需透传
        result = {
            "reason": str(time_stop.get("reason") or ""),
            "should_close": bool(time_stop.get("should_close")),
        }
        if time_stop.get("should_reduce"):
            result["should_reduce"] = True
        return result
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
        execution_warnings = _real_trade_execution_warnings(trade)
        if execution_warnings and not trade.force:
            return {
                "status": "warning",
                "detail": "实盘执行记录不完整，请补齐后再加入；若确认为人工强制记录，可 force=true。",
                "warnings": execution_warnings,
                "execution_quality": "INCOMPLETE",
            }

        signal_sources = _resolve_trade_signal_sources(engine, trade)
        upgraded = _upgrade_open_tv_position(engine, trade, signal_sources)
        if upgraded is not None:
            return upgraded

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
        execution_tier, risk_unit = _tv_position_state(
            signal_sources,
            same_day_dual=set(signal_sources) == {"ma", "zp"},
        )
        resolved_theme, resolved_rise_logic = _resolve_trade_theme_and_logic(engine, trade, new_industry)
        with engine.connect() as conn:
            result = conn.execute(text('''
                INSERT INTO paper_trading (
                    code, name, entry_price, entry_date, current_price, high_since_entry,
                    status, strategy_type, remark, theme, rise_logic, trade_mode,
                    entry_source, entry_signal_date, entry_reason_snapshot,
                    signal_sources, execution_tier, risk_unit,
                    pa_trade_action, pa_trade_setup, pa_entry_condition, pa_invalidation, pa_risk_pct,
                    logic_status, planned_entry_price, actual_entry_price, entry_slippage_pct,
                    position_pct, shares, capital_used, execution_note, plan_adherence, watchlist_id
                )
                VALUES (
                    :code, :name, :price, :date, :price, :price,
                    'OPEN', :strategy_type, :remark, :theme, :rise_logic, :trade_mode,
                    :entry_source, CAST(:entry_signal_date AS DATE), :entry_reason_snapshot,
                    :signal_sources, :execution_tier, :risk_unit,
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
                "signal_sources": "+".join(signal_sources) or None,
                "execution_tier": execution_tier,
                "risk_unit": risk_unit,
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
            payload={
                "planned_price": planned_price,
                "actual_price": actual_price,
                "slippage_pct": round(slippage_pct, 3),
                "execution_warnings": execution_warnings,
            },
        )

        # 发送 Bark 实时推送 — 根据交易模式区分标题（使用统一风控常量）
        stop_price = round(trade.price * FIXED_STOP_LOSS_RATIO, 2)
        tp_price = round(trade.price * TAKE_PROFIT_RATIO, 2)
        mode_label = "实盘买入" if trade.trade_mode == "REAL" else "模拟仓买入"
        title = f"【{mode_label}】{trade.name} ({trade.code})"
        body = (
            f"交易模式：{'🔴 实盘' if trade.trade_mode == 'REAL' else '🔵 模拟盘'}\n"
            f"入场价格：¥{trade.price:.2f}\n"
            f"计划价格：¥{planned_price:.2f}｜实际价格：¥{actual_price:.2f}｜滑点：{slippage_pct:+.2f}%\n"
            f"计划遵守：{trade.plan_adherence}\n"
            f"价格来源：{trade.entry_source or 'manual_current_price'}\n"
            f"信号日期：{trade.entry_signal_date or datetime.now().strftime('%Y-%m-%d')}\n"
            f"固定止损：¥{stop_price:.2f} ({FIXED_STOP_LOSS_PCT}%)\n"
            f"首笔止盈：+{FIRST_PROFIT_TAKE_PCT:.0f}%减半仓，剩余移动止盈跟踪\n"
            f"目标上限：¥{tp_price:.2f} (+{TAKE_PROFIT_PCT}%,剩余仓位的乐观上限)\n"
            f"交易备注：{trade.remark or '无'}"
        )
        body += (
            f"\n操作指令：>{tp_price:.2f}: 分批止盈/不追加；"
            f"<{stop_price:.2f}: 固定止损复核"
        )
        send_paper_trade_notification(title, body)

        return {
            "status": "success",
            "signal_sources": signal_sources,
            "execution_tier": execution_tier,
            "risk_unit": risk_unit,
        }
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
            risk = compute_paper_risk_levels_with_context(entry, high, current, _local_price_action_summary(engine, str(row.get("code") or "")), str(row.get("code") or ""))
            plan = _build_trade_plan(row.to_dict(), current, high, risk)
            items.append({
                "id": int(row["id"]),
                "code": row["code"],
                "name": row["name"],
                "trade_mode": row.get("trade_mode") or "SIMULATED",
                "strategy_type": row.get("strategy_type"),
                "signal_sources": _normalize_signal_sources(row.get("signal_sources")),
                "execution_tier": row.get("execution_tier"),
                "risk_unit": _optional_value(row.get("risk_unit")),
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
            if not snapshot.empty and not is_snapshot_stale(snapshot):
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
        price_updates: List[Dict[str, Any]] = []
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
            price_updates.append({
                "id": int(row["id"]),
                "current_price": round(current, 2),
                "high_since_entry": round(high, 2),
                "updated_at": datetime.now(),
            })
            risk = compute_paper_risk_levels_with_context(entry, high, current, _local_price_action_summary(engine, code), code)
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

        if price_updates:
            try:
                with engine.connect() as conn:
                    conn.execute(text("""
                        UPDATE paper_trading
                        SET current_price = :current_price,
                            high_since_entry = :high_since_entry,
                            updated_at = :updated_at
                        WHERE id = :id
                    """), price_updates)
                    conn.commit()
            except Exception as exc:
                logger.warning(f"Failed to refresh paper trading live prices: {exc}")

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
            risk = compute_paper_risk_levels_with_context(entry, high, current, _local_price_action_summary(engine, str(row.get("code") or "")), str(row.get("code") or ""))
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
        # 修复 BUG-B: 统计指标(胜率/盈亏比/回撤)只用 CLOSED（已实现），
        # 不混入 OPEN（浮亏/浮盈会随盘中波动导致胜率不稳定）。
        closed_for_stats = [t for t in trades if t.get('status') == 'CLOSED']
        wins = sum(1 for t in closed_for_stats if t['pl_pct'] > 0)
        losses = sum(1 for t in closed_for_stats if t['pl_pct'] < 0)
        flat = sum(1 for t in closed_for_stats if t['pl_pct'] == 0)
        total = len(closed_for_stats)
        avg_pl = sum(t['pl_pct'] for t in closed_for_stats) / total if total > 0 else 0
        avg_hold = sum(t['hold_days'] for t in closed_for_stats) / total if total > 0 else 0

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

        # 改动 #13：最大回撤改为复利权益曲线口径（修复旧实现用 pl_pct 累加和的数学错误）。
        # 保留本接口的"正值百分比"输出契约。复用 analytics 规范 helper。
        from core.analytics import compute_equity_curve_drawdown, compute_profit_factor
        sorted_for_dd = sorted(closed_for_stats, key=lambda x: x['entry_date'])
        max_drawdown, _curve = compute_equity_curve_drawdown([t['pl_pct'] for t in sorted_for_dd])
        # 盈亏比：改调规范 helper（毛额口径，cap 9.9 保留本接口契约）
        profit_factor = compute_profit_factor([t['pl_pct'] for t in closed_for_stats], cap=9.9)

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
    """平仓或部分卖出。

    不传 close_shares 时保持原有全平行为；传入小于当前持仓的 close_shares
    时，只更新剩余股数，并用 lifecycle_events 记录已卖出部分。
    """
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
            current_shares = int(t_map.get("shares") or 0)
            close_shares = data.close_shares
            if close_shares is not None and current_shares <= 0:
                raise HTTPException(status_code=400, detail="当前记录没有持仓股数，不能部分卖出")
            if close_shares is not None and close_shares > current_shares:
                raise HTTPException(status_code=400, detail="卖出股数不能大于当前持仓")
            is_partial_close = close_shares is not None and close_shares < current_shares

            if is_partial_close:
                remaining_shares = current_shares - int(close_shares)
                existing_note = t_map.get("execution_note") or t_map.get("remark") or ""
                partial_note = (
                    data.execution_note
                    or f"手动部分卖出{close_shares}股，卖出价{float(close_price):.2f}，剩余{remaining_shares}股继续持有。"
                )
                merged_note = f"{existing_note}；{partial_note}".strip("；")
                conn.execute(text("""
                    UPDATE paper_trading
                    SET shares = :remaining_shares,
                        capital_used = :capital_used,
                        current_price = :close_price,
                        execution_note = :execution_note,
                        plan_adherence = 'PARTIAL_TAKE_PROFIT',
                        logic_last_review_at = :updated_at,
                        updated_at = :updated_at
                    WHERE id = :id
                """), {
                    "remaining_shares": remaining_shares,
                    "capital_used": round(entry_price * remaining_shares, 2),
                    "close_price": float(close_price),
                    "execution_note": merged_note,
                    "updated_at": datetime.now(),
                    "id": id,
                })
                conn.commit()

                pl_pct = 0.0 if entry_price <= 0 else (float(close_price) - entry_price) / entry_price * 100
                realized_amount = (float(close_price) - entry_price) * int(close_shares)
                mode_label = "实盘减仓" if trade_mode == "REAL" else "模拟仓减仓"
                send_paper_trade_notification(
                    f"【{mode_label}】{name} ({code})",
                    (
                        f"交易模式：{'🔴 实盘' if trade_mode == 'REAL' else '🔵 模拟盘'}\n"
                        f"买入价格：¥{entry_price:.2f}\n"
                        f"卖出价格：¥{float(close_price):.2f}\n"
                        f"卖出股数：{int(close_shares)}股\n"
                        f"剩余股数：{remaining_shares}股\n"
                        f"本次盈亏：{pl_pct:+.2f}%"
                    ),
                )
                record_lifecycle_event(
                    "PARTIAL_SELL",
                    source="paper_trade",
                    code=code,
                    name=name,
                    trade_id=id,
                    strategy_type=t_map.get("strategy_type"),
                    theme=t_map.get("theme"),
                    payload={
                        "sell_shares": int(close_shares),
                        "remaining_shares": remaining_shares,
                        "close_price": float(close_price),
                        "pnl_pct": round(pl_pct, 2),
                        "realized_pnl_amount": round(realized_amount, 2),
                    },
                )
                return {
                    "status": "success",
                    "partial": True,
                    "closed_shares": int(close_shares),
                    "remaining_shares": remaining_shares,
                    "pnl_pct": round(pl_pct, 2),
                    "realized_pnl_amount": round(realized_amount, 2),
                }

            conn.execute(text("""
                UPDATE paper_trading
                SET close_price = :close_price,
                    close_date = :close_date,
                    status = 'CLOSED',
                    close_source = 'manual',
                    closed_by = 'user',
                    execution_note = COALESCE(:execution_note, execution_note),
                    -- 修复#3: logic_status 改为更准确的三态（盈利/亏损/待验证）
                    logic_status = CASE
                        WHEN :close_price >= entry_price * 1.05 THEN 'CONFIRMED'
                        WHEN :close_price < entry_price * 0.97 THEN 'INVALIDATED'
                        ELSE 'PENDING'
                    END,
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
        # 修复 BUG-E: entry_price<=0 时跳过（避免 ZeroDivisionError，与 run_wind_control 的 BUG2 修复一致）
        if entry_price <= 0:
            pl_pct = 0.0
        else:
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

    # 混合退出策略开关：默认 SIGNAL_REVERSE_SELL_ENABLED(False)，DB key 可运行时覆盖
    signal_reverse_enabled = str(
        get_setting("signal_reverse_sell_enabled", str(SIGNAL_REVERSE_SELL_ENABLED), engine=engine)
    ).strip().lower() in {"1", "true", "yes", "on"}
    
    try:
        df = pd.read_sql(text("SELECT * FROM paper_trading WHERE status = :status"), engine, params={"status": "OPEN"})
        if df.empty: return {"status": "success", "closed_count": 0}
        
        from core.data import get_market_snapshot
        snapshot = get_market_snapshot()
        if snapshot.empty: return {"status": "error", "detail": "市场行情不可用"}
        if is_snapshot_stale(snapshot):
            return {"status": "error", "detail": "实时行情已过期，风控暂停，避免使用过时行情。"}
        
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
        pending_exit_count = 0
        snapshot_trade_date = pd.to_datetime(
            snapshot.attrs.get("data_date") or close_date_str,
            errors="coerce",
        )
        current_trade_date = (
            snapshot_trade_date.date()
            if not pd.isna(snapshot_trade_date)
            else datetime.now().date()
        )
        # 改动 B1：累计本次风控的最小 stop_buffer，供 sentinel 动态调整间隔
        min_stop_buffer_pct = None

        # 改动 B5：组合浮亏熔断。系统性下跌日持仓全部浮亏但未触发止损时，
        # 旧熔断（已实现亏损）不触发。此处检测浮亏超限并推送告警。
        floating_cb = evaluate_floating_loss_circuit_breaker(engine, snapshot)
        if floating_cb.get("halted"):
            alerts.append(f"🔴【组合浮亏熔断】{floating_cb['message']}，暂停加仓")

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
            curr_open = safe_float(match.iloc[0].get('open'), curr_price)
            curr_high = safe_float(match.iloc[0].get('high'), curr_price)
            # 改动 B1：读取当日最低价。急跌行情下 30 分钟 tick 可能错过盘中击穿
            # 止损线又反弹的场景（上班族完全无感）。后续止损判定用 effective_price
            # = min(curr_price, curr_low)，只要盘中任一时刻击穿过就触发。
            curr_low = safe_float(match.iloc[0].get('low'), curr_price)
            high_since_entry = track_high_since_entry(
                entry_price,
                high_since_entry,
                curr_price,
                curr_high,
                entry_date,
                now,
            )
            # 修复 BUG2：entry_price=0/None 时跳过（避免 ZeroDivisionError 中断整个风控循环）
            if entry_price <= 0:
                logger.warning(f"{code} entry_price 异常({entry_price})，跳过风控")
                continue
            pl_pct = (curr_price - entry_price) / entry_price * 100

            # 改动(上班族Bark)：分级预警。原逻辑只在跌破 -9% 止损线才推送，导致
            # -3%~-9% 的恶化过程静默（6/16 -4.4% 零推送）。此处补三级早期预警：
            # -3% 轻度（留意）→ -5% 中度（建议减仓）。每级每天最多推一次（防刷屏）。
            _wt_trade_mode = row.get('trade_mode', 'SIMULATED') or 'SIMULATED'
            _tier_early_warning(
                code=code, name=row.get('name') or code, trade_mode=_wt_trade_mode,
                entry_price=entry_price, curr_price=curr_price, pl_pct=pl_pct,
                regime_desc=regime.get('desc', 'N/A') if isinstance(regime, dict) else 'N/A',
                snapshot=snapshot,
            )

            # --- 风控逻辑判定 ---
            # 有明确 MA/ZP 来源的 TV 持仓只执行用户声明的退出规则；旧持仓继续走通用风控。
            signal_sources = _normalize_signal_sources(row.get("signal_sources"))
            forced_exec_price = None
            pending_reason = str(row.get("pending_exit_reason") or "").strip()
            pending_date = pd.to_datetime(row.get("pending_exit_signal_date"), errors="coerce")
            pending_due = bool(
                signal_sources
                and pending_reason
                and not pd.isna(pending_date)
                and pending_date.date() < current_trade_date
            )
            pa_summary: Dict[str, Any] = {}
            if signal_sources:
                fixed_stop = entry_price * (1 + FIXED_STOP_LOSS_PCT / 100)
                risk_levels = {
                    "active_stop_price": fixed_stop,
                    "stop_price": fixed_stop,
                    "initial_stop_price": fixed_stop,
                    "structure_stop_price": fixed_stop,
                    "risk_stage": "TV固定-9%保护",
                    "max_pl_pct": round((high_since_entry - entry_price) / entry_price * 100, 2),
                }
                time_stop = None
                plan = _build_trade_plan(row.to_dict(), curr_price, high_since_entry, risk_levels)
                if pending_due:
                    decision = {
                        "reason": f"{pending_reason}（下一交易日开盘执行）",
                        "should_close": True,
                    }
                    forced_exec_price = curr_open
                else:
                    if curr_price <= fixed_stop:
                        tv_signals = [{
                            "level": "critical",
                            "reason": f"触发TV策略固定保护止损 ({FIXED_STOP_LOSS_PCT:g}%)",
                            "suggestion": "立即退出",
                        }]
                    elif (
                        "ma" in signal_sources
                        and high_since_entry >= entry_price * (1 + MA_STRATEGY_TAKE_PROFIT_PCT / 100)
                    ):
                        tv_signals = [{
                            "level": "critical",
                            "reason": f"均线策略达到+{MA_STRATEGY_TAKE_PROFIT_PCT:g}%目标",
                            "suggestion": "立即退出",
                        }]
                    else:
                        tv_signals = []
                    query = text("""
                        SELECT date as "日期", close as "收盘", open as "开盘",
                               high as "最高", low as "最低", vol as "成交量"
                        FROM daily_k
                        WHERE code = :code
                        ORDER BY date DESC LIMIT 260
                    """)
                    with engine.connect() as conn:
                        tv_history = pd.read_sql(query, conn, params={"code": code}).sort_values("日期")
                    if not tv_signals and len(tv_history) >= 20:
                        from core.strategy import evaluate_exit_signals
                        tv_labeled = calculate_indicators(tv_history, current_price=curr_price)
                        tv_signals = evaluate_exit_signals(
                            tv_labeled,
                            entry_price,
                            high_since_entry,
                            code=str(code),
                            signal_sources=signal_sources,
                            close_confirmed=now.hour >= 15,
                        )
                    signal = tv_signals[0] if tv_signals else None
                    if signal and "下一交易日开盘退出" in str(signal.get("suggestion") or ""):
                        scheduled_reason = str(signal.get("reason") or "TV策略收盘卖点确认")
                        with engine.begin() as conn:
                            conn.execute(text("""
                                UPDATE paper_trading
                                SET pending_exit_reason = :reason,
                                    pending_exit_signal_date = :signal_date,
                                    updated_at = :updated_at
                                WHERE id = :trade_id AND status = 'OPEN'
                            """), {
                                "reason": scheduled_reason,
                                "signal_date": current_trade_date,
                                "updated_at": now,
                                "trade_id": int(row["id"]),
                            })
                        pending_exit_count += 1
                        trade_mode = row.get('trade_mode', 'SIMULATED') or 'SIMULATED'
                        if trade_mode == "REAL":
                            warned_real_count += 1
                        else:
                            warned_simulated_count += 1
                        alerts.append(f"{row['name']}({code}) 已记录次日开盘退出: {scheduled_reason}")
                        send_paper_trade_notification(
                            f"【TV卖点确认·次日开盘】{row['name']} ({code})",
                            f"{scheduled_reason}\n本次不按收盘价卖出；已登记下一交易日开盘退出。",
                        )
                        record_lifecycle_event(
                            "POSITION_EXIT_SCHEDULED",
                            source="wind_control",
                            code=str(code),
                            name=str(row.get("name") or ""),
                            trade_id=int(row["id"]),
                            strategy_type=str(row.get("strategy_type") or ""),
                            payload={
                                "signal_sources": signal_sources,
                                "reason": scheduled_reason,
                                "signal_date": current_trade_date.isoformat(),
                            },
                        )
                        continue
                    decision = {
                        "reason": str(signal.get("reason") or "") if signal else "",
                        "should_close": bool(signal),
                    }
            else:
                pa_summary = _local_price_action_summary(engine, str(code))
                latest_atr = safe_float(pa_summary.get("latest_atr")) or None
                from core.market_regime import map_status_to_regime
                regime_status = map_status_to_regime(regime.get("status")) if isinstance(regime, dict) else None
                risk_levels = compute_paper_risk_levels(
                    entry_price,
                    high_since_entry,
                    curr_price,
                    pa_summary,
                    atr=latest_atr,
                    market_regime=regime_status,
                )
                time_stop = _evaluate_time_stop(
                    hold_trading_days,
                    pl_pct,
                    row.get("strategy_type"),
                )
                plan = _build_trade_plan(row.to_dict(), curr_price, high_since_entry, risk_levels)
            # 改动 B1：跟踪本次循环的最小 stop_buffer，用于 sentinel 动态间隔
            _buf = safe_num(plan.get("health", {}).get("stop_buffer_pct")) if isinstance(plan.get("health"), dict) else safe_num(plan.get("stop_buffer_pct"))
            _active_stop_buf = safe_num(risk_levels.get("active_stop_price"))
            if _active_stop_buf > 0 and curr_price > 0:
                _buf = (curr_price - _active_stop_buf) / curr_price * 100
            if _buf is not None:
                min_stop_buffer_pct = _buf if min_stop_buffer_pct is None else min(min_stop_buffer_pct, _buf)
            # 已减仓标记：通过 remark 中是否含首笔止盈标记判断，避免重复触发分批止盈。
            existing_remark = str(row.get("remark") or "")
            already_reduced = bool(existing_remark and FIRST_PROFIT_TAKE_MARK in existing_remark)
            # 改动 B3：提取强势股豁免所需数据。
            # close_position: 从 pa_summary 提取（若 price_action 未暴露则用 0，不豁免）
            # sector_phase: 持仓记录中存储的板块阶段（开仓时写入）
            _close_pos = safe_float(pa_summary.get("last_close_position") or pa_summary.get("close_position"))
            _sector_phase = str(row.get("sector_phase") or "")
            _pa_regime = str(pa_summary.get("price_action_regime") or "")
            if not signal_sources:
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
                    sector_phase=_sector_phase,
                    close_position=_close_pos,
                    pa_regime=_pa_regime,
                    signal_reverse_enabled=signal_reverse_enabled,
                )
                decision = _wind_control_decision(
                    curr_price,
                    risk_levels,
                    time_stop,
                    decision_snapshot,
                    entry_price=entry_price,
                )
            reason = decision["reason"]
            should_close = decision["should_close"]
            should_reduce = decision.get("should_reduce", False)

            # 改动 B1：盘中急跌感知。若当日最低价(curr_low)击穿了执行止损线，但
            # 最新价(curr_price)因反弹未触发 → 仍然视为止损触发（不能被反弹掩盖）。
            # 这对上班族尤其关键：30 分钟 tick 可能在反弹后才采样，但盘中击穿已
            # 经发生，必须如实推送并按击穿价成交（保守口径）。
            _stop_level = safe_num(risk_levels.get("active_stop_price"))
            _pierced_by_low = (
                not should_close
                and _stop_level > 0
                and curr_low < curr_price          # 确实有下影线（非平盘）
                and curr_low <= _stop_level         # 盘中击穿了止损线
            )
            if _pierced_by_low:
                should_close = True
                reason = (
                    f"盘中击穿止损线 ¥{_stop_level:.2f}（最低 ¥{curr_low:.2f}），"
                    f"按击穿价保守成交。{risk_levels.get('risk_stage') or '风险控制'}"
                )
                decision["reason"] = reason
                decision["should_close"] = True

            if reason:
                # 生成更详细的智能备注
                remark = f"{reason}。卖出时大盘状态：{regime.get('desc', 'N/A')}。"
                # 发送 Bark 风控平仓推送 — 根据交易模式区分
                trade_mode = row.get('trade_mode', 'SIMULATED') or 'SIMULATED'
                # 改动 B1：平仓成交价。若盘中击穿止损线，按击穿价(当日最低)保守成交，
                # 让模拟盘 PnL 与实盘真实滑点一致（回测引擎已用 gap-through-stop 建模）。
                exec_price = (
                    forced_exec_price
                    if forced_exec_price is not None
                    else min(curr_price, curr_low) if _pierced_by_low
                    else curr_price
                )
                if trade_mode == "REAL":
                    warned_real_count += 1
                    # 改动 B2：实盘告警升级。首次普通推送，后续每次 tick（~30分钟）仍未
                    # 平仓 → 升级为"未处理·第N次"，让上班族意识到紧迫性。
                    _today_key = close_date_str
                    _alert_key = f"{code}:real_stop"
                    # 跨日重置
                    if _REAL_STOP_ALERT_DATE.get(_alert_key) != _today_key:
                        _REAL_STOP_ALERT_DATE[_alert_key] = _today_key
                        _real_stop_alert_state[_alert_key] = 0
                    _real_stop_alert_state[_alert_key] = _real_stop_alert_state.get(_alert_key, 0) + 1
                    _alert_n = _real_stop_alert_state[_alert_key]
                    if _alert_n == 1:
                        alerts.append(f"{row['name']}({code}) 实盘风控预警: {reason}")
                        mode_label = "实盘风控预警"
                        action_line = "系统不会自动平仓，请人工确认是否卖出。"
                    else:
                        alerts.append(f"🔴【未处理·第{_alert_n}次】{row['name']}({code}) 实盘止损仍未处理: {reason}")
                        mode_label = f"实盘风控预警·第{_alert_n}次"
                        action_line = f"⚠️ 已第{_alert_n}次提醒！请立即在券商App处理，或点击下方链接记录平仓。"
                elif should_close:
                    close_updates.append({
                        "p": exec_price,
                        "d": close_date_str,
                        "r": remark,
                        "u": datetime.now(),
                        "id": int(row['id']),
                        # 改动 #17：携带失败样本写入所需元数据
                        "fs_code": str(code),
                        "fs_name": str(row.get('name') or ''),
                        "fs_entry": float(entry_price),
                        "fs_strategy": str(row.get('strategy_type') or ''),
                        "fs_pl_pct": float((exec_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0,
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
                                # 改动 B1：减仓成交价同样用保守口径（盘中击穿时按 low）
                                "code": str(row['code']),
                                "name": str(row.get('name') or ''),
                                "entry_price": float(entry_price),
                                "entry_date": row['entry_date'],
                                "strategy_type": str(row.get('strategy_type') or ''),
                                "trade_mode": str(trade_mode),
                                "close_price": float(exec_price),
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
                # 改动 B4：推送加入持仓天数和时间止损倒计时，解决上班族"忘记持仓时间"痛点
                _ts_date = plan.get("time_stop_date") if isinstance(plan, dict) else None
                _ts_line = f"时间止损倒计时：{_ts_date}\n" if _ts_date else ""
                body = (
                    f"交易模式：{'🔴 实盘' if trade_mode == 'REAL' else '🔵 模拟盘'}\n"
                    f"处理方式：{action_line}\n"
                    f"风控原因：{reason}\n"
                    f"买入价格：¥{entry_price:.2f}\n"
                    f"当前价格：¥{curr_price:.2f}\n"
                    f"当前收益：{pl_pct:+.2f}%\n"
                    f"持仓天数：{hold_trading_days} 个交易日\n"
                    f"{_ts_line}"
                    f"大盘状态：{regime.get('desc', 'N/A')}"
                )
                # P1：标注行情新鲜度。snapshot 与 curr_price 同源（均为外层 1367 行快照），无矛盾。
                try:
                    from core.data import format_freshness
                    body = body + "\n" + format_freshness(snapshot)
                except Exception:
                    pass
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
                        pending_exit_reason = NULL,
                        pending_exit_signal_date = NULL,
                        updated_at = :u
                    WHERE id = :id
                """), close_updates)
                conn.commit()

        # 改动 #17：自动止损平仓也写 failure_samples，丰富语料（之前只手动亏损平仓写）。
        # 仅记录亏损平仓（pl_pct<0），盈利平仓不算失败。
        try:
            for cu in close_updates:
                if cu.get("fs_pl_pct", 0) < 0:
                    save_failure_sample({
                        "code": cu.get("fs_code"),
                        "name": cu.get("fs_name"),
                        "sample_date": cu.get("d"),
                        "strategy_type": cu.get("fs_strategy"),
                        "failure_type": "wind_control_stop",
                        "reason": "风控自动止损平仓",
                        "pnl_pct": round(cu.get("fs_pl_pct", 0), 2),
                        "source": "wind_control_auto",
                    }, engine)
        except Exception as fs_err:
            logger.warning(f"failure_samples 写入异常（不阻断风控）: {fs_err}")

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

        # 改动 B1：判断大盘是否处于弱市（供 sentinel 缩短风控间隔）
        from core.market_regime import map_status_to_regime
        _regime_urgent = map_status_to_regime(regime.get("status") if isinstance(regime, dict) else None) in ("bear", "volatile")

        return {
            "status": "success",
            "closed_count": closed_count,
            "warned_real_count": warned_real_count,
            "warned_simulated_count": warned_simulated_count,
            "pending_exit_count": pending_exit_count,
            "alerts": alerts,
            # 改动 B1：暴露 urgency 状态给 sentinel 动态间隔
            "min_stop_buffer_pct": round(min_stop_buffer_pct, 2) if min_stop_buffer_pct is not None else None,
            "regime_urgent": _regime_urgent,
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
