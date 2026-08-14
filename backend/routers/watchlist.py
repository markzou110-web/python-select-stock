from fastapi import APIRouter, HTTPException
from sqlalchemy import text, bindparam
from typing import Dict, Any
from datetime import datetime
import asyncio
import json
import pandas as pd

from core.db import get_db_engine, validate_stock_code
from core.logging_config import logger
from core.notifier import notifier
from core.risk_constants import FIXED_STOP_LOSS_RATIO, TAKE_PROFIT_RATIO
from core.operation_plan import watch_exit_decision, watch_instruction
from core.audit_log import record_lifecycle_event, record_watchlist_theme_state_change
from core.theme_leadership import (
    build_theme_leadership_body,
    discover_theme_leadership,
    load_theme_leadership_review,
)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

_THEME_MOMENTUM_PUSHED = {}


def _age_days(value: Any) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        return max(0, (datetime.now() - parsed).days)
    except Exception:
        return 0


def _triggered_archive_decision(item: Dict[str, Any], max_triggered_days: int = 5) -> Dict[str, Any]:
    if (item.get("status") or "") != "TRIGGERED":
        return {"should_archive": False, "reason": ""}
    age = _age_days(item.get("updated_at") or item.get("created_at"))
    execution_state = item.get("execution_state") or ""
    if age >= max(2, int(max_triggered_days)) and execution_state not in {"CONFIRM"}:
        return {
            "should_archive": True,
            "reason": f"已触发 {age} 天未转实盘，自动归档，避免占用观察池版面",
        }
    return {"should_archive": False, "reason": ""}


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


def _is_sector_watch_item(item: Dict[str, Any]) -> bool:
    return (
        str(item.get("strategy_type") or "") == "sector_watch"
        or str(item.get("source") or "") == "sector_push_gap"
    )


def _sector_watch_volume_confirmed(item: Dict[str, Any]) -> bool:
    pattern = str(item.get("pa_volume_pattern") or "")
    return bool(item.get("pa_volume_confirmed")) or pattern in {"放量突破", "量能确认", "缩量回调后放量反包"}


def _sector_watch_state(item: Dict[str, Any]) -> Dict[str, str]:
    if not _is_sector_watch_item(item):
        return {}

    current = float(item.get("current_price") or 0)
    watch_price = float(item.get("watch_price") or 0)
    target_price = item.get("target_price")
    intraday_high = float(item.get("intraday_high") or 0)
    pa_action = str(item.get("pa_trade_action") or "")
    volume_confirmed = _sector_watch_volume_confirmed(item)

    if item.get("stop_hit") or pa_action == "AVOID":
        return {
            "state": "INVALIDATED",
            "label": "逻辑失效",
            "action": "板块/个股结构失效，归档或重新等待新信号",
        }
    if item.get("target_hit") or (target_price is not None and current > 0 and current >= float(target_price)):
        return {
            "state": "TRIGGERED",
            "label": "已触发",
            "action": "触发观察目标，仍需尾盘站稳和量能确认后再复核",
        }
    if target_price is not None and current > 0:
        distance = (float(target_price) - current) / current * 100
        if 0 <= distance <= 3:
            if volume_confirmed:
                return {
                    "state": "PULLBACK_CONFIRMED",
                    "label": "回踩放量确认",
                    "action": "价位接近确认价且量能有效，尾盘站稳确认价后再复核",
                }
            return {
                "state": "APPROACH_CONFIRM",
                "label": "接近确认价",
                "action": "接近确认价，等待放量站稳，不提前追",
            }
    if (
        current > 0
        and watch_price > 0
        and watch_price * 0.98 <= current <= watch_price * 1.02
        and intraday_high >= watch_price * 1.03
    ):
        if volume_confirmed:
            return {
                "state": "PULLBACK_CONFIRMED",
                "label": "回踩放量确认",
                "action": "回踩观察价附近且量能确认，等待尾盘站稳确认价",
            }
        return {
            "state": "PULLBACK_NEEDS_VOLUME",
            "label": "回踩待放量",
            "action": "回踩到观察区，但量能未确认，等放量反包/站稳确认价",
        }
    if current > 0 and watch_price > 0 and current >= watch_price * 1.04:
        return {
            "state": "WAIT_PULLBACK",
            "label": "涨幅偏高等回踩",
            "action": "题材已发酵但不追涨，等待回踩不破支撑后再确认",
        }
    return {
        "state": "THEME_TRACKING",
        "label": "题材跟踪中",
        "action": "跟踪板块强度和核心股轮动，等待明确买点",
    }


