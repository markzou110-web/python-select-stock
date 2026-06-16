from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from typing import Dict, Any
from datetime import datetime
import asyncio
import pandas as pd

from core.db import get_db_engine, validate_stock_code
from core.logging_config import logger
from core.notifier import notifier
from core.risk_constants import FIXED_STOP_LOSS_RATIO, TAKE_PROFIT_RATIO
from core.operation_plan import watch_exit_decision, watch_instruction
from core.audit_log import record_lifecycle_event

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


def _watch_decision(item: Dict[str, Any]) -> Dict[str, str]:
    target_hit = bool(item.get("target_hit"))
    stop_hit = bool(item.get("stop_hit"))
    pa_action = item.get("pa_trade_action") or ""
    pl_pct = float(item.get("pl_pct") or 0)
    current_price = float(item.get("current_price") or 0)
    target_price = item.get("target_price")
    stop_price = item.get("stop_price")

    if stop_hit or pa_action == "AVOID":
        return {
            "decision": "INVALIDATE",
            "action": "失效移除：已触发失效价或 Brooks 回避信号",
        }
    if target_hit:
        return {
            "decision": "PROMOTE",
            "action": "转可交易：尾盘确认未破失效线，可转入拟合实盘",
        }
    if target_price and current_price > 0:
        distance = (float(target_price) - current_price) / current_price * 100
        if 0 <= distance <= 2:
            return {"decision": "NEAR_TRIGGER", "action": "接近触发：只等放量站稳，不提前追"}
    if pa_action == "READY":
        return {"decision": "READY_WAIT", "action": "结构就绪：仍需站稳触发价，未触发不买"}
    if stop_price and current_price > 0:
        buffer_pct = (current_price - float(stop_price)) / current_price * 100
        if 0 <= buffer_pct < 2:
            return {"decision": "RISK", "action": "贴近失效：不转入，跌破后归档"}
    if pl_pct >= 5:
        return {"decision": "WATCH_PULLBACK", "action": "已有涨幅：不追，等回踩确认"}
    return {"decision": "KEEP_WATCH", "action": "继续观察：等待回踩/放量站稳或再次入选"}


def _logic_status(decision: str) -> str:
    if decision in {"INVALIDATE", "AUTO_PRUNE"}:
        return "INVALIDATED"
    if decision in {"PROMOTE", "TRIGGERED"}:
        return "CONFIRMED"
    if decision in {"NEAR_TRIGGER", "READY_WAIT"}:
        return "STRENGTHENING"
    if decision in {"RISK", "WATCH_PULLBACK"}:
        return "WEAKENING"
    return "UNVERIFIED"


def _latest_prices(engine, codes):
    if not codes:
        return {}
    placeholders = ",".join([f":code_{i}" for i in range(len(codes))])
    params = {f"code_{i}": c for i, c in enumerate(codes)}
    try:
        df = pd.read_sql(text(f"""
            SELECT DISTINCT ON (code) code, close AS latest_price, date AS latest_date
            FROM daily_k
            WHERE code IN ({placeholders})
            ORDER BY code, date DESC
        """), engine, params=params)
        return {
            row["code"]: {"price": float(row["latest_price"]), "date": str(row["latest_date"])}
            for _, row in df.iterrows()
        }
    except Exception as exc:
        logger.warning(f"Watchlist latest price fetch failed: {exc}")
        return {}


def _send_trigger_notification(alerts) -> Dict[str, bool]:
    if not alerts:
        return {}

    title = f"Alpha Vision 观察池触发 {len(alerts)} 条"
    lines = []
    for alert in alerts[:8]:
        reasons = "、".join(alert["reasons"])
        instruction = f"\n   └ 指令: {alert.get('instruction')}" if alert.get("instruction") else ""
        lines.append(
            f"{alert['name']}({alert['code']}) {reasons}: "
            f"现价 {alert['current_price']}，观察收益 {alert['pl_pct']}%{instruction}"
        )
    if len(alerts) > 8:
        lines.append(f"另有 {len(alerts) - 8} 条触发记录，请打开观察池查看。")
    body = "\n".join(lines)

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(notifier.send(title, body, channels=["bark"]))
            return {"bark": True}
        return asyncio.run(notifier.send(title, body, channels=["bark"]))
    except Exception as exc:
        logger.error(f"Watchlist trigger notification error: {exc}")
        return {"bark": False}