def _attach_sector_watch_state(item: Dict[str, Any], *, audit: bool = False) -> None:
    sector_watch = _sector_watch_state(item)
    if not sector_watch:
        return
    item["theme_tracking_state"] = sector_watch["state"]
    item["theme_tracking_label"] = sector_watch["label"]
    item["theme_tracking_action"] = sector_watch["action"]
    if audit:
        record_watchlist_theme_state_change(
            watchlist_id=item.get("id"),
            code=item.get("code"),
            name=item.get("name"),
            strategy_type=item.get("strategy_type"),
            theme=item.get("theme") or item.get("industry"),
            source=item.get("source") or "watchlist",
            state=sector_watch["state"],
            label=sector_watch["label"],
            action=sector_watch["action"],
            current_price=item.get("current_price"),
            watch_price=item.get("watch_price"),
            target_price=item.get("target_price"),
            stop_price=item.get("stop_price"),
            pl_pct=item.get("pl_pct"),
        )


def _watch_execution_state(item: Dict[str, Any]) -> Dict[str, str]:
    current = float(item.get("current_price") or 0)
    watch_price = float(item.get("watch_price") or 0)
    trigger = item.get("trigger_price") or item.get("target_price")
    guard = item.get("guard_price") or item.get("stop_price")
    high = float(item.get("intraday_high") or current or 0)

    if item.get("stop_hit"):
        return {"state": "CANCEL", "label": "取消计划", "action": "已跌破失效价，不再观察买入"}
    if not trigger or current <= 0:
        return {"state": "WAIT", "label": "等待", "action": "价格条件不足，继续观察"}

    trigger = float(trigger)
    guard = float(guard or 0)
    over_trigger_pct = (current - trigger) / trigger * 100 if trigger > 0 else 0
    high_over_trigger = high >= trigger if trigger > 0 and high > 0 else False

    if high_over_trigger and current < trigger:
        return {"state": "FADED", "label": "冲高回落", "action": "盘中触发后回落，尾盘不确认不买"}
    if current >= trigger and over_trigger_pct > 3:
        return {"state": "NO_CHASE", "label": "高位不追", "action": "已高出触发价3%以上，等待回踩确认"}
    if current >= trigger:
        return {"state": "CONFIRM", "label": "待确认", "action": "站上触发价，尾盘确认承接后再考虑小仓"}
    if guard > 0 and current <= guard * 1.02:
        return {"state": "NEAR_GUARD", "label": "贴近失效", "action": "接近保护价，暂不转实盘"}
    if watch_price > 0 and current >= watch_price * 1.05:
        return {"state": "PULLBACK", "label": "等回踩", "action": "观察后已有涨幅，不追涨"}
    return {"state": "WAIT", "label": "等待", "action": "未触发，继续观察"}


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
    # P1：标注行情新鲜度。用缓存里与 current_price 同源的快照 attrs，避免重新抓取
    # 导致价格(来自触发检查时的快照)与时间戳(新快照)矛盾。
    try:
        from core.data import get_stale_cache, format_freshness
        lines.append(format_freshness(get_stale_cache("market_snapshot")))
    except Exception:
        pass
    body = "\n".join(lines)

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(notifier.send(title, body, channels=["bark"]))
            return {"bark": False, "pending": True}
        return asyncio.run(notifier.send(title, body, channels=["bark"]))
    except Exception as exc:
        logger.error(f"Watchlist trigger notification error: {exc}")
        return {"bark": False}