def _refresh_items_with_snapshot(items):
    if not items:
        return []
    try:
        from core.data import get_market_snapshot

        snapshot = get_market_snapshot()
        if snapshot is None or snapshot.empty:
            return items
        snapshot_map = snapshot.set_index("code")["price"].to_dict()
        from core.data import get_market_regime
        from core.decision_layer import build_market_decision_context, load_market_cycle_history
        engine = get_db_engine()
        market_context = build_market_decision_context(
            snapshot,
            get_market_regime(),
            load_market_cycle_history(engine),
        )
    except Exception as exc:
        logger.warning(f"Watchlist status snapshot refresh failed: {exc}")
        return items

    refreshed = []
    for original in items:
        item = dict(original)
        item.update(market_context)
        current_price = snapshot_map.get(item["code"])
        if not current_price:
            refreshed.append(item)
            continue

        current_price = round(float(current_price), 2)
        watch_price = float(item.get("watch_price") or current_price)
        target_price = item.get("target_price")
        stop_price = item.get("stop_price")
        item["current_price"] = current_price
        item["pl_pct"] = round((current_price - watch_price) / watch_price * 100, 2) if watch_price > 0 else 0
        item["target_hit"] = target_price is not None and current_price >= float(target_price)
        item["stop_hit"] = stop_price is not None and current_price <= float(stop_price)

        decision = _watch_decision(item)
        instruction = watch_instruction(item)
        item["computed_decision"] = decision["decision"]
        item["computed_action"] = decision["action"]
        item["operation_instruction"] = instruction["instruction"]
        item["trigger_price"] = instruction["trigger_price"]
        item["guard_price"] = instruction["guard_price"]
        refreshed.append(item)
    return refreshed


def _build_watchlist_status_body(items, slot: str) -> str:
    is_morning = slot == "morning"
    first = items[0] if items else {}
    market_blocked = first.get("market_sentiment_stage") == "RETREAT"
    lines = [
        (
            f"市场：{first.get('market_sentiment_label', '--')} "
            f"{first.get('market_sentiment_score', '--')}分 | "
            f"总仓上限 {first.get('portfolio_position_cap_pct', '--')}%"
        ),
        "性质：观察池主动汇报，不是无条件买入指令。",
        (
            "晨间纪律：不抢开盘；超过触发价且放量站稳后再复核，跌破保护价立即取消。"
            if is_morning
            else "尾盘纪律：仅确认全天承接有效的标的；冲高回落、跌破保护价或未站稳触发价均不买。"
        ),
        "",
    ]
    for item in items[:10]:
        decision = item.get("computed_decision") or "KEEP_WATCH"
        action = item.get("computed_action") or "继续观察"
        if market_blocked:
            action = f"市场退潮，禁止新增仓位；原计划：{action}"
        trigger = item.get("trigger_price") or item.get("target_price")
        guard = item.get("guard_price") or item.get("stop_price")
        lines.append(
            f"【{decision}】{item.get('name', '')}({item.get('code', '')}) "
            f"现价 {item.get('current_price', '--')} ({float(item.get('pl_pct') or 0):+.2f}%)"
        )
        lines.append(f"  结论：{action}")
        lines.append(f"  价格指令：站稳 >{trigger if trigger else '--'} 再复核；跌破 <{guard if guard else '--'} 取消观察")
        if market_blocked:
            lines.append("  操作：今日只观察，不买入；等待市场情绪至少修复后重新评估")
        elif item.get("operation_instruction"):
            lines.append(f"  操作：{item['operation_instruction']}")
        lines.append("")
    if len(items) > 10:
        lines.append(f"另有 {len(items) - 10} 只观察票，请打开观察池查看。")
    return "\n".join(lines).rstrip()