def _refresh_items_with_snapshot(items, require_live_snapshot: bool = False):
    if not items:
        return []
    try:
        from core.data import get_market_snapshot, is_snapshot_stale

        snapshot = get_market_snapshot()
        if snapshot is None or snapshot.empty:
            if require_live_snapshot:
                return None
            return items
        if require_live_snapshot and is_snapshot_stale(snapshot):
            return None
        snapshot_map = snapshot.set_index("code")["price"].to_dict()
        snapshot_high_map = (
            snapshot.set_index("code")["high"].to_dict()
            if "high" in getattr(snapshot, "columns", [])
            else {}
        )
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
        if require_live_snapshot:
            return None
        return items

    refreshed = []
    for original in items:
        item = dict(original)
        item.update(market_context)
        current_price = snapshot_map.get(item["code"])
        if not current_price:
            if require_live_snapshot:
                return None
            refreshed.append(item)
            continue

        current_price = round(float(current_price), 2)
        watch_price = float(item.get("watch_price") or current_price)
        target_price = item.get("target_price")
        stop_price = item.get("stop_price")
        item["current_price"] = current_price
        if item["code"] in snapshot_high_map:
            try:
                item["intraday_high"] = round(float(snapshot_high_map[item["code"]]), 2)
            except Exception:
                pass
        item["pl_pct"] = round((current_price - watch_price) / watch_price * 100, 2) if watch_price > 0 else 0
        item["target_hit"] = target_price is not None and current_price >= float(target_price)
        item["stop_hit"] = stop_price is not None and current_price <= float(stop_price)

        decision = _watch_decision(item)
        instruction = watch_instruction(item)
        item["computed_decision"] = decision["decision"]
        item["computed_action"] = decision["action"]
        _attach_sector_watch_state(item, audit=True)
        item["operation_instruction"] = instruction["instruction"]
        item["trigger_price"] = instruction["trigger_price"]
        item["guard_price"] = instruction["guard_price"]
        execution = _watch_execution_state(item)
        item["execution_state"] = execution["state"]
        item["execution_label"] = execution["label"]
        item["execution_action"] = execution["action"]
        # DB 中已是 TRIGGERED 的票：target_hit=True 会被 _watch_decision 算成 PROMOTE，
        # 这里覆盖为 TRIGGERED 语义，使其在分区渲染中归入"已触发待确认"段，
        # 与 WATCHING 池里恰好接近目标价的票视觉区分。不修改 DB 字段。
        if original.get("status") == "TRIGGERED":
            item["computed_decision"] = "TRIGGERED"
            item["computed_action"] = "已触发目标价：待确认是否转入拟合实盘"
        refreshed.append(item)
    return refreshed


def _watchlist_status_sort_key(item: Dict[str, Any], state_boosts: Dict[str, float] | None = None) -> tuple:
    state_boosts = state_boosts or {}
    decision_rank = {
        "TRIGGERED": 90,
        "PROMOTE": 85,
        "NEAR_TRIGGER": 75,
        "READY_WAIT": 68,
        "KEEP_WATCH": 50,
        "WATCH_PULLBACK": 35,
        "RISK": 20,
        "INVALIDATE": 0,
    }
    theme_state_rank = {
        "PULLBACK_CONFIRMED": 35,
        "APPROACH_CONFIRM": 25,
        "PULLBACK_NEEDS_VOLUME": 15,
        "THEME_TRACKING": 5,
        "WAIT_PULLBACK": -8,
        "INVALIDATED": -60,
    }
    execution_rank = {
        "CONFIRM": 20,
        "WAIT": 5,
        "PULLBACK": 0,
        "FADED": -12,
        "NO_CHASE": -18,
        "NEAR_GUARD": -25,
        "CANCEL": -60,
    }
    decision = str(item.get("computed_decision") or item.get("watch_decision") or "KEEP_WATCH")
    theme_state = str(item.get("theme_tracking_state") or "")
    execution_state = str(item.get("execution_state") or "")
    return (
        decision_rank.get(decision, 40)
        + theme_state_rank.get(theme_state, 0)
        + execution_rank.get(execution_state, 0)
        + float(state_boosts.get(theme_state, 0)),
        float(item.get("pl_pct") or 0),
        str(item.get("updated_at") or ""),
    )


def _theme_state_priority_note(item: Dict[str, Any], state_boosts: Dict[str, float] | None = None) -> str:
    state_boosts = state_boosts or {}
    theme_state = str(item.get("theme_tracking_state") or "")
    boost = float(state_boosts.get(theme_state, 0))
    label = item.get("theme_tracking_label") or theme_state
    if boost > 0:
        return f"状态复盘加权：{label}近5日表现较好，优先展示"
    if boost < 0:
        return f"状态复盘降权：{label}近5日表现偏弱，保守观察"
    return ""