def send_watchlist_status_report(slot: str) -> Dict[str, Any]:
    """Push a proactive morning plan or late-session conclusion for the formal watchlist."""
    if slot not in {"morning", "late"}:
        return {"bark": False, "count": 0, "reason": "unsupported slot"}

    payload = list_watchlist(status="WATCHING")
    items = _refresh_items_with_snapshot(payload.get("items", []))
    if not items:
        return {"bark": False, "count": 0, "reason": "empty watchlist"}

    title = "Alpha Vision 观察池晨间计划" if slot == "morning" else "Alpha Vision 观察池尾盘结论"
    body = _build_watchlist_status_body(items, slot)
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(notifier.send(title, body, channels=["bark"]))
            return {"bark": True, "count": len(items), "body": body}
        result = asyncio.run(notifier.send(title, body, channels=["bark"]))
        return {"bark": bool(result), "count": len(items), "body": body}
    except Exception as exc:
        logger.error(f"Watchlist status notification error: {exc}")
        return {"bark": False, "count": len(items), "body": body, "detail": str(exc)}


@router.get("/list")
def list_watchlist(status: str = "WATCHING") -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return {"items": [], "stats": {}}

    try:
        if status == "ALL":
            df = pd.read_sql(text("SELECT * FROM watchlist ORDER BY updated_at DESC, created_at DESC"), engine)
        else:
            df = pd.read_sql(
                text("SELECT * FROM watchlist WHERE status = :status ORDER BY updated_at DESC, created_at DESC"),
                engine,
                params={"status": status},
            )
        if df.empty:
            return {"items": [], "stats": {"total": 0, "triggered": 0, "avg_pl_pct": 0}}

        price_map = _latest_prices(engine, df["code"].unique().tolist())
        items = []
        triggered = 0
        pl_values = []
        for _, row in df.iterrows():
            latest = price_map.get(row["code"], {})
            current_price = latest.get("price", float(row["watch_price"] or 0))
            watch_price = float(row["watch_price"] or current_price or 0)
            pl_pct = ((current_price - watch_price) / watch_price * 100) if watch_price > 0 else 0
            target_price = row.get("target_price")
            stop_price = row.get("stop_price")
            target_hit = target_price is not None and current_price >= float(target_price)
            stop_hit = stop_price is not None and current_price <= float(stop_price)
            if target_hit or stop_hit:
                triggered += 1
            pl_values.append(pl_pct)
            item = {
                "id": int(row["id"]),
                "code": row["code"],
                "name": row["name"],
                "industry": row.get("industry") or "未知",
                "source": row.get("source") or "manual",
                "strategy_type": row.get("strategy_type") or "squeeze",
                "watch_price": round(watch_price, 2),
                "current_price": round(current_price, 2),
                "pl_pct": round(pl_pct, 2),
                "target_price": round(float(target_price), 2) if target_price is not None else None,
                "stop_price": round(float(stop_price), 2) if stop_price is not None else None,
                "target_hit": target_hit,
                "stop_hit": stop_hit,
                "status": row.get("status") or "WATCHING",
                "reason": row.get("reason") or "",
                "theme": row.get("theme") or row.get("industry") or "",
                "rise_logic": row.get("rise_logic") or row.get("reason") or "",
                "logic_status": row.get("logic_status") or "UNVERIFIED",
                "invalidation": row.get("invalidation") or "",
                "pa_trade_action": row.get("pa_trade_action") or "",
                "pa_trade_setup": row.get("pa_trade_setup") or "",
                "pa_entry_condition": row.get("pa_entry_condition") or "",
                "pa_invalidation": row.get("pa_invalidation") or "",
                "pa_risk_pct": round(float(row.get("pa_risk_pct")), 2) if row.get("pa_risk_pct") is not None else None,
                "last_review_date": str(row.get("last_review_date")) if row.get("last_review_date") is not None else None,
                "watch_decision": row.get("watch_decision") or "",
                "watch_action": row.get("watch_action") or "",
                "exit_reason": row.get("exit_reason") or "",
                "created_at": row["created_at"].isoformat() if hasattr(row.get("created_at"), "isoformat") else str(row.get("created_at") or ""),
                "latest_date": latest.get("date"),
            }
            decision = _watch_decision(item)
            instruction = watch_instruction(item)
            exit_check = watch_exit_decision({**item, "computed_decision": decision["decision"], "computed_action": decision["action"]})
            item["computed_decision"] = decision["decision"]
            item["computed_action"] = decision["action"]
            item["logic_status"] = _logic_status(decision["decision"])
            item["operation_instruction"] = instruction["instruction"]
            item["trigger_price"] = instruction["trigger_price"]
            item["guard_price"] = instruction["guard_price"]
            item["auto_exit_reason"] = exit_check["reason"] if exit_check["should_exit"] else ""
            if not item["watch_decision"]:
                item["watch_decision"] = decision["decision"]
                item["watch_action"] = decision["action"]
            items.append(item)

        return {
            "items": items,
            "stats": {
                "total": len(items),
                "triggered": triggered,
                "avg_pl_pct": round(sum(pl_values) / len(pl_values), 2) if pl_values else 0,
            },
        }
    except Exception as exc:
        logger.error(f"List watchlist error: {exc}")
        return {"items": [], "stats": {}}