def _build_watchlist_status_body(items, slot: str, state_boosts: Dict[str, float] | None = None) -> str:
    is_morning = slot == "morning"
    is_noon = slot == "noon"
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
            else (
                "午间纪律：只做下午计划；未放量站稳触发价不追，跌破保护价取消观察。"
                if is_noon
                else "尾盘纪律：仅确认全天承接有效的标的；冲高回落、跌破保护价或未站稳触发价均不买。"
            )
        ),
        "",
    ]

    def render_item(item: Dict[str, Any]) -> None:
        decision = item.get("computed_decision") or "KEEP_WATCH"
        action = item.get("computed_action") or "继续观察"
        if market_blocked:
            action = f"市场退潮，禁止新增仓位；原计划：{action}"
        trigger = item.get("trigger_price") or item.get("target_price")
        guard = item.get("guard_price") or item.get("stop_price")
        execution_state = str(item.get("execution_state") or "")
        lines.append(
            f"【{decision}】{item.get('name', '')}({item.get('code', '')}) "
            f"现价 {item.get('current_price', '--')} ({float(item.get('pl_pct') or 0):+.2f}%)"
        )
        lines.append(f"  结论：{action}")
        if item.get("execution_label") or item.get("execution_action"):
            lines.append(
                f"  执行状态：{item.get('execution_label', '--')} | "
                f"{item.get('execution_action', '')}"
            )
        if item.get("theme_tracking_label") or item.get("theme_tracking_action"):
            lines.append(
                f"  题材状态：{item.get('theme_tracking_label', '--')} | "
                f"{item.get('theme_tracking_action', '')}"
            )
        priority_note = _theme_state_priority_note(item, state_boosts)
        if priority_note:
            lines.append(f"  {priority_note}")
        if execution_state == "NO_CHASE":
            lines.append(
                f"  价格指令：已远离触发价，不以继续上涨作为买点；"
                f"等待回踩后重新确认，跌破 <{guard if guard else '--'} 取消观察"
            )
        else:
            lines.append(f"  价格指令：站稳 >{trigger if trigger else '--'} 再复核；跌破 <{guard if guard else '--'} 取消观察")
        if market_blocked:
            lines.append("  操作：今日只观察，不买入；等待市场情绪至少修复后重新评估")
        elif execution_state == "NO_CHASE":
            lines.append("  操作：禁止转实盘或追价；只有完成回踩并重新确认后才能生成新计划")
        elif item.get("operation_instruction"):
            lines.append(f"  操作：{item['operation_instruction']}")
        lines.append("")

    # 分区：WATCHING 票（继续观察）与 TRIGGERED 票（已触发待确认）视觉分离，
    # 避免 TRIGGERED 票混在普通观察票里被忽略（600460 漏推根因）。
    watching_items = [i for i in items if i.get("computed_decision") != "TRIGGERED"]
    triggered_items = [i for i in items if i.get("computed_decision") == "TRIGGERED"]
    watching_items = sorted(watching_items, key=lambda i: _watchlist_status_sort_key(i, state_boosts), reverse=True)
    triggered_items = sorted(triggered_items, key=lambda i: _watchlist_status_sort_key(i, state_boosts), reverse=True)

    for item in watching_items[:10]:
        render_item(item)
    if len(watching_items) > 10:
        lines.append(f"另有 {len(watching_items) - 10} 只观察票，请打开观察池查看。")

    if triggered_items:
        lines.append("--- 已触发待确认 ---")
        for item in triggered_items[:5]:
            render_item(item)
        if len(triggered_items) > 5:
            lines.append(f"另有 {len(triggered_items) - 5} 只已触发票，请打开观察池查看。")

    return "\n".join(lines).rstrip()


def _theme_key(item: Dict[str, Any]) -> str:
    return str(item.get("theme") or item.get("industry") or "").strip()


def _load_theme_state_priority_boosts(days: int = 120, min_samples: int = 3) -> Dict[str, float]:
    engine = get_db_engine()
    if not engine:
        return {}
    try:
        cutoff = datetime.now() - pd.Timedelta(days=max(1, min(int(days), 365)))
        events = pd.read_sql(text("""
            SELECT event_time, code, payload
            FROM lifecycle_events
            WHERE event_type = 'WATCHLIST_THEME_STATE_CHANGED'
              AND event_time >= :cutoff
        """), engine, params={"cutoff": cutoff})
        if events.empty:
            return {}
        codes = sorted({str(code).zfill(6) for code in events["code"].dropna().astype(str)})
        if not codes:
            return {}
        stmt = text("""
            SELECT code, date, close
            FROM daily_k
            WHERE code IN :codes
            ORDER BY code, date
        """).bindparams(bindparam("codes", expanding=True))
        daily = pd.read_sql(stmt, engine, params={"codes": codes})
        if daily.empty:
            return {}
        daily["date"] = pd.to_datetime(daily["date"]).dt.date
        daily = daily.sort_values(["code", "date"])
        returns_by_state: Dict[str, list[float]] = {}
        for _, event in events.iterrows():
            payload = event.get("payload") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            state = str(payload.get("state") or "")
            event_price = float(payload.get("current_price") or payload.get("watch_price") or 0)
            event_date = pd.to_datetime(event.get("event_time"), errors="coerce")
            if not state or event_price <= 0 or pd.isna(event_date):
                continue
            code = str(event.get("code") or "").zfill(6)
            path = daily[(daily["code"].astype(str).str.zfill(6) == code) & (daily["date"] > event_date.date())]
            if len(path) < 5:
                continue
            close_5d = float(path.iloc[4]["close"])
            returns_by_state.setdefault(state, []).append((close_5d - event_price) / event_price * 100)
        boosts: Dict[str, float] = {}
        for state, returns in returns_by_state.items():
            if len(returns) < max(1, int(min_samples)):
                continue
            series = pd.Series(returns)
            avg = float(series.mean())
            win_rate = float((series > 0).mean() * 100)
            if avg > 1 and win_rate >= 55:
                boosts[state] = 25.0
            elif avg < 0:
                boosts[state] = -25.0
        return boosts
    except Exception as exc:
        logger.debug(f"Theme state priority boost unavailable: {exc}")
        return {}

def _is_limit_like(item: Dict[str, Any]) -> bool:
    price = float(item.get("current_price") or item.get("price") or 0)
    limit_up = float(item.get("limit_up") or 0)
    pct = float(item.get("pct_chg") or item.get("pl_pct") or 0)
    if limit_up > 0 and price > 0:
        return price >= limit_up * 0.995
    return pct >= 9.8


def _build_theme_momentum_alert(items, min_theme_count: int = 3) -> Dict[str, Any]:
    groups: Dict[str, list] = {}
    for item in items:
        theme = _theme_key(item)
        if not theme:
            continue
        groups.setdefault(theme, []).append(item)

    alerts = []
    for theme, group in groups.items():
        rising = [i for i in group if float(i.get("pct_chg") or 0) >= 3]
        leaders = [i for i in group if float(i.get("pct_chg") or 0) >= 7 or _is_limit_like(i)]
        advancing = [i for i in group if float(i.get("pct_chg") or 0) > 0]
        strong = [i for i in group if float(i.get("pct_chg") or 0) >= 5 or _is_limit_like(i)]
        group_avg = sum(float(i.get("pct_chg") or 0) for i in group) / len(group)
        advance_ratio = len(advancing) / len(group) * 100
        cluster_breakout = len(rising) >= min_theme_count and bool(leaders)
        breadth_breakout = (
            len(group) >= 8
            and advance_ratio >= 70
            and group_avg >= 1.5
            and len(strong) >= 2
        )
        if not cluster_breakout and not breadth_breakout:
            continue
        display_pool = rising if rising else advancing
        display_pool = sorted(display_pool, key=lambda x: float(x.get("pct_chg") or 0), reverse=True)
        watchable = [
            i for i in display_pool
            if not _is_limit_like(i) and float(i.get("pct_chg") or 0) < 8
        ]
        alerts.append({
            "theme": theme,
            "rising_count": len(rising),
            "leader_count": len(strong),
            "leaders": display_pool[:5],
            "watchable": watchable[:5],
            "avg_pct": round(group_avg, 2),
            "advance_ratio": round(advance_ratio, 1),
            "signal_mode": "BREADTH" if breadth_breakout and not cluster_breakout else "CLUSTER",
        })
    alerts.sort(key=lambda x: (x["leader_count"], x["rising_count"], x["avg_pct"]), reverse=True)
    return {"alerts": alerts, "count": len(alerts)}


def _build_theme_momentum_body(alerts: list, slot: str) -> str:
    lines = [
        f"时段：{slot}",
        "性质：题材异动预警，不是买入指令。",
        "纪律：涨停/大涨不追；只找未封板、放量跟随、回踩不破后的确认机会。",
        "",
    ]
    for alert in alerts[:3]:
        lines.append(
            f"【{alert['theme']}】{alert['rising_count']}只上涨>3%，"
            f"{alert['leader_count']}只强势，板块均涨 {alert['avg_pct']}%，"
            f"上涨占比 {alert.get('advance_ratio', 0)}%"
        )
        leader_text = "、".join(
            f"{i.get('name')}({i.get('code')}) {float(i.get('pct_chg') or 0):+.2f}%"
            for i in alert["leaders"][:4]
        )
        lines.append(f"  龙头：{leader_text}")
        if alert["watchable"]:
            watch_text = "、".join(
                f"{i.get('name')}({i.get('code')}) {float(i.get('pct_chg') or 0):+.2f}%"
                for i in alert["watchable"][:4]
            )
            lines.append(f"  可观察：{watch_text}")
        else:
            lines.append("  可观察：多数已高位，等回踩确认")
        lines.append("")
    if len(alerts) > 3:
        lines.append(f"另有 {len(alerts) - 3} 个主题异动，请打开观察池查看。")
    return "\n".join(lines).rstrip()