@router.post("/refresh-decisions")
def refresh_watchlist_decisions() -> Dict[str, Any]:
    payload = list_watchlist(status="WATCHING")
    items = payload.get("items", [])
    engine = get_db_engine()
    if not engine:
        return {"status": "error", "updated": 0}

    updated = 0
    try:
        with engine.connect() as conn:
            for item in items:
                decision = item.get("computed_decision") or item.get("watch_decision") or "KEEP_WATCH"
                action = item.get("computed_action") or item.get("watch_action") or ""
                status = "WATCHING"
                exit_reason = item.get("exit_reason") or ""
                if decision == "INVALIDATE":
                    status = "INVALIDATED"
                    exit_reason = action
                conn.execute(text("""
                    UPDATE watchlist
                    SET watch_decision = :decision,
                        watch_action = :action,
                        exit_reason = :exit_reason,
                        last_review_date = :review_date,
                        status = :status,
                        logic_status = :logic_status,
                        logic_last_review_at = :updated_at,
                        updated_at = :updated_at
                    WHERE id = :id
                """), {
                    "decision": decision,
                    "action": action,
                    "exit_reason": exit_reason,
                    "review_date": datetime.now().strftime("%Y-%m-%d"),
                    "status": status,
                    "logic_status": _logic_status(decision),
                    "updated_at": datetime.now(),
                    "id": item["id"],
                })
                updated += 1
            conn.commit()
        return {"status": "success", "updated": updated}
    except Exception as exc:
        logger.error(f"Refresh watchlist decisions error: {exc}")
        return {"status": "error", "updated": updated, "detail": str(exc)}