def send_theme_momentum_alert(slot: str = "morning", notify: bool = True) -> Dict[str, Any]:
    """Push observation-only theme momentum from watchlist or the live market universe."""
    engine = get_db_engine()
    if not engine:
        return {"bark": False, "count": 0, "reason": "db_unavailable"}

    try:
        items_df = pd.read_sql(text("""
            SELECT code, name, industry, theme, watch_price, status, rise_logic
            FROM watchlist
            WHERE status IN ('WATCHING', 'TRIGGERED')
        """), engine)
    except Exception as exc:
        logger.error(f"Theme momentum watchlist fetch failed: {exc}")
        return {"bark": False, "count": 0, "reason": "watchlist_fetch_failed", "detail": str(exc)}
    universe_mode = items_df.empty

    try:
        from core.data import get_market_snapshot, is_snapshot_stale, get_stale_cache, format_freshness
        snapshot = get_market_snapshot()
        if snapshot is None or snapshot.empty or is_snapshot_stale(snapshot):
            return {"bark": False, "count": 0, "reason": "live_snapshot_unavailable"}
        snapshot_map = snapshot.set_index("code").to_dict("index")
    except Exception as exc:
        logger.warning(f"Theme momentum snapshot unavailable: {exc}")
        return {"bark": False, "count": 0, "reason": "live_snapshot_unavailable", "detail": str(exc)}

    items = []
    if universe_mode:
        from core.db import get_stock_basic_map
        basic_map = get_stock_basic_map()
        for _, snap in snapshot.iterrows():
            code = str(snap.get("code") or "").zfill(6)
            name = str(snap.get("name") or "")
            raw_industry = snap.get("industry")
            industry = "" if pd.isna(raw_industry) else str(raw_industry).strip()
            if not industry:
                industry = str((basic_map.get(code) or {}).get("industry") or "").strip()
            if (
                not code.startswith(("60", "00", "30", "688"))
                or "ST" in name.upper() or "退" in name
                or not industry
            ):
                continue
            items.append({
                "code": code,
                "name": name,
                "industry": industry,
                "theme": industry,
                "current_price": round(float(snap.get("price") or 0), 2),
                "pct_chg": round(float(snap.get("pct_chg") or 0), 2),
                "turnover": round(float(snap.get("turnover") or 0), 2),
                "amount": float(snap.get("amount") or 0),
                "limit_up": float(snap.get("limit_up") or 0),
                "trade_eligible": False,
                "trade_bucket": "OBSERVE",
            })
    else:
        for _, row in items_df.iterrows():
            code = str(row["code"]).zfill(6)
            snap = snapshot_map.get(code)
            if not snap:
                continue
            item = dict(row)
            item["code"] = code
            item["current_price"] = round(float(snap.get("price") or 0), 2)
            item["pct_chg"] = round(float(snap.get("pct_chg") or 0), 2)
            item["turnover"] = round(float(snap.get("turnover") or 0), 2)
            item["amount"] = float(snap.get("amount") or 0)
            item["limit_up"] = float(snap.get("limit_up") or 0)
            items.append(item)

    try:
        shadow_payload = discover_theme_leadership(engine, snapshot, observed_at=datetime.now())
    except Exception as exc:
        logger.warning(f"Theme leadership shadow skipped without affecting momentum alert: {exc}")
        shadow_payload = {"items": [], "count": 0, "technical_seed_count": 0}
    shadow_items = shadow_payload["items"]
    alert_payload = _build_theme_momentum_alert(items)
    alerts = alert_payload["alerts"]
    if not alerts and not shadow_items:
        return {
            "bark": False, "count": 0, "reason": "no_theme_momentum",
            "source": "market_snapshot" if universe_mode else "watchlist",
            "theme_leadership_count": 0,
            "technical_seed_count": shadow_payload["technical_seed_count"],
        }

    today = datetime.now().strftime("%Y-%m-%d")
    theme_names = ",".join(alert["theme"] for alert in alerts)
    shadow_names = ",".join(f"{item['code']}:{item['state']}" for item in shadow_items)
    dedupe_key = f"{today}:{theme_names}:{shadow_names}"
    total_count = len(alerts) + len(shadow_items)
    if notify and _THEME_MOMENTUM_PUSHED.get(dedupe_key):
        return {
            "bark": False, "count": total_count, "reason": "deduped",
            "theme_leadership_count": len(shadow_items),
        }

    sections = []
    if alerts:
        sections.append(_build_theme_momentum_body(alerts, slot))
    if shadow_items:
        sections.append(build_theme_leadership_body(shadow_items, slot))
    body = "\n\n".join(sections)
    try:
        body = body + "\n" + format_freshness(get_stale_cache("market_snapshot"))
    except Exception:
        pass
    if not notify:
        return {
            "bark": False, "count": total_count, "body": body, "notification": False,
            "source": "market_snapshot" if universe_mode else "watchlist",
            "theme_leadership_count": len(shadow_items),
            "technical_seed_count": shadow_payload["technical_seed_count"],
        }

    title = f"Alpha Vision 题材异动预警 {today}"
    try:
        result = asyncio.run(notifier.send(
            title,
            body,
            channels=["bark"],
            group="AlphaVision_Theme",
            url="http://localhost:3000",
        ))
        sent = bool(result.get("bark"))
        if sent:
            _THEME_MOMENTUM_PUSHED[dedupe_key] = datetime.now()
        return {
            "bark": sent, "count": total_count, "body": body,
            "source": "market_snapshot" if universe_mode else "watchlist",
            "theme_leadership_count": len(shadow_items),
            "technical_seed_count": shadow_payload["technical_seed_count"],
        }
    except Exception as exc:
        logger.error(f"Theme momentum Bark push failed: {exc}")
        return {
            "bark": False, "count": total_count, "body": body, "detail": str(exc),
            "theme_leadership_count": len(shadow_items),
        }