@router.post("/auto-prune")
def auto_prune_watchlist(max_watch_days: int = 15) -> Dict[str, Any]:
    payload = list_watchlist(status="WATCHING")
    items = payload.get("items", [])
    engine = get_db_engine()
    if not engine:
        return {"status": "error", "updated": 0, "items": []}

    prune_items = []
    for item in items:
        exit_check = watch_exit_decision(item, max_watch_days=max(3, int(max_watch_days)))
        if exit_check["should_exit"]:
            prune_items.append({**item, "exit_reason": exit_check["reason"]})

    if not prune_items:
        return {"status": "success", "updated": 0, "items": []}

    try:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE watchlist
                SET status = 'INVALIDATED',
                    watch_decision = 'AUTO_PRUNE',
                    watch_action = :action,
                    exit_reason = :reason,
                    last_review_date = :review_date,
                    updated_at = :updated_at
                WHERE id = :id
            """), [
                {
                    "id": item["id"],
                    "action": "自动淘汰：不再占用观察池",
                    "reason": item["exit_reason"],
                    "review_date": datetime.now().strftime("%Y-%m-%d"),
                    "updated_at": datetime.now(),
                }
                for item in prune_items
            ])
            conn.commit()
        return {
            "status": "success",
            "updated": len(prune_items),
            "items": [
                {"id": item["id"], "code": item["code"], "name": item["name"], "exit_reason": item["exit_reason"]}
                for item in prune_items
            ],
        }
    except Exception as exc:
        logger.error(f"Auto prune watchlist error: {exc}")
        return {"status": "error", "updated": 0, "items": [], "detail": str(exc)}


@router.post("/check-triggers")
def check_watchlist_triggers(notify: bool = True) -> Dict[str, Any]:
    payload = list_watchlist(status="WATCHING")
    alerts = []

    for item in payload.get("items", []):
        reasons = []
        if item.get("target_hit"):
            reasons.append("Brooks入场触发" if item.get("pa_trade_action") else "触达目标价")
        if item.get("stop_hit"):
            reasons.append("触发Brooks失效位" if item.get("pa_trade_action") else "触发失效价")
        if not reasons:
            continue
        alerts.append({
            "id": item["id"],
            "code": item["code"],
            "name": item["name"],
            "industry": item.get("industry") or "未知",
            "current_price": item.get("current_price"),
            "watch_price": item.get("watch_price"),
            "target_price": item.get("target_price"),
            "stop_price": item.get("stop_price"),
            "pl_pct": item.get("pl_pct"),
            "reasons": reasons,
            "instruction": item.get("operation_instruction") or "",
        })

    notification = _send_trigger_notification(alerts) if notify and alerts else {}
    if alerts:
        try:
            engine = get_db_engine()
            if engine:
                updates = []
                for alert in alerts:
                    is_stop = any("失效" in reason for reason in alert.get("reasons", []))
                    updates.append({
                        "id": alert["id"],
                        "status": "INVALIDATED" if is_stop else "TRIGGERED",
                        "decision": "INVALIDATE" if is_stop else "TRIGGERED",
                        "action": "触发失效价，移出观察池" if is_stop else "已触发目标价，等待尾盘确认是否转实盘",
                        "review_date": datetime.now().strftime("%Y-%m-%d"),
                        "logic_status": "INVALIDATED" if is_stop else "CONFIRMED",
                        "updated_at": datetime.now(),
                    })
                with engine.connect() as conn:
                    conn.execute(text("""
                        UPDATE watchlist
                        SET status = :status,
                            watch_decision = :decision,
                            watch_action = :action,
                            last_review_date = :review_date,
                            logic_status = :logic_status,
                            logic_last_review_at = :updated_at,
                            updated_at = :updated_at
                        WHERE id = :id
                    """), updates)
                    conn.commit()
                for alert in alerts:
                    is_stop = any("失效" in reason for reason in alert.get("reasons", []))
                    record_lifecycle_event(
                        "WATCHLIST_INVALIDATED" if is_stop else "WATCHLIST_TRIGGERED",
                        source="watchlist_trigger",
                        code=alert.get("code"),
                        name=alert.get("name"),
                        watchlist_id=alert.get("id"),
                        payload={"reasons": alert.get("reasons"), "current_price": alert.get("current_price")},
                    )
        except Exception as exc:
            logger.warning(f"Watchlist lifecycle update failed: {exc}")
    return {
        "status": "success",
        "count": len(alerts),
        "alerts": alerts,
        "notification": notification,
    }


@router.post("/add")
def add_watchlist_item(data: Dict[str, Any]) -> Dict[str, Any]:
    code = str(data.get("code", "")).strip()
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code")

    price = float(data.get("watch_price") or data.get("price") or 0)
    if price <= 0:
        raise HTTPException(status_code=400, detail="watch_price must be positive")

    engine = get_db_engine()
    if not engine:
        return {"status": "error", "detail": "Database unavailable"}

    target_price = data.get("target_price")
    stop_price = data.get("stop_price")
    if target_price is None:
        target_price = round(price * TAKE_PROFIT_RATIO, 2)
    if stop_price is None:
        stop_price = round(price * FIXED_STOP_LOSS_RATIO, 2)

    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                INSERT INTO watchlist (
                    code, name, industry, source, strategy_type, watch_price,
                    target_price, stop_price, status, reason, theme, rise_logic, invalidation,
                    pa_trade_action, pa_trade_setup, pa_entry_condition, pa_invalidation, pa_risk_pct,
                    logic_status, created_at, updated_at
                ) VALUES (
                    :code, :name, :industry, :source, :strategy_type, :watch_price,
                    :target_price, :stop_price, 'WATCHING', :reason, :theme, :rise_logic, :invalidation,
                    :pa_trade_action, :pa_trade_setup, :pa_entry_condition, :pa_invalidation, :pa_risk_pct,
                    'UNVERIFIED', :created_at, :updated_at
                )
                RETURNING id
            """), {
                "code": code,
                "name": data.get("name") or code,
                "industry": data.get("industry") or "未知",
                "source": data.get("source") or "scan",
                "strategy_type": data.get("strategy_type") or "squeeze",
                "watch_price": price,
                "target_price": float(target_price) if target_price is not None else None,
                "stop_price": float(stop_price) if stop_price is not None else None,
                "reason": data.get("reason") or "",
                "theme": data.get("theme") or data.get("industry") or "",
                "rise_logic": data.get("rise_logic") or data.get("reason") or "",
                "invalidation": data.get("invalidation") or "",
                "pa_trade_action": data.get("pa_trade_action"),
                "pa_trade_setup": data.get("pa_trade_setup"),
                "pa_entry_condition": data.get("pa_entry_condition"),
                "pa_invalidation": data.get("pa_invalidation"),
                "pa_risk_pct": float(data.get("pa_risk_pct")) if data.get("pa_risk_pct") is not None else None,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            })
            item_id = result.scalar()
            conn.commit()
        record_lifecycle_event(
            "WATCHLIST_ADDED",
            source=data.get("source") or "scan",
            code=code,
            name=data.get("name") or code,
            watchlist_id=item_id,
            strategy_type=data.get("strategy_type") or "squeeze",
            theme=data.get("theme") or data.get("industry") or "",
            payload={"watch_price": price, "target_price": target_price, "stop_price": stop_price},
        )
        return {"status": "success", "id": item_id}
    except Exception as exc:
        logger.error(f"Add watchlist error: {exc}")
        return {"status": "error", "detail": str(exc)}


@router.post("/archive/{item_id}")
def archive_watchlist_item(item_id: int) -> Dict[str, str]:
    return _set_status(item_id, "ARCHIVED")


@router.post("/activate/{item_id}")
def activate_watchlist_item(item_id: int) -> Dict[str, str]:
    return _set_status(item_id, "WATCHING")


@router.delete("/remove/{item_id}")
def remove_watchlist_item(item_id: int) -> Dict[str, str]:
    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM watchlist WHERE id = :id"), {"id": item_id})
            conn.commit()
        return {"status": "success"}
    except Exception as exc:
        logger.error(f"Remove watchlist error: {exc}")
        return {"status": "error"}


def _set_status(item_id: int, status: str) -> Dict[str, str]:
    engine = get_db_engine()
    if not engine:
        return {"status": "error"}
    try:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE watchlist SET status = :status, updated_at = :updated_at WHERE id = :id"),
                {"status": status, "updated_at": datetime.now(), "id": item_id},
            )
            conn.commit()
        return {"status": "success"}
    except Exception as exc:
        logger.error(f"Set watchlist status error: {exc}")
        return {"status": "error"}