@router.get("/theme-leadership/review")
def get_theme_leadership_review(days: int = 120) -> Dict[str, Any]:
    """Review forward returns of observation-only theme-leadership signals."""
    engine = get_db_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return load_theme_leadership_review(engine, days=days)


def send_watchlist_status_report(slot: str, notify: bool = True) -> Dict[str, Any]:
    """Push a proactive morning plan or late-session conclusion for the formal watchlist."""
    if slot not in {"morning", "noon", "late"}:
        return {"bark": False, "count": 0, "reason": "unsupported slot"}

    # 拉取 WATCHING + TRIGGERED 两个池，TRIGGERED 票（已触发目标价）需持续追踪，
    # 否则会从所有后续晨报/尾盘报告中消失，失去后续价格追踪（600460 漏推根因）。
    watching_payload = list_watchlist(status="WATCHING")
    triggered_payload = list_watchlist(status="TRIGGERED")
    combined_items = watching_payload.get("items", []) + triggered_payload.get("items", [])
    items = _refresh_items_with_snapshot(combined_items, require_live_snapshot=True)
    if items is None:
        logger.warning("Watchlist status Bark skipped: live market snapshot unavailable.")
        return {"bark": False, "count": 0, "reason": "live_snapshot_unavailable"}
    if not items:
        return {"bark": False, "count": 0, "reason": "empty watchlist"}

    title_map = {
        "morning": "Alpha Vision 观察池晨间计划",
        "noon": "Alpha Vision 观察池午间计划",
        "late": "Alpha Vision 观察池尾盘结论",
    }
    title = title_map[slot]
    state_boosts = _load_theme_state_priority_boosts()
    body = _build_watchlist_status_body(items, slot, state_boosts=state_boosts)
    # P1：标注行情新鲜度。用缓存里与 items 现价同源的快照 attrs（_refresh_items_with_snapshot
    # 刚抓的快照就在缓存里），避免重新抓取导致价格与时间戳矛盾。
    try:
        from core.data import get_stale_cache, format_freshness
        body = body + "\n" + format_freshness(get_stale_cache("market_snapshot"))
    except Exception:
        pass
    if not notify:
        return {"bark": False, "count": len(items), "body": body, "notification": False}
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(notifier.send(title, body, channels=["bark"]))
            return {"bark": False, "pending": True, "count": len(items), "body": body}
        result = asyncio.run(notifier.send(title, body, channels=["bark"]))
        return {"bark": bool(result.get("bark")), "count": len(items), "body": body}
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
        elif status == "ACTIVE":
            df = pd.read_sql(
                text("SELECT * FROM watchlist WHERE status IN ('WATCHING', 'TRIGGERED') ORDER BY updated_at DESC, created_at DESC"),
                engine,
            )
        else:
            df = pd.read_sql(
                text("SELECT * FROM watchlist WHERE status = :status ORDER BY updated_at DESC, created_at DESC"),
                engine,
                params={"status": status},
            )
        if df.empty:
            return {"items": [], "stats": {"total": 0, "triggered": 0, "avg_pl_pct": 0}}

        price_map = _latest_prices(engine, df["code"].unique().tolist())
        state_boosts = _load_theme_state_priority_boosts()
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
                "updated_at": row["updated_at"].isoformat() if hasattr(row.get("updated_at"), "isoformat") else str(row.get("updated_at") or ""),
                "latest_date": latest.get("date"),
            }
            decision = _watch_decision(item)
            instruction = watch_instruction(item)
            exit_check = watch_exit_decision({**item, "computed_decision": decision["decision"], "computed_action": decision["action"]})
            item["computed_decision"] = decision["decision"]
            item["computed_action"] = decision["action"]
            item["logic_status"] = _logic_status(decision["decision"])
            _attach_sector_watch_state(item, audit=True)
            item["theme_priority_note"] = _theme_state_priority_note(item, state_boosts)
            item["theme_priority_boost"] = float(state_boosts.get(str(item.get("theme_tracking_state") or ""), 0))
            item["operation_instruction"] = instruction["instruction"]
            item["trigger_price"] = instruction["trigger_price"]
            item["guard_price"] = instruction["guard_price"]
            item["auto_exit_reason"] = exit_check["reason"] if exit_check["should_exit"] else ""
            if not item["watch_decision"]:
                item["watch_decision"] = decision["decision"]
                item["watch_action"] = decision["action"]
            items.append(item)
        items = sorted(items, key=lambda i: _watchlist_status_sort_key(i, state_boosts), reverse=True)

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
def auto_prune_watchlist(max_watch_days: int = 15, max_triggered_days: int = 5) -> Dict[str, Any]:
    payload = list_watchlist(status="WATCHING")
    triggered_payload = list_watchlist(status="TRIGGERED")
    items = payload.get("items", [])
    triggered_items = triggered_payload.get("items", [])
    engine = get_db_engine()
    if not engine:
        return {"status": "error", "updated": 0, "items": []}

    prune_items = []
    for item in items:
        exit_check = watch_exit_decision(item, max_watch_days=max(3, int(max_watch_days)))
        if exit_check["should_exit"]:
            prune_items.append({**item, "exit_reason": exit_check["reason"]})
    archive_items = []
    for item in triggered_items:
        archive_check = _triggered_archive_decision(item, max_triggered_days=max_triggered_days)
        if archive_check["should_archive"]:
            archive_items.append({**item, "exit_reason": archive_check["reason"]})

    if not prune_items and not archive_items:
        return {"status": "success", "updated": 0, "items": []}

    try:
        with engine.connect() as conn:
            if prune_items:
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
            if archive_items:
                conn.execute(text("""
                    UPDATE watchlist
                    SET status = 'ARCHIVED',
                        watch_decision = 'AUTO_ARCHIVE_TRIGGERED',
                        watch_action = :action,
                        exit_reason = :reason,
                        last_review_date = :review_date,
                        updated_at = :updated_at
                    WHERE id = :id
                """), [
                    {
                        "id": item["id"],
                        "action": "自动归档：已触发但长期未转实盘",
                        "reason": item["exit_reason"],
                        "review_date": datetime.now().strftime("%Y-%m-%d"),
                        "updated_at": datetime.now(),
                    }
                    for item in archive_items
                ])
            conn.commit()
        changed_items = prune_items + archive_items
        return {
            "status": "success",
            "updated": len(changed_items),
            "items": [
                {"id": item["id"], "code": item["code"], "name": item["name"], "exit_reason": item["exit_reason"]}
                for item in changed_items
            ],
        }
    except Exception as exc:
        logger.error(f"Auto prune watchlist error: {exc}")
        return {"status": "error", "updated": 0, "items": [], "detail": str(exc)}


@router.post("/check-triggers")
def check_watchlist_triggers(notify: bool = True) -> Dict[str, Any]:
    payload = list_watchlist(status="WATCHING")
    items = _refresh_items_with_snapshot(payload.get("items", []), require_live_snapshot=True)
    if items is None:
        logger.warning("Watchlist trigger Bark skipped: live market snapshot unavailable.")
        return {
            "status": "success",
            "count": 0,
            "alerts": [],
            "notification": {},
            "reason": "live_snapshot_unavailable",
        }
    alerts = []

    for item in items:
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
