from .celery_app import celery_app
from .logging_config import logger
from .notifier import notifier
from .db import get_db_engine, get_setting, save_setting
from .task_idempotency import daily_task_slot
from .data import get_market_snapshot, is_snapshot_stale
from .indicators import calculate_indicators
from core.strategy import evaluate_exit_signals
from core.risk_engine import safe_float
from core.logic_chain import build_capital_evidence_line, build_logic_chain_line
from core.trading_calendar import is_a_share_intraday_session, is_a_share_trading_day
import asyncio
import json
import pandas as pd
import re
import time
from datetime import datetime, time as datetime_time, timedelta
from typing import Any, Dict, Optional
from sqlalchemy import text

# 收盘AI复核前的个股研究快照预热预算（新闻/公告等15个数据源，纯增量缓存）
_DAILY_AI_RESEARCH_WARMUP_SECONDS = 240

_ALERT_DEDUPE_CACHE = {}
_ALERT_DEDUPE_SECONDS = 30 * 60
_ALERT_STATE_KEY_PREFIX = "realtime_exit_alert:"
_SCHEDULED_SCAN_MAX_DELAY_MINUTES = 15
_AFTER_CLOSE_REVIEW_END = datetime_time(18, 0)


def _scheduled_scan_skip_reason(now: datetime, slot: str) -> str | None:
    """Reject periodic scan messages that are closed-market or too old to execute."""
    if not is_a_share_intraday_session(now):
        return "market_closed"
    if slot not in {"09:45", "13:15", "13:25"}:
        return None

    hour, minute = (int(part) for part in slot.split(":"))
    scheduled_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delay_minutes = (now - scheduled_at).total_seconds() / 60
    if delay_minutes < 0 or delay_minutes > _SCHEDULED_SCAN_MAX_DELAY_MINUTES:
        return "stale_task_slot"
    return None


def _is_after_close_review_window(now: datetime) -> bool:
    return (
        is_a_share_trading_day(now)
        and datetime_time(15, 0) <= now.time() <= _AFTER_CLOSE_REVIEW_END
    )


def _premarket_position_action(plan: dict) -> str:
    current = safe_float(plan.get("current_price"))
    stop = safe_float(plan.get("active_stop_price"))
    guard = safe_float(plan.get("add_guard_price"))
    trigger = safe_float(plan.get("add_trigger_price"))
    if stop > 0 and current <= stop:
        return "已低于防守线，开盘优先减仓或退出复核"
    if guard > 0 and current <= guard:
        return "持有观察，不加仓；已低于撤退线，转弱则执行风控"
    if trigger > 0 and current < trigger:
        return "持有观察，不加仓；等待放量站上转强线"
    return "已到转强线，等待开盘后量价确认；不自动加仓"


def build_premarket_position_advice(
    positions: list[dict],
    market: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Build one compact, actionable Bark card for open real positions."""
    now = now or datetime.now()
    market_desc = str((market or {}).get("desc") or "市场状态待开盘确认")
    lines = [f"市场：{market_desc}", "纪律：盘前价位基于上一交易日收盘，不自动下单", ""]
    visible = positions[:8]
    for item in visible:
        plan = item.get("plan") or {}
        entry = safe_float(plan.get("entry_price"))
        current = safe_float(plan.get("current_price"), entry)
        stop = safe_float(plan.get("active_stop_price"))
        guard = safe_float(plan.get("add_guard_price"))
        trigger = safe_float(plan.get("add_trigger_price"))
        price_action = item.get("price_action") or {}
        pl_pct = (current - entry) / entry * 100 if entry > 0 else 0
        lines.extend([
            f"{item.get('name') or item.get('code')}({item.get('code')})",
            f"昨收 {current:.2f}｜成本 {entry:.2f}｜{pl_pct:+.2f}%",
            f"防守 {stop:.2f}｜撤退 {guard:.2f}｜转强 {trigger:.2f}",
            f"建议：{_premarket_position_action(plan)}",
        ])
        structure_bits = [
            str(value) for value in (
                price_action.get("structure_state_label"),
                f"突破跟进{price_action.get('follow_through_state')}" if price_action.get("follow_through_state") not in {None, "NONE"} else None,
                f"MTR {price_action.get('mtr_state')}" if price_action.get("mtr_state") not in {None, "NONE"} else None,
            ) if value
        ]
        if structure_bits:
            lines.append(f"价格行为：{'｜'.join(structure_bits)}")
        nearest_support = price_action.get("nearest_support") or {}
        nearest_resistance = price_action.get("nearest_resistance") or {}
        if nearest_support.get("center") or nearest_resistance.get("center"):
            lines.append(
                f"结构区：支撑 {nearest_support.get('center') or '--'}｜压力 {nearest_resistance.get('center') or '--'}"
            )
        lines.append("")
    if len(positions) > len(visible):
        lines.append(f"另有 {len(positions) - len(visible)} 只持仓请在系统内查看。")
    lines.append("盘中仅在关键区间首次进入或状态变化时提醒；同类信号重入冷却30分钟。")
    return {
        "title": f"📋 盘前持仓策略 {now:%m-%d}",
        "body": "\n".join(lines),
        "count": len(positions),
    }


def build_position_status_summary(
    positions: list[dict],
    *,
    slot: str,
    now: datetime | None = None,
    live_refreshed: bool = True,
) -> dict:
    """Build the fixed 11:25/14:50 heartbeat without changing alert state."""
    from core.operation_plan import evaluate_operation_trigger

    now = now or datetime.now()
    titles = {
        "morning": "☀️ 上午持仓摘要",
        "late": "🎯 尾盘持仓确认",
    }
    refresh_text = "实时行情已刷新" if live_refreshed else "实时行情刷新异常，使用最近缓存价"
    lines = [
        f"监控{'正常' if live_refreshed else '在线'}｜{refresh_text} {now:%H:%M}",
        f"本次检查 {len(positions)} 只实盘持仓；本条为定时状态确认。",
        "",
    ]
    action_labels = {
        "STRUCTURE_EXIT": "退出复核",
        "REDUCE": "减仓/收紧风控",
        "CANCEL_ADD": "撤回加仓计划",
        "ADD_TRIGGER": "加仓复核（等待价量与收盘确认）",
        "HOLD": "持有观察（未触发新操作）",
    }
    visible = positions[:8]
    for item in visible:
        plan = item.get("plan") or {}
        entry = safe_float(plan.get("entry_price"))
        current = safe_float(plan.get("current_price"), entry)
        stop = safe_float(plan.get("active_stop_price"))
        guard = safe_float(plan.get("add_guard_price"))
        trigger_price = safe_float(plan.get("add_trigger_price"))
        pl_pct = (current - entry) / entry * 100 if entry > 0 else 0
        trigger = evaluate_operation_trigger(current, plan)
        kind = str(trigger.get("kind") or "HOLD")
        lines.extend([
            f"{item.get('name') or item.get('code')}({item.get('code')})",
            f"现价 {current:.2f}｜成本 {entry:.2f}｜{pl_pct:+.2f}%",
            f"状态：{action_labels.get(kind, str(trigger.get('action') or '持有观察'))}",
            f"防守 {stop:.2f}｜撤退 {guard:.2f}｜转强 {trigger_price:.2f}",
            "",
        ])
    if len(positions) > len(visible):
        lines.append(f"另有 {len(positions) - len(visible)} 只持仓请在系统内查看。")
    if slot == "late":
        lines.append("尾盘纪律：仅按已确认价位执行；未站稳转强线不加仓，跌破防守线优先风控。")
    lines.append("即时提醒仍在运行；后续仅在操作状态变化时另行推送。")
    return {
        "title": f"{titles[slot]} {now:%m-%d}",
        "body": "\n".join(lines),
        "count": len(positions),
    }


def _latest_breadth_line(engine) -> str | None:
    """读 breadth_history 最近一条有 NH-NL 数据的 MARKET 行，拼 Elder 宽度功课行。

    忽略尚无 NH-NL 统计的更近盘中快照行（盘前看昨天/前天的宽度是常态，
    Elder 功课表本就用上一交易日数据）；行内带日期标签表明数据新鲜度。"""
    try:
        row = pd.read_sql(text("""
            SELECT bar_date, nh_count, nl_count, pct_above_ma50
            FROM breadth_history
            WHERE scope = 'MARKET' AND nh_count IS NOT NULL
            ORDER BY bar_date DESC LIMIT 1
        """), engine)
    except Exception:
        return None
    if row.empty:
        return None
    record = row.iloc[0]
    nh, nl = int(record["nh_count"] or 0), int(record["nl_count"] or 0)
    parts = [f"Elder宽度({pd.Timestamp(record['bar_date']).strftime('%m-%d')}): 52周新高{nh}家/新低{nl}家"]
    if pd.notna(record.get("pct_above_ma50")):
        parts.append(f"MA50上占比{float(record['pct_above_ma50']):.0f}%")
    return " ".join(parts)


def _latest_limit_up_sentiment_line(engine) -> str | None:
    """读 breadth_history 最近一条有涨停情绪的 MARKET 行，拼盘前功课行。

    与宽度行同一策略：忽略尚无统计的更近盘中快照行，行内带日期标签表明
    数据新鲜度；炸板率达到退潮阈值时追加"降暴露"提示（仅 advisory，
    ZT_EBB_BROKEN_RATE_PCT 常量化于 risk_constants）。"""
    try:
        from core.market_regime import limit_up_sentiment_ebb

        row = pd.read_sql(text("""
            SELECT bar_date, zt_sealed_count, zt_broken_count, zt_max_streak,
                   zt_promotion_rate, zt_broken_rate
            FROM breadth_history
            WHERE scope = 'MARKET' AND zt_sealed_count IS NOT NULL
            ORDER BY bar_date DESC LIMIT 1
        """), engine)
    except Exception:
        return None
    if row.empty:
        return None
    record = row.iloc[0]
    stats = {
        "broken_rate": record.get("zt_broken_rate"),
    }
    parts = [
        f"涨停情绪({pd.Timestamp(record['bar_date']).strftime('%m-%d')}): "
        f"涨停{int(record['zt_sealed_count'] or 0)}家/炸板{int(record['zt_broken_count'] or 0)}家"
    ]
    if record.get("zt_max_streak"):
        parts.append(f"最高{int(record['zt_max_streak'])}板")
    if pd.notna(record.get("zt_promotion_rate")):
        parts.append(f"晋级率{float(record['zt_promotion_rate']):.0f}%")
    if pd.notna(record.get("zt_broken_rate")):
        parts.append(f"炸板率{float(record['zt_broken_rate']):.0f}%")
    if limit_up_sentiment_ebb(stats):
        parts.append("⚠️退潮警示，考虑降暴露")
    return " ".join(parts)


@celery_app.task(name="tasks.send_premarket_position_advice")
@daily_task_slot("premarket-position-advice", timeout_minutes=30)
def send_premarket_position_advice():
    """Send the trading-day 08:45 plan for REAL + OPEN positions."""
    now = datetime.now()
    if not is_a_share_trading_day(now):
        return {"status": "skipped", "reason": "non_trading_day"}
    try:
        from core.data import get_market_regime
        from routers.paper_trade import get_open_trade_plans

        payload = get_open_trade_plans()
        positions = [
            item for item in (payload.get("items") or [])
            if str(item.get("trade_mode") or "").upper() == "REAL"
        ]
        if not positions:
            return {"status": "skipped", "reason": "no_open_real_positions"}
        try:
            market = get_market_regime()
        except Exception as exc:
            logger.warning(f"Premarket regime unavailable: {exc}")
            market = None
        message = build_premarket_position_advice(positions, market=market, now=now)
        # Elder 每日功课第 11 项：盘前看一眼市场宽度（NH-NL/MA50 占比），只追加不改动原正文
        try:
            breadth_line = _latest_breadth_line(get_db_engine())
            if breadth_line:
                message["body"] = f"{message['body']}\n{breadth_line}"
        except Exception as exc:
            logger.debug(f"Premarket breadth line unavailable: {exc}")
        # 第 12 项：盘前看一眼涨停情绪（涨停/炸板/晋级率，退潮期提示降暴露）
        try:
            sentiment_line = _latest_limit_up_sentiment_line(get_db_engine())
            if sentiment_line:
                message["body"] = f"{message['body']}\n{sentiment_line}"
        except Exception as exc:
            logger.debug(f"Premarket sentiment line unavailable: {exc}")
        # 第 13 项：市场状态闸门（R3 十日动量，SHADOW 只提示不改行为）
        try:
            gate_line = _market_state_gate_line(get_db_engine())
            if gate_line:
                message["body"] = f"{message['body']}\n{gate_line}"
        except Exception as exc:
            logger.debug(f"Premarket gate line unavailable: {exc}")
        delivery = asyncio.run(notifier.send(
            message["title"],
            message["body"],
            channels=["bark"],
            group="AlphaVision_Position",
            url="http://localhost:3000",
        ))
        sent = bool(delivery.get("bark"))
        return {
            "status": "success" if sent else "queued",
            "bark": sent,
            "count": message["count"],
        }
    except Exception as exc:
        logger.error(f"Premarket position advice failed: {exc}")
        return {"status": "error", "detail": str(exc)}


@celery_app.task(name="tasks.send_position_status_summary")
@daily_task_slot("position-status-summary", slot_argument="slot", timeout_minutes=30)
def send_position_status_summary(slot: str):
    """Send one low-noise position heartbeat at 11:25 or 14:50."""
    if slot not in {"morning", "late"}:
        return {"status": "error", "reason": "invalid_slot", "slot": slot}
    now = datetime.now()
    if not is_a_share_intraday_session(now):
        return {"status": "skipped", "reason": "market_closed", "slot": slot}
    try:
        from routers.paper_trade import check_operation_triggers, get_open_trade_plans

        refresh = check_operation_triggers(notify=False, trade_mode="REAL")
        live_refreshed = refresh.get("status") == "success" and not refresh.get("reason")
        payload = get_open_trade_plans()
        positions = [
            item for item in (payload.get("items") or [])
            if str(item.get("trade_mode") or "").upper() == "REAL"
        ]
        if not positions:
            return {"status": "skipped", "reason": "no_open_real_positions", "slot": slot}
        message = build_position_status_summary(
            positions,
            slot=slot,
            now=now,
            live_refreshed=live_refreshed,
        )
        delivery = asyncio.run(notifier.send(
            message["title"],
            message["body"],
            channels=["bark"],
            group="AlphaVision_Position",
            url="http://localhost:3000",
        ))
        sent = bool(delivery.get("bark"))
        return {
            "status": "success" if sent else "queued",
            "bark": sent,
            "count": message["count"],
            "slot": slot,
            "live_refreshed": live_refreshed,
        }
    except Exception as exc:
        logger.error(f"Position status summary failed ({slot}): {exc}")
        return {"status": "error", "detail": str(exc), "slot": slot}


@celery_app.task(name="tasks.send_daily_ai_review")
@daily_task_slot("daily-ai-review", timeout_minutes=30)
def send_daily_ai_review():
    """After-close AI review of the day's top candidates; persists and pushes via Bark."""
    now = datetime.now()
    if not is_a_share_trading_day(now):
        return {"status": "skipped", "reason": "non_trading_day"}
    from core.config import config

    if not config.is_ai_analysis_configured():
        return {"status": "skipped", "reason": "ai_not_configured"}
    try:
        from core.ai_stock_analysis import analyze_strategy_candidates
        from core.daily_strategy_report import build_daily_strategy_report, build_daily_strategy_report_body
        from core.db import (
            get_scan_dates,
            get_scan_history_by_date,
            save_ai_candidate_reviews,
        )
        from core.stock_research import build_stock_research_signals

        dates = get_scan_dates()
        scan_date = dates[0] if dates else ""
        expected_date = now.strftime("%Y-%m-%d")
        if scan_date and scan_date != expected_date:
            return {
                "status": "skipped",
                "reason": "stale_scan_date",
                "scan_date": scan_date,
                "expected_date": expected_date,
            }
        results = get_scan_history_by_date(scan_date) if scan_date else []
        if not results:
            return {"status": "skipped", "reason": "no_scan_results", "scan_date": scan_date}

        def _candidate_rank(stock: dict):
            trade_eligible = bool(stock.get("trade_eligible")) and str(stock.get("trade_bucket") or "").upper() == "TRADE"
            score = float(
                stock.get("display_opportunity_score")
                or stock.get("final_trade_score")
                or stock.get("Score")
                or 0
            )
            return (0 if trade_eligible else 1, -score)

        candidates = sorted(results, key=_candidate_rank)[: config.AI_MAX_CANDIDATES]

        # 附上行业景气聚合（ROE/净利同比中位数），给AI复核板块级基本面参考
        from core.industry_prosperity import build_industry_prosperity

        prosperity_map = build_industry_prosperity(results)
        if prosperity_map:
            for stock in candidates:
                industry = str(stock.get("行业") or stock.get("industry") or "").strip()
                if prosperity_map.get(industry):
                    stock["industry_prosperity"] = prosperity_map[industry]

        # 预热研究快照（新闻/公告/龙虎榜），让 AI 复核读到舆情证据；超预算即止
        warmup_deadline = time.monotonic() + _DAILY_AI_RESEARCH_WARMUP_SECONDS
        warmed = 0
        for stock in candidates:
            if time.monotonic() > warmup_deadline:
                break
            code = str(stock.get("代码") or stock.get("code") or "").zfill(6)
            if not code.isdigit() or len(code) != 6:
                continue
            try:
                build_stock_research_signals(code, trade_date=scan_date, force_refresh=False)
                warmed += 1
            except Exception as exc:
                logger.warning(f"AI review research warmup failed for {code}: {exc}")

        review = analyze_strategy_candidates(candidates)
        if review.get("status") != "success":
            return {
                "status": "degraded",
                "reason": review.get("message"),
                "scan_date": scan_date,
                "warmed_research": warmed,
            }
        saved = save_ai_candidate_reviews(
            review["analyses"],
            review_date=scan_date,
            model=review.get("model") or "",
            market_summary=review.get("market_summary") or "",
            source="scheduled",
        )
        buy_count = sum(
            1 for item in review["analyses"] if str(item.get("action") or "").upper() == "BUY"
        )
        report = build_daily_strategy_report(
            results,
            scan_date=scan_date,
            ai_review={**review, "source": "scheduled"},
        )
        report_body = build_daily_strategy_report_body(report)
        if buy_count == 0:
            # 无BUY不再静默：照常推送观察版，附确定性市场概况，便于人工研判。
            report_body = (
                f"今日无BUY推荐（{len(review['analyses'])}只候选全部等待/回避）。\n{report_body}"
            )
            title = f"收盘AI复核 {scan_date}｜无BUY"
        else:
            title = f"收盘AI复核 {scan_date}"
        try:
            from core.data import get_market_regime
            market_desc = str(get_market_regime().get("desc") or "")
        except Exception:
            market_desc = ""
        if market_desc:
            report_body = f"市场：{market_desc}\n{report_body}"
        delivery = asyncio.run(notifier.send(
            title,
            report_body,
            channels=["bark"],
            group="AlphaVision_Report",
            url="http://localhost:3000",
        ))
        return {
            "status": "success" if delivery.get("bark") else "queued",
            "bark": bool(delivery.get("bark")),
            **({"reason": "no_buy_candidates"} if buy_count == 0 else {}),
            "scan_date": scan_date,
            "buy_count": buy_count,
            "candidates": len(review["analyses"]),
            "warmed_research": warmed,
            "batch_id": (saved or {}).get("batch_id"),
        }
    except Exception as exc:
        logger.error(f"Daily AI review failed: {exc}")
        return {"status": "error", "detail": str(exc)}


@celery_app.task(name="tasks.discover_event_catalysts")
@daily_task_slot("event-catalyst-discovery", timeout_minutes=60)
def discover_event_catalysts():
    try:
        from core.event_ingestion import discover_official_event_catalysts
        return discover_official_event_catalysts(get_db_engine())
    except Exception as exc:
        logger.error(f"Event catalyst discovery failed: {exc}")
        return {"error": str(exc)}


@celery_app.task(name="tasks.database_backup")
@daily_task_slot("database-backup", timeout_minutes=60)
def database_backup():
    """数据库备份：当日已有成功备份则跳过（幂等），失败推 Bark，支持第二副本目录。

    幂等性使备份可以在错过 20:30 调度后的任何时刻安全补跑（加一个 beat
    条目或手动触发均可），修复"错过单点调度 = 当日无备份"的单点问题。"""
    try:
        from core.database_backup import create_database_backup, today_backup_exists

        if today_backup_exists():
            logger.info("Database backup skipped: today's backup already exists.")
            return {"status": "ok", "skipped": True, "reason": "already_backed_up_today"}
        result = create_database_backup(retention=14)
        logger.info(f"Database backup done: {result.get('path')}")
        return {"status": "ok", **result}
    except Exception as exc:
        logger.error(f"Database backup failed: {exc}")
        try:
            asyncio.run(notifier.send(
                "🔴数据库备份失败",
                f"pg_dump 备份失败：{str(exc)[:300]}\n请检查 pg_dump 可用性/磁盘空间，业务数据当前无当日备份。",
                channels=["bark"], group="AlphaVision_Data",
            ))
        except Exception as push_exc:
            logger.warning(f"Backup failure bark push failed: {push_exc}")
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="tasks.expire_execution_intents")
@daily_task_slot("expire-execution-intents", timeout_minutes=15)
def expire_execution_intents():
    from core.execution_intents import expire_due_execution_intents
    expired = expire_due_execution_intents(get_db_engine())
    return {"status": "ok", "expired": expired}


def _is_limit_up_collection_window(now: datetime) -> bool:
    final_capture = is_a_share_trading_day(now) and now.hour == 15 and 0 <= now.minute <= 5
    return is_a_share_intraday_session(now) or final_capture


@celery_app.task(name="tasks.collect_limit_up_leadership")
def collect_limit_up_leadership():
    now = datetime.now()
    if not _is_limit_up_collection_window(now):
        return "Market closed"
    try:
        from core.limit_up_leadership import collect_limit_up_events

        return collect_limit_up_events(engine=get_db_engine(), collected_at=now)
    except Exception as exc:
        logger.error(f"Limit-up leadership collection failed: {exc}")
        return {"error": str(exc)}


@celery_app.task(name="tasks.collect_candidate_minute_bars")
def collect_candidate_minute_bars():
    now = datetime.now()
    if not is_a_share_intraday_session(now):
        return "Market closed"
    try:
        from core.limit_up_leadership import collect_candidate_minute_bars as collect

        return collect(engine=get_db_engine())
    except Exception as exc:
        logger.error(f"Candidate minute-bar collection failed: {exc}")
        return {"error": str(exc)}


@celery_app.task(name="tasks.theme_momentum_watch")
def theme_momentum_watch(slot: str = "morning"):
    now = datetime.now()
    if not is_a_share_intraday_session(now):
        logger.info(f"Theme momentum watch skipped: market closed ({slot}).")
        return {"bark": False, "count": 0, "reason": "market_closed", "slot": slot}
    try:
        from routers.watchlist import send_theme_momentum_alert

        # Research observations remain available in the app; Bark is reserved
        # for executable signals and position-risk actions.
        result = send_theme_momentum_alert(slot=slot, notify=False)
        logger.info(f"Theme momentum watch completed: {result}")
        return result
    except Exception as exc:
        logger.error(f"Theme momentum watch failed: {exc}")
        return {"bark": False, "count": 0, "reason": "error", "slot": slot, "detail": str(exc)}


@celery_app.task(name="tasks.retry_pending_notifications")
def retry_pending_notifications():
    """Retry durable notification failures without creating duplicate outbox rows."""
    from core.audit_log import load_due_notifications, record_notification_retry

    pending = load_due_notifications(limit=20)
    sent_count = 0
    for item in pending:
        try:
            from core.notifier import bark_body_too_large
            if item["channel"] == "bark" and bark_body_too_large(item["body"]):
                record_notification_retry(
                    item["id"], False, "payload_too_large", permanent=True,
                )
                continue
            result = asyncio.run(notifier.send(
                item["title"],
                item["body"],
                channels=[item["channel"]],
                url=item.get("url"),
                group=item.get("group_name"),
                is_archive=int(item.get("is_archive") or 1),
                enqueue_failed=False,
            ))
            sent = bool(result.get(item["channel"]))
            record_notification_retry(item["id"], sent, None if sent else "delivery_failed")
            sent_count += int(sent)
        except Exception as exc:
            record_notification_retry(item["id"], False, str(exc))
    return {"count": len(pending), "sent": sent_count, "failed": len(pending) - sent_count}


def _late_formal_scan_state(now: datetime | None = None) -> dict:
    """Return today's 14:50 scan state from durable task audit evidence."""
    now = now or datetime.now()
    engine = get_db_engine()
    if engine is None:
        return {"completed": False, "running": False, "reason": "db_unavailable"}
    try:
        with engine.connect() as conn:
            slot_status = conn.execute(text("""
                SELECT status FROM task_slot_claims WHERE slot_key = :slot_key
            """), {"slot_key": f"intraday-monitor:{now:%Y-%m-%d}:late_decision"}).scalar()
            if str(slot_status or "") == "RUNNING":
                return {"completed": False, "running": True, "reason": "still_running"}
            row = conn.execute(text("""
                SELECT status, result_summary
                FROM task_run_audits
                WHERE task_name = 'tasks.intraday_monitor_checkpoint'
                  AND CAST(COALESCE(finished_at, started_at) AS DATE) = :today
                  AND result_summary LIKE '%late_decision%'
                ORDER BY COALESCE(finished_at, started_at) DESC
                LIMIT 1
            """), {"today": now.date()}).mappings().first()
        if not row:
            return {"completed": False, "running": False, "reason": "missing_audit"}
        status = str(row.get("status") or "")
        if status in {"STARTED", "RECEIVED", "RETRY"}:
            return {"completed": False, "running": True, "reason": "still_running"}
        try:
            summary = json.loads(row.get("result_summary") or "{}")
        except (TypeError, json.JSONDecodeError):
            summary = {}
        completed = bool(summary.get("formal_scan_completed"))
        return {
            "completed": completed,
            "running": False,
            "reason": "completed" if completed else "incomplete",
            "status": status,
        }
    except Exception as exc:
        logger.warning(f"Late formal scan audit unavailable: {exc}")
        return {"completed": False, "running": False, "reason": "audit_unavailable"}


@celery_app.task(name="tasks.recover_late_formal_scan")
@daily_task_slot("late-formal-scan-recovery", timeout_minutes=30)
def recover_late_formal_scan():
    """Run one 14:55 compensation scan only when the 14:50 scan did not complete."""
    now = datetime.now()
    if not is_a_share_trading_day(now) or not is_a_share_intraday_session(now):
        return {"status": "skipped", "reason": "market_closed"}
    state = _late_formal_scan_state(now)
    if state.get("completed"):
        return {"status": "skipped", "reason": "late_scan_already_completed"}
    if state.get("running"):
        return {"status": "skipped", "reason": "late_scan_still_running"}
    logger.warning(f"14:50 formal scan incomplete ({state.get('reason')}); starting 14:55 recovery.")
    return intraday_monitor_checkpoint(slot="late_recovery")


def _alert_action(signal: dict, trade_mode: str, pl_pct: float) -> str:
    level = signal.get("level")
    reason = signal.get("reason", "")
    if level == "critical":
        if trade_mode == "REAL":
            return f"建议人工确认平仓；{reason}"
        return f"模拟仓建议平仓；{reason}"
    if pl_pct > 8:
        return f"建议减仓锁定利润；{reason}"
    return f"建议减仓或收紧风控；{reason}"


def _alert_event_key(reason: str, level: str) -> str:
    normalized = re.sub(r"¥\s*\d+(?:\.\d+)?", "¥#", str(reason or ""))
    normalized = re.sub(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?%", "#%", normalized)
    return f"{level}:{normalized}"


def _load_alert_state(code: str) -> dict:
    raw = get_setting(f"{_ALERT_STATE_KEY_PREFIX}{code}", "")
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _should_push_alert(code: str, reason: str, now: datetime, level: str = "warning") -> bool:
    event_key = _alert_event_key(reason, level)
    day = now.strftime("%Y-%m-%d")
    state = _load_alert_state(code)
    if state.get("day") == day and state.get("active") is True and state.get("event_key") == event_key:
        return False

    saved = save_setting(
        f"{_ALERT_STATE_KEY_PREFIX}{code}",
        json.dumps({
            "day": day,
            "active": True,
            "event_key": event_key,
            "updated_at": now.isoformat(timespec="seconds"),
        }, ensure_ascii=False),
    )
    if saved:
        return True

    # 持久化不可用时退回进程内冷却，避免数据库故障放大成推送风暴。
    key = f"{code}:{event_key}"
    last_ts = _ALERT_DEDUPE_CACHE.get(key)
    now_ts = now.timestamp()
    if last_ts and now_ts - last_ts < _ALERT_DEDUPE_SECONDS:
        return False
    _ALERT_DEDUPE_CACHE[key] = now_ts
    return True


def _mark_alert_recovered(code: str, now: datetime) -> None:
    state = _load_alert_state(code)
    if state.get("day") != now.strftime("%Y-%m-%d") or state.get("active") is not True:
        return
    state["active"] = False
    state["updated_at"] = now.isoformat(timespec="seconds")
    save_setting(
        f"{_ALERT_STATE_KEY_PREFIX}{code}",
        json.dumps(state, ensure_ascii=False),
    )


@celery_app.task(name="tasks.check_realtime_alerts")
def check_realtime_alerts():
    """
    实时监控任务：
    1. 获取持仓列表
    2. 获取实时行情
    3. 评估卖出信号
    4. 触发手机推送
    """
    now = datetime.now()
    if not is_a_share_intraday_session(now):
        logger.debug("Market is closed. Skipping real-time check.")
        return "Market closed"

    engine = get_db_engine()
    try:
        # 1. 获取所有 OPEN 持仓
        df_paper = pd.read_sql("SELECT * FROM paper_trading WHERE status = 'OPEN'", engine)
        if df_paper.empty:
            return "No open positions"

        # 2. 获取行情（持仓小名单走直连快报价，超限才退全市场快照）
        codes = df_paper['code'].unique().tolist()
        from core.data import get_fast_quotes, FAST_QUOTE_MAX_CODES
        snapshot = get_fast_quotes(codes) if len(codes) <= FAST_QUOTE_MAX_CODES else get_market_snapshot()
        if snapshot.empty:
            return "Failed to fetch snapshot"
        if is_snapshot_stale(snapshot):
            return "Failed to fetch fresh snapshot"

        snapshot_map = snapshot.set_index('code')['price'].to_dict()
        snapshot_high_map = snapshot.set_index('code')['high'].to_dict() if 'high' in snapshot.columns else {}

        # 在循环外部，仅抓取一次大盘基准指数历史 K 线（用于 RS 计算），彻底消除循环内 24 次冗余的网络请求
        from core.data import get_index_hist
        bench_df = get_index_hist("000001")

        alerts_triggered = []

        for _, row in df_paper.iterrows():
            code = row['code']
            name = row['name']
            entry_price = safe_float(row['entry_price'])
            high_since_entry = safe_float(row.get('high_since_entry'), entry_price)
            trade_mode = row.get('trade_mode', 'SIMULATED') or 'SIMULATED'
            
            curr_price = snapshot_map.get(code)
            if not curr_price: continue
            high_since_entry = max(high_since_entry, safe_float(snapshot_high_map.get(code), curr_price), curr_price)

            # 3. 拉取最近 K 线计算技术指标 (EMA, VolMA 等)
            # 这里复用 alert.py 的逻辑，但为了性能只取少量数据
            query = text("""
                SELECT date as "日期", close as "收盘", open as "开盘", 
                       high as "最高", low as "最低", vol as "成交量"
                FROM daily_k
                WHERE code = :code
                ORDER BY date DESC LIMIT 260
            """)
            with engine.connect() as conn:
                df_hist = pd.read_sql(query, conn, params={"code": code})
                df_hist = df_hist.sort_values("日期")
            
            if len(df_hist) < 20: continue

            # 注入实时价并传入大盘基准，避免内部隐式重复抓取
            df_labeled = calculate_indicators(df_hist, current_price=curr_price, bench_df=bench_df)
            
            # 4. 评估信号
            signals = evaluate_exit_signals(
                df_labeled,
                entry_price,
                high_since_entry,
                code=code,
                signal_sources=str(row.get("signal_sources") or "").split("+") if row.get("signal_sources") else None,
                close_confirmed=now.hour >= 15,
            )
            
            # 过滤出需要推送的信号 (warning 和 critical)
            important_signals = [s for s in signals if s['level'] in ['warning', 'critical']]
            if not important_signals:
                _mark_alert_recovered(code, now)
                continue

            sig = important_signals[0]
            reason = sig.get('reason', '')
            if not _should_push_alert(code, reason, now, level=sig.get('level', 'warning')):
                continue
            pl_pct = (curr_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
            mode_label = "实盘" if trade_mode == "REAL" else "模拟"
            action = _alert_action(sig, trade_mode, pl_pct)
            alerts_triggered.append(
                f"{name}({code}) [{mode_label}] {curr_price:.2f} ({pl_pct:+.2f}%)\n"
                f"动作：{action}\n"
                f"提示：{sig.get('suggestion', '')}"
            )

        # 5. 发送推送
        if alerts_triggered:
            title = f"⚠️ 盘中风控建议 ({len(alerts_triggered)}个)"
            # P1：body 末尾标注行情新鲜度（snapshot 已在作用域内），让用户知晓现价可信度
            from core.data import format_freshness
            freshness_tail = format_freshness(snapshot)
            # 为保证移动端通知显示美观，若多于 5 个预警，仅展示前 5 个并做优雅截断，避免消息堆叠
            if len(alerts_triggered) > 5:
                body = "\n".join(alerts_triggered[:5]) + f"\n... 等共 {len(alerts_triggered)} 个风控预警信号，请点击查看仪表板。"
            else:
                body = "\n".join(alerts_triggered)
            body = body + "\n" + freshness_tail
                
            # 这是一个异步操作，但不等待结果
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            delivery = loop.run_until_complete(notifier.send(
                title, 
                body, 
                channels=["bark"],
                group="AlphaVision_Alert",
                url="http://localhost:3000"  # 默认跳转到仪表板
            ))
            if delivery.get("bark"):
                logger.info(f"Sent {len(alerts_triggered)} alerts via Bark.")
            else:
                logger.warning(f"Bark delivery failed for {len(alerts_triggered)} alerts; queued for retry.")

        return f"Processed {len(df_paper)} positions, triggered {len(alerts_triggered)} alerts"

    except Exception as e:
        logger.error(f"Error in check_realtime_alerts task: {e}")
        return str(e)


@celery_app.task(name="tasks.check_position_operation_alerts")
def check_position_operation_alerts():
    """Check REAL positions every five minutes and notify only on state changes."""
    now = datetime.now()
    if not is_a_share_intraday_session(now):
        return {"status": "skipped", "reason": "market_closed"}
    try:
        from routers.paper_trade import check_operation_triggers

        return check_operation_triggers(notify=True, trade_mode="REAL")
    except Exception as exc:
        logger.error(f"Position operation alert failed: {exc}")
        return {"status": "error", "alerts": [], "detail": str(exc)}


@celery_app.task(name="tasks.intraday_monitor_checkpoint")
@daily_task_slot("intraday-monitor", slot_argument="slot", timeout_minutes=45)
def intraday_monitor_checkpoint(slot: str = "price_watch"):
    """Professional intraday workflow checkpoints for real trading operations."""
    now = datetime.now()
    if slot == "after_close_review" and not _is_after_close_review_window(now):
        logger.info("Checkpoint after_close_review skipped: stale task slot.")
        return {
            "status": "skipped",
            "reason": "stale_after_close_slot",
            "slot": slot,
        }
    if slot != "after_close_review" and not is_a_share_intraday_session(now):
        logger.info(f"Checkpoint {slot} skipped: market closed.")
        return "Market closed"

    periodic_strategy_scan = slot.startswith("strategy_scan_")
    noon_periodic_scan = slot == "strategy_scan_1300"
    candidate_periodic_scan = slot == "strategy_scan_1430"
    summary = {
        "slot": slot,
        "operation_alerts": 0,
        "watch_alerts": 0,
        "watch_status_push": 0,
        "formal_scan_count": 0,
        "formal_scan_push": 0,
        "formal_scan_completed": 0,
        "pruned": 0,
        "next_day_push": 0,
        "next_day_confirmation": {"reviewed": 0, "confirmed": 0, "bark": False},
        "next_day_reviewed": 0,
        "next_day_confirmed": 0,
        "next_day_confirmation_bark": 0,
        "bark_self_check": 0,
        "shadow_close_push": 0,
        "errors": [],
    }
    try:
        from core.bark_health import send_bark_self_check
        from routers.paper_trade import check_operation_triggers
        from routers.watchlist import (
            auto_prune_watchlist,
            check_watchlist_triggers,
            refresh_watchlist_decisions,
            send_late_watchlist_confirmations,
        )

        if slot == "open_risk":
            self_check = send_bark_self_check()
            summary["bark_self_check"] = 1 if (self_check.get("notification") or {}).get("bark") else 0

        if noon_periodic_scan or slot in {"open_risk", "morning_confirm", "late_decision", "price_watch"}:
            operation = check_operation_triggers(notify=True, trade_mode="REAL")
            summary["operation_alerts"] = len(operation.get("alerts") or [])

        if periodic_strategy_scan or slot in {"morning_confirm", "candidate_scan", "late_decision"}:
            watch = check_watchlist_triggers(
                notify=True,
                notify_target_hits=slot != "late_decision",
            )
            summary["watch_alerts"] = int(watch.get("count") or 0)
            summary["watch_status_push"] = int(bool((watch.get("notification") or {}).get("bark")))

        if periodic_strategy_scan or slot in {"morning_confirm", "candidate_scan", "late_decision", "late_recovery"}:
            try:
                from core.bark_scan_selection import run_bark_tv_observation_scan
                from core.sentinel import send_intraday_notification

                results = run_bark_tv_observation_scan(
                    local_only=True,
                    require_live_snapshot=True,
                ) or []
                summary["formal_scan_completed"] = 1
                summary["formal_scan_count"] = len(results)
                pushed = send_intraday_notification(results) if results else None
                summary["formal_scan_push"] = 1 if pushed else 0
                if periodic_strategy_scan and results:
                    try:
                        from core.db import get_setting
                        shadow_enabled = str(
                            get_setting("new_strategy_shadow_enabled", "true")
                        ).strip().lower() in {"1", "true", "yes", "on"}
                        if shadow_enabled:
                            from core.sentinel import run_new_strategy_shadow_cycle
                            run_new_strategy_shadow_cycle(results, notify=False)
                    except Exception as shadow_exc:
                        logger.warning(f"{slot} shadow scan skipped: {shadow_exc}")
                if slot == "morning_confirm":
                    from core.next_day_confirmation import send_next_day_confirmation
                    confirmation = send_next_day_confirmation(
                        get_db_engine(), results, now=now,
                    )
                    summary["next_day_confirmation"] = confirmation
                    summary["next_day_reviewed"] = int(confirmation.get("reviewed") or 0)
                    summary["next_day_confirmed"] = int(confirmation.get("confirmed") or 0)
                    summary["next_day_confirmation_bark"] = int(bool(confirmation.get("bark")))
            except Exception as scan_exc:
                logger.error(f"{slot} formal scan error: {scan_exc}")
                summary["errors"].append(f"formal_scan: {scan_exc}")

        if slot == "late_decision":
            late_confirmation = send_late_watchlist_confirmations(notify=True)
            summary["watch_status_push"] = max(
                summary["watch_status_push"],
                int(bool(late_confirmation.get("bark"))),
            )

        if candidate_periodic_scan or slot in {"candidate_scan", "after_close_review"}:
            refresh_watchlist_decisions()
            pruned = auto_prune_watchlist(max_watch_days=15)
            summary["pruned"] = int(pruned.get("updated") or 0)

        if slot == "after_close_review":
            from core.db import get_scan_history_by_date
            from core.sentinel import send_after_close_watchlist

            scan_date = now.strftime("%Y-%m-%d")
            scan_results = get_scan_history_by_date(scan_date)
            pushed_body = send_after_close_watchlist(scan_results, scan_date=scan_date, now=now)
            summary["next_day_push"] = 1 if pushed_body else 0
            if is_a_share_trading_day(now):
                try:
                    from core.db import get_setting
                    from core.sentinel import run_new_strategy_shadow_cycle

                    shadow_enabled = str(
                        get_setting("new_strategy_shadow_enabled", "true")
                    ).strip().lower() in {"1", "true", "yes", "on"}
                    tv_candidates = [
                        item
                        for item in scan_results
                        if item.get("strategy_type") == "tv_dual"
                    ]
                    if shadow_enabled:
                        shadow_body = run_new_strategy_shadow_cycle(
                            tv_candidates,
                            completed_day=True,
                            notify=False,
                        )
                        summary["shadow_close_push"] = int(bool(shadow_body))
                except Exception as exc:
                    logger.warning(f"New-strategy close shadow skipped: {exc}")
                    summary["errors"].append(f"shadow_close: {exc}")
            # The next-day watchlist now carries the compact close summary.
            # Keep the detailed strategy report in the app instead of sending
            # a second Bark message for the same close.
            summary["daily_report_push"] = 0

        logger.info(f"Intraday checkpoint completed: {summary}")
        return summary
    except Exception as exc:
        logger.error(f"Intraday checkpoint {slot} error: {exc}")
        return {"slot": slot, "error": str(exc)}


@celery_app.task(name="tasks.daily_sync")
@daily_task_slot("daily-sync", slot_argument="slot", timeout_minutes=180)
def daily_sync(slot: str = "晚上"):
    """
    按计划自动执行全市场数据同步：盘前 / 中午 / 晚上。
    """
    now = datetime.now()
    if not is_a_share_trading_day(now):
        logger.info("Non-trading day. Skipping scheduled market sync.")
        return "Non-trading day"

    from core.sync_state import sync_progress, sync_progress_lock
    with sync_progress_lock:
        if sync_progress.get("is_running"):
            logger.info(f"Scheduled market sync skipped ({slot}): sync already running.")
            return "Sync already running"

    from core.sync_scheduler import acquire_sync_lock, release_sync_lock
    if not acquire_sync_lock():
        logger.info(f"Scheduled market sync skipped ({slot}): another scheduler owns the lock.")
        return "Sync locked"

    logger.info(f"Starting scheduled market data sync ({slot})...")
    try:
        from routers.sync import background_sync_task
        # background_sync_task 会处理多源同步、重试和错误处理
        result = background_sync_task()
        return f"Scheduled sync completed successfully ({slot})"
    except Exception as e:
        logger.error(f"Error in scheduled daily_sync task: {e}")
        return str(e)
    finally:
        release_sync_lock()


def _scan_score(item: dict) -> float:
    for key in ("final_trade_score", "Score", "score", "sop_quality_score"):
        try:
            value = float(item.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value:
            return value
    return 0.0


def _scan_display_score(item: dict) -> float:
    try:
        return max(0.0, min(100.0, float(item.get("display_trade_score") or _scan_score(item))))
    except (TypeError, ValueError):
        return 0.0


def _noon_action_text(item: dict) -> str:
    action = str(item.get("sop_action") or item.get("pa_trade_action") or item.get("trade_bucket") or "WATCH").upper()
    bucket = str(item.get("trade_bucket") or "").upper()
    if bucket == "BLOCK" or action == "AVOID":
        return "排除/不买：风控或交易条件未通过，最多复盘观察"
    if action == "READY":
        return "待确认：可复核，不等于立即买入；需下午放量站稳确认价"
    if action == "WATCH":
        return "观察：等待回踩、放量或尾盘站稳，未确认前不买"
    if action == "WAIT":
        return "等待：方向未确认，先不操作"
    if action in {"TRADE", "PROBE"}:
        return "可交易候选：仍需确认价、量能和仓位约束，小仓复核"
    return f"{action}：按确认价和量能复核，禁止追高"


def _send_noon_scan_push(results: list[dict], limit: int = 5) -> bool:
    actionable = [
        item for item in results
        if item.get("trade_bucket") == "TRADE" and item.get("trade_eligible") is True
    ]
    reference_mode = not actionable
    pool = actionable
    market_desc = ""
    if reference_mode:
        # 仅当市场状态一刀切（CRITICAL）挡住全部候选时降级为参考版，
        # 其它原因导致的无可交易候选维持静默。
        from core.risk_constants import REGIME_REFERENCE_PUSH_ENABLED
        try:
            from core.data import get_market_regime
            regime = get_market_regime()
            market_desc = str(regime.get("desc") or "")
            market_gate_blocked = str(regime.get("status") or "").upper() == "CRITICAL"
        except Exception:
            market_gate_blocked = False
        if not (REGIME_REFERENCE_PUSH_ENABLED and market_gate_blocked):
            return False
        pool = [item for item in results if item.get("trade_bucket") != "BLOCK"]
        if not pool:
            return False

    selected = sorted(pool, key=_scan_score, reverse=True)[:limit]
    scan_date = selected[0].get("data_date") or datetime.now().strftime("%Y-%m-%d")
    lines = [f"午间全量同步后选股 {scan_date}"]
    if reference_mode:
        if market_desc:
            lines.append(f"市场：{market_desc}")
        lines.append("性质：市场风控禁新仓，以下仅策略信号参考，不可下单。")
    else:
        lines.append("性质：午间候选清单，下午需结合放量站稳和市场情绪复核。")
    lines.append("")
    for item in selected:
        code = item.get("代码") or item.get("code") or ""
        name = item.get("名称") or item.get("name") or ""
        price = item.get("现价") or item.get("current_price") or item.get("price") or "--"
        score = _scan_display_score(item)
        bucket = item.get("trade_bucket") or "OBSERVE"
        lines.append(f"{name}({code}) 现价 {price} | 评分 {score:.1f} | {bucket}")
        if item.get("bark_selection_source_label"):
            lines.append(f"  候选来源：{item['bark_selection_source_label']}")
        lines.append(f"  建议：{_noon_action_text(item)}")
        logic_line = build_logic_chain_line(item)
        if logic_line:
            lines.append(f"  {logic_line}")
        capital_line = build_capital_evidence_line(item)
        if capital_line:
            lines.append(f"  {capital_line}")
        if reference_mode:
            blockers = item.get("trade_blockers") or []
            if isinstance(blockers, str):
                blockers = [blockers]
            if blockers:
                lines.append(f"  风控：{'、'.join(str(b) for b in blockers[:2])}")
        if item.get("early_trade_candidate") and item.get("early_trade_reason"):
            lines.append(f"  提前复核：{item['early_trade_reason']}；仅小仓，不追高")
        if item.get("observe_promotion_candidate") and item.get("observe_promotion_action"):
            lines.append(f"  转可买：{item['observe_promotion_action']}")
        if item.get("pa_decision_summary") or item.get("price_action_summary"):
            lines.append(f"  结构：{item.get('pa_decision_summary') or item.get('price_action_summary')}")
        lines.append("")

    try:
        from core.data import get_stale_cache, format_freshness
        lines.append(format_freshness(get_stale_cache("market_snapshot")))
    except Exception:
        pass

    body = "\n".join(lines).rstrip()
    title = (
        f"Alpha Vision 午间参考 {scan_date}"
        if reference_mode
        else f"Alpha Vision 午间选股 {scan_date}"
    )
    try:
        delivery = asyncio.run(notifier.send(
            title,
            body,
            channels=["bark"],
            group="AlphaVision_Noon",
            url="http://localhost:3000",
        ))
        return bool(delivery.get("bark"))
    except Exception as exc:
        logger.error(f"Noon scan Bark push failed: {exc}")
        return False


@celery_app.task(name="tasks.noon_sync_scan_review")
@daily_task_slot("noon-sync-scan-review", slot_argument="sync_first", timeout_minutes=180)
def noon_sync_scan_review(sync_first: bool = True, run_review: bool = True):
    """Lunch workflow: sync at 11:35, then run live review after the afternoon session resumes."""
    now = datetime.now()
    if not is_a_share_trading_day(now):
        logger.info("Noon workflow skipped: non-trading day.")
        return {"status": "skipped", "reason": "non_trading_day"}

    summary = {
        "slot": "noon",
        "sync": None,
        "scan_count": 0,
        "scan_push": False,
        "operation_alerts": 0,
        "watch_alerts": 0,
        "watch_status_push": 0,
        "errors": [],
    }
    try:
        if sync_first:
            sync_result = daily_sync(slot="午间")
            summary["sync"] = sync_result
            if "completed successfully" not in str(sync_result):
                logger.warning(f"Noon workflow stopped before review: {sync_result}")
                return summary
        if not run_review:
            logger.info(f"Noon sync workflow completed: {summary}")
            return summary

        from core.bark_scan_selection import run_bark_tv_observation_scan
        try:
            results = run_bark_tv_observation_scan(
                local_only=True,
                require_live_snapshot=True,
            ) or []
            summary["scan_count"] = len(results)
            summary["scan_push"] = _send_noon_scan_push(results)
        except Exception as scan_exc:
            logger.error(f"Noon scan step error: {scan_exc}")
            summary["errors"].append(f"scan: {scan_exc}")

        from routers.paper_trade import check_operation_triggers
        from routers.watchlist import check_watchlist_triggers

        try:
            operation = check_operation_triggers(notify=True, trade_mode="REAL")
            summary["operation_alerts"] = len(operation.get("alerts") or [])
        except Exception as operation_exc:
            logger.error(f"Noon operation trigger step error: {operation_exc}")
            summary["errors"].append(f"operation: {operation_exc}")
        try:
            watch = check_watchlist_triggers(notify=True)
            summary["watch_alerts"] = int(watch.get("count") or 0)
            summary["watch_status_push"] = int(bool((watch.get("notification") or {}).get("bark")))
        except Exception as watch_exc:
            logger.error(f"Noon watch trigger step error: {watch_exc}")
            summary["errors"].append(f"watch: {watch_exc}")
        logger.info(f"Noon workflow completed: {summary}")
        return summary
    except Exception as exc:
        logger.error(f"Noon workflow error: {exc}")
        summary["error"] = str(exc)
        return summary


@celery_app.task(name="tasks.early_value_scan")
@daily_task_slot("early-value-scan", timeout_minutes=45)
def early_value_scan():
    """Run the independent early-value watch strategy without changing the main scan."""
    now = datetime.now()
    slot = "13:15"
    skip_reason = _scheduled_scan_skip_reason(now, slot)
    if skip_reason:
        return {
            "status": "skipped",
            "reason": skip_reason,
            "strategy_type": "early_value",
            "slot": slot,
        }
    try:
        from routers.scan import run_market_scan_task

        results = run_market_scan_task(
            strategy_type="early_value",
            local_only=True,
            require_live_snapshot=True,
        ) or []
        return {"status": "ok", "strategy_type": "early_value", "scan_count": len(results)}
    except Exception as exc:
        logger.error(f"Early-value independent scan failed: {exc}")
        return {
            "status": "error",
            "strategy_type": "early_value",
            "scan_count": 0,
            "error": str(exc),
        }


@celery_app.task(name="tasks.bottom_discovery_scan")
@daily_task_slot("bottom-discovery-scan", slot_argument="slot", timeout_minutes=45)
def bottom_discovery_scan(slot: str = "scheduled"):
    """Run the independent bottom-discovery research strategy and push observation-only Bark."""
    now = datetime.now()
    skip_reason = _scheduled_scan_skip_reason(now, slot)
    if skip_reason:
        return {
            "status": "skipped",
            "reason": skip_reason,
            "strategy_type": "bottom_discovery",
            "slot": slot,
        }
    try:
        from routers.scan import run_market_scan_task
        from core.sentinel import send_intraday_notification

        results = run_market_scan_task(
            strategy_type="bottom_discovery",
            local_only=True,
            require_live_snapshot=True,
            turnover_min=0.5,
            min_data_days=80,
        ) or []
        pushed = send_intraday_notification(results) if results else None
        return {
            "status": "ok",
            "strategy_type": "bottom_discovery",
            "slot": slot,
            "scan_count": len(results),
            "bark_status": "sent" if pushed else "none",
        }
    except Exception as exc:
        logger.error(f"Bottom-discovery independent scan failed: {exc}")
        return {
            "status": "error",
            "strategy_type": "bottom_discovery",
            "slot": slot,
            "scan_count": 0,
            "error": str(exc),
        }


@celery_app.task(name="tasks.weekly_entry_timing_report")
def weekly_entry_timing_report():
    """每周一 09:00 生成"买入时点周报"，对比尾盘买 vs 次日开盘买的胜率。

    取近 30 天推送过的票，跑 backtest_lab 两种 entry_mode，聚合后推送 bark。
    """
    try:
        from core.entry_timing_report import build_entry_timing_report
        report = build_entry_timing_report(days=30, max_codes=50)
        body = report.get("body", "")
        meta = report.get("meta", {})

        # 无有效样本时静默跳过（不发空报告）
        if meta.get("effective_size", 0) == 0:
            logger.info("Weekly entry timing report skipped: no effective samples")
            return {"bark": False, "reason": "no_effective_samples", "meta": meta}

        title = f"Alpha Vision 买入时点周报 {datetime.now().strftime('%m-%d')}"
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        delivery = loop.run_until_complete(notifier.send(
            title, body,
            channels=["bark"],
            group="AlphaVision_Report",
            url="http://localhost:3000"
        ))
        bark_sent = bool(delivery.get("bark"))
        logger.info(
            f"Weekly entry timing report {'sent' if bark_sent else 'queued for retry'}: "
            f"{meta.get('effective_size')} stocks"
        )
        return {"bark": bark_sent, "meta": meta, "aggregate": report.get("aggregate", {})}
    except Exception as e:
        logger.error(f"Error in weekly_entry_timing_report task: {e}")
        return {"bark": False, "error": str(e)}


def _collect_post_exit_review_items(
    engine,
    now=None,
    min_days_closed: int = 60,
    max_items: int = 20,
) -> list:
    """取平仓超过 min_days_closed 天且尚未写过退出后回顾日志的平仓交易。

    Elder《以交易为生》：每笔交易退出两个月后重新审视，检验退出决策。
    去重依据：trade_journal_events 中 source='post_exit_review' 且 trade_id 匹配。
    """
    cutoff = (pd.Timestamp(now or datetime.now()) - pd.Timedelta(days=int(min_days_closed))).strftime("%Y-%m-%d")
    closed = pd.read_sql(
        text("""
            SELECT id, code, name, entry_price, close_price, shares, entry_date, close_date, strategy_type
            FROM paper_trading
            WHERE status = 'CLOSED' AND close_date <= :cutoff
            ORDER BY close_date DESC LIMIT :max_items
        """),
        engine,
        params={"cutoff": cutoff, "max_items": int(max_items)},
    )
    if closed.empty:
        return []
    reviewed = pd.read_sql(
        text("SELECT DISTINCT trade_id FROM trade_journal_events WHERE source = 'post_exit_review'"),
        engine,
    )
    reviewed_ids = set(pd.to_numeric(reviewed["trade_id"], errors="coerce").dropna().astype(int)) if not reviewed.empty else set()
    return [row for row in closed.to_dict("records") if int(row["id"]) not in reviewed_ids]


@celery_app.task(name="tasks.post_exit_monthly_review")
def post_exit_monthly_review():
    """Elder 退出后月度回顾：回顾两个月前平仓的交易，写交易日志并推送 Bark 摘要。"""
    try:
        from routers.paper_trade import _ensure_trade_journal_table

        engine = get_db_engine()
        if not engine:
            return {"reviewed": 0, "reason": "no_db"}
        _ensure_trade_journal_table(engine)
        items = _collect_post_exit_review_items(engine)
        if not items:
            return {"reviewed": 0, "reason": "nothing_to_review"}

        now = datetime.now()
        lines = []
        with engine.begin() as conn:
            for row in items:
                entry_price = float(row.get("entry_price") or 0)
                close_price = float(row.get("close_price") or 0)
                pnl_pct = ((close_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
                hold_days = ""
                close_date = pd.to_datetime(row.get("close_date"), errors="coerce")
                entry_date = pd.to_datetime(row.get("entry_date"), errors="coerce")
                if pd.notna(close_date) and pd.notna(entry_date):
                    hold_days = f"，持有 {(close_date - entry_date).days} 天"
                note = f"收益 {pnl_pct:+.1f}%{hold_days}"
                lines.append(f"{row.get('code')} {row.get('name') or ''} {note}")
                conn.execute(
                    text("""
                        INSERT INTO trade_journal_events
                            (event_time, source, code, name, trade_id, advice, action_taken, result_note)
                        VALUES
                            (:event_time, 'post_exit_review', :code, :name, :trade_id,
                             :advice, :action_taken, :result_note)
                    """),
                    {
                        "event_time": now,
                        "code": str(row.get("code") or ""),
                        "name": row.get("name"),
                        "trade_id": int(row["id"]),
                        "advice": "退出后回顾：当时退出是否正确？若继续持有会怎样？",
                        "action_taken": f"策略 {row.get('strategy_type') or 'unknown'} 已平仓复盘",
                        "result_note": note,
                    },
                )

        title = f"Alpha Vision 退出后回顾 {now.strftime('%Y-%m')}"
        body = "平仓满两个月，重新审视退出决策（Elder 月度功课）：\n" + "\n".join(lines)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        delivery = loop.run_until_complete(notifier.send(title, body, channels=["bark"], group="AlphaVision_Report"))
        logger.info(f"Post-exit monthly review processed {len(items)} trades, bark={bool(delivery.get('bark'))}")
        return {"reviewed": len(items), "bark": bool(delivery.get("bark"))}
    except Exception as e:
        logger.error(f"Error in post_exit_monthly_review task: {e}")
        return {"reviewed": 0, "error": str(e)}


@celery_app.task(name="tasks.update_market_breadth_extremes")
def update_market_breadth_extremes():
    """Elder NH-NL 宽度指标（盘后）：全市场 250 日新高/新低家数 + MA50 上方占比。

    直接读 daily_k 逐票滚动统计，以数据实际最后交易日为基准（未同步到今日时
    不会错标今日），upsert 进 breadth_history 当日 MARKET 行。
    """
    try:
        from core.db import get_db_engine, record_breadth_extremes
        from core.market_regime import compute_market_breadth_extremes

        engine = get_db_engine()
        if not engine:
            return {"written": False, "reason": "no_db"}
        stats = compute_market_breadth_extremes(engine)
        written = record_breadth_extremes(stats, engine)
        summary = {k: stats.get(k) for k in ("bar_date", "nh_count", "nl_count", "pct_above_ma50", "total_count")}
        logger.info(f"Market breadth extremes task done: {summary}")
        return {"written": written, "stats": summary}
    except Exception as e:
        logger.error(f"Error in update_market_breadth_extremes task: {e}")
        return {"written": False, "error": str(e)}


@celery_app.task(name="tasks.update_limit_up_sentiment")
def update_limit_up_sentiment():
    """涨停情绪周期（盘后）：从 limit_up_events 聚合涨停/炸板/晋级率/炸板率。

    依赖 15:01 collect_limit_up_leadership 先写入当日最终事件；聚合结果 upsert
    进 breadth_history 当日 MARKET 行。炸板率达到退潮阈值时打 warning 日志供
    排查（盘前功课行会带"降暴露"提示；当前仅 advisory，不改仓位）。
    """
    try:
        from core.db import get_db_engine, record_limit_up_sentiment
        from core.market_regime import compute_limit_up_sentiment, limit_up_sentiment_ebb

        engine = get_db_engine()
        if not engine:
            return {"written": False, "reason": "no_db"}
        stats = compute_limit_up_sentiment(engine)
        written = record_limit_up_sentiment(stats, engine)
        ebb = limit_up_sentiment_ebb(stats)
        if ebb:
            logger.warning(
                f"涨停情绪退潮警示: 炸板率 {stats.get('broken_rate')}% >= 阈值 "
                f"(bar_date={stats.get('bar_date')})"
            )
        summary = {k: stats.get(k) for k in (
            "bar_date", "prev_bar_date", "sealed_count", "broken_count",
            "max_streak", "promotion_rate", "broken_rate",
        )}
        logger.info(f"Limit-up sentiment task done: {summary}, ebb={ebb}")
        return {"written": written, "stats": summary, "ebb": ebb}
    except Exception as e:
        logger.error(f"Error in update_limit_up_sentiment task: {e}")
        return {"written": False, "error": str(e)}


@celery_app.task(name="tasks.update_sector_fund_flow")
def update_sector_fund_flow():
    """行业资金流排名（午间/盘后各一次）：东财行业主力净流入排名落 breadth_history。

    写 SECTOR 行的 fund_flow_rank/main_force_net 两列，供候选证据门的
    sector_context 判断"独立走强 vs 板块共振"。接口失败 fail-open。
    节假日闸门：非交易日东财"今日"返回上一交易日数据但会被标成今天的
    bar_date，制造越界最新行——直接跳过。
    """
    try:
        if not is_a_share_trading_day(datetime.now()):
            return {"status": "skipped", "reason": "non_trading_day"}
        from core.sector_fund_flow import collect_sector_fund_flow_rank

        result = collect_sector_fund_flow_rank()
        logger.info(f"Sector fund flow task done: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in update_sector_fund_flow task: {e}")
        return {"saved": 0, "errors": 1, "error": str(e)}


@celery_app.task(name="tasks.update_lhb_records")
def update_lhb_records(days: int = 3):
    """龙虎榜日度记录（盘后）：最近交易日全市场龙虎榜落 lhb_records。

    供 ReviewCenter 查持仓/候选股"近 3 日是否上榜、净买多少"，只读参考。
    """
    try:
        from core.lhb_records import collect_lhb_records

        result = collect_lhb_records(days=int(days))
        logger.info(f"LHB records task done: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in update_lhb_records task: {e}")
        return {"saved": 0, "errors": 1, "error": str(e)}


# ── 数据新鲜度看门狗（改动：data-freshness-watchdog-v2）──────────────────────
# 全链路"失败只 log 不抛"的安全契约让单条数据链路断流可数周无人发现（数据源
# 改版、接口限流、进程故障都会静默停更）。本看门狗盘后交叉比对各链路的最新
# 日期：以 daily_k 为市场数据心跳基准（节假日两端同时停更，天然鲁棒），其余
# 链路落后基准超容差即视为断流并推送一条 Bark 汇总。
# v2：daily_k 心跳滞后改用**交易日差**（akshare 官方日历）度量——v1 的 5 个
# 自然日容忍窗覆盖不了春节 8 天长假，假期内每个工作日都会误报。

FRESHNESS_MIN_MISSED_SESSIONS = 1
# 官方日历加载失败时的自然日兜底容忍（>10 天才告警，宁漏报勿误报）
FRESHNESS_HEARTBEAT_LAG_DAYS_FALLBACK = 10


def _heartbeat_lag_sessions(reference, now: datetime) -> Optional[int]:
    """reference（最后同步日）之后到 now 之间错过的 A 股交易日数；日历不可用返回 None。"""
    from core.trading_calendar import is_a_share_trading_day

    try:
        day = reference + timedelta(days=1)
        sessions = 0
        # 上限 30 天，防日历异常导致死循环
        for _ in range(30):
            if day > now.date():
                break
            if is_a_share_trading_day(datetime.combine(day, datetime_time(12, 0))):
                sessions += 1
            day += timedelta(days=1)
        return sessions
    except Exception:
        return None


def compute_data_freshness(engine, now: Optional[datetime] = None) -> Dict[str, Any]:
    """比对各数据链路最新日期与 daily_k 心跳，返回 {stale: [...], reference_date}。"""
    now = now or datetime.now()
    result: Dict[str, Any] = {"reference_date": None, "heartbeat_lag_days": None, "stale": []}
    if engine is None:
        return result
    try:
        with engine.connect() as conn:
            ref = conn.execute(text("SELECT MAX(date) FROM daily_k")).scalar()
            probes = [
                ("涨停/炸板事件(limit_up_events)", "SELECT MAX(event_date) FROM limit_up_events", 1),
                ("市场宽度(breadth_history)", "SELECT MAX(bar_date) FROM breadth_history WHERE scope='MARKET' AND advance_ratio IS NOT NULL", 1),
                ("涨停情绪(zt_sentiment)", "SELECT MAX(bar_date) FROM breadth_history WHERE scope='MARKET' AND zt_sealed_count IS NOT NULL", 1),
                ("行业资金流(fund_flow_rank)", "SELECT MAX(bar_date) FROM breadth_history WHERE scope='SECTOR' AND fund_flow_rank IS NOT NULL", 2),
                ("龙虎榜(lhb_records)", "SELECT MAX(event_date) FROM lhb_records", 2),
            ]
    except Exception as exc:
        logger.warning(f"compute_data_freshness: probe query failed: {exc}")
        result["stale"].append(f"daily_k 心跳查询失败: {exc}")
        return result

    if ref is None:
        result["stale"].append("daily_k 为空（同步从未成功）")
        return result
    ref_date = pd.Timestamp(ref).date() if not isinstance(ref, datetime) else ref.date()
    result["reference_date"] = ref_date.isoformat()
    result["heartbeat_lag_days"] = (now.date() - ref_date).days
    lag_sessions = _heartbeat_lag_sessions(ref_date, now)
    result["heartbeat_lag_sessions"] = lag_sessions
    if lag_sessions is None:
        # 官方日历不可用：退回自然日口径（放宽到 10 天，宁漏报勿在长假误报）
        if result["heartbeat_lag_days"] > FRESHNESS_HEARTBEAT_LAG_DAYS_FALLBACK:
            result["stale"].append(
                f"daily_k 心跳滞后 {result['heartbeat_lag_days']} 个自然日（最后 {ref_date}，交易日历不可用）——全市场同步可能断流"
            )
    elif lag_sessions >= FRESHNESS_MIN_MISSED_SESSIONS:
        result["stale"].append(
            f"daily_k 心跳已错过 {lag_sessions} 个交易日（最后 {ref_date}）——全市场同步可能断流"
        )
    for name, query, tolerance in probes:
        try:
            with engine.connect() as conn:
                latest = conn.execute(text(query)).scalar()
        except Exception as exc:
            result["stale"].append(f"{name}: 查询失败 {exc}")
            continue
        if latest is None:
            # 链路从未产出过数据：涨停/宽度属核心链路，其余（资金流/龙虎榜）
            # 可能是新增功能尚未到首次运行时间，不告警
            if "limit_up_events" in query or "advance_ratio" in query:
                result["stale"].append(f"{name}: 从未写入")
            continue
        latest_date = pd.Timestamp(latest).date() if not isinstance(latest, datetime) else latest.date()
        lag = (ref_date - latest_date).days
        if lag > tolerance:
            result["stale"].append(f"{name}: 落后行情基准 {lag} 天（最后 {latest_date}）")
    return result


_DATA_FRESHNESS_ALERT_DATE: Dict[str, str] = {}


@celery_app.task(name="tasks.check_data_freshness")
def check_data_freshness():
    """盘后看门狗：任何数据链路静默断流时推送一条 Bark 汇总（每日最多一次）。"""
    now = datetime.now()
    try:
        engine = get_db_engine()
        if not engine:
            return {"status": "skipped", "reason": "no_db"}
        report = compute_data_freshness(engine, now)
        if not report["stale"]:
            logger.info(f"Data freshness check ok: ref={report['reference_date']}")
            return {"status": "ok", "reference_date": report["reference_date"]}
        body = "以下数据链路疑似断流，请检查同步/采集任务：\n" + "\n".join(
            f"· {item}" for item in report["stale"]
        )
        title = f"⚠️数据链路告警（基准 {report['reference_date']}）"
        trading_date = now.strftime("%Y-%m-%d")
        sent = False
        if _DATA_FRESHNESS_ALERT_DATE.get("watchdog") != trading_date:
            delivery = asyncio.run(notifier.send(
                title, body, channels=["bark"], group="AlphaVision_Data",
            ))
            sent = bool(delivery.get("bark"))
            # 送达成功才记去重日期：发送失败（notifier 会转 outbox 重试）时不
            # 标记，避免"告警没发出去却被记成已发"的静默丢失
            if sent:
                _DATA_FRESHNESS_ALERT_DATE["watchdog"] = trading_date
        logger.warning(f"Data freshness stale: {report['stale']}, bark={sent}")
        return {"status": "stale", "stale": report["stale"], "bark": sent}
    except Exception as e:
        logger.error(f"Error in check_data_freshness task: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task(name="tasks.weekly_data_maintenance")
def weekly_data_maintenance():
    """每周数据维护（周六 04:00）：按保留窗口清理非业务高增长表。

    只清审计/快照/分钟bar 类表（白名单见 db.purge_expired_data），绝不触碰
    paper_trades/daily_k/scan_history。分批 DELETE 防长事务；失败推送 Bark。
    """
    try:
        from core.db import purge_expired_data

        report = purge_expired_data()
        logger.info(f"Weekly data maintenance done: {report}")
        return {"status": "ok", "purged": report.get("purged", {})}
    except Exception as e:
        logger.error(f"Weekly data maintenance failed: {e}")
        try:
            asyncio.run(notifier.send(
                "⚠️数据维护任务失败",
                f"容量清理任务异常：{str(e)[:200]}（各表清理为独立事务，部分可能已完成）",
                channels=["bark"], group="AlphaVision_Data",
            ))
        except Exception:
            pass
        return {"status": "error", "error": str(e)}


# ── Redis 健康看门（redis-health-watch-v1）────────────────────────────────────
# task_always_eager 只在 celery_app import 时判定一次：Redis 运行中宕机后，
# 所有 .delay() 入死 broker——分钟级采集/风控告警/每日备份静默丢失且无错误日志。
# 本任务每 5 分钟探测一次，仅在状态翻转时推送 Bark（避免刷屏），把"静默丢失"
# 变成"分钟级可知"。

_REDIS_HEALTH_STATE: Optional[str] = None  # "up" / "down"


@celery_app.task(name="tasks.redis_health_watch")
def redis_health_watch():
    global _REDIS_HEALTH_STATE
    try:
        import os as _os

        import redis as _redis

        client = _redis.Redis.from_url(
            _os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=2, socket_timeout=2,
        )
        alive = bool(client.ping())
    except Exception:
        alive = False

    previous = _REDIS_HEALTH_STATE
    _REDIS_HEALTH_STATE = "up" if alive else "down"
    if previous == _REDIS_HEALTH_STATE:
        return {"status": _REDIS_HEALTH_STATE, "transition": False}

    if not alive:
        title = "🔴Redis 不可达"
        body = (
            "Redis 连接失败：Celery 定时任务（分钟级采集/风控告警/每日备份）将静默丢失，"
            "直到恢复为止。请检查 redis 服务状态。恢复后会再推送一条。"
        )
    else:
        title = "🟢Redis 已恢复"
        body = "Redis 连接恢复，定时任务恢复正常调度。宕机期间错过的任务不会自动补跑，建议核对数据完整性。"
    try:
        asyncio.run(notifier.send(title, body, channels=["bark"], group="AlphaVision_Data"))
    except Exception as exc:
        logger.warning(f"Redis health bark push failed: {exc}")
    logger.warning(f"Redis health transition: {previous} -> {_REDIS_HEALTH_STATE}")
    return {"status": _REDIS_HEALTH_STATE, "transition": True, "previous": previous}


def _market_state_gate_line(engine) -> str | None:
    """盘前功课第 13 行：市场状态闸门（R3 动量，SHADOW 只提示不改行为）。"""
    try:
        from core.market_regime import compute_market_state_gate

        gate = compute_market_state_gate(engine)
    except Exception:
        return None
    if not gate.get("bar_date") or gate.get("mom_10d_pct") is None:
        return None
    mark = "⚠️建议暂停新开仓" if gate.get("blocked") else "正常"
    return (
        f"市场状态闸门({pd.Timestamp(gate['bar_date']).strftime('%m-%d')}): "
        f"10日动量{gate['mom_10d_pct']:+.1f}% [{mark}]（SHADOW 观察中）"
    )


@celery_app.task(name="tasks.update_market_state_gate")
def update_market_state_gate():
    """市场状态闸门（SHADOW）：每日记录 R3 动量状态到 system_setting。

    记录格式 market_state_gate:YYYY-MM-DD = {mom_10d_pct, blocked, ...}，
    为 promotion_allowed 的 SHADOW 实盘期积累逐日对照样本（只记录，不改行为）。
    """
    try:
        import json as _json

        from core.db import save_setting
        from core.market_regime import compute_market_state_gate

        engine = get_db_engine()
        if not engine:
            return {"status": "skipped", "reason": "no_db"}
        gate = compute_market_state_gate(engine)
        if not gate.get("bar_date"):
            return {"status": "skipped", "reason": "no_data"}
        trading_date = datetime.now().strftime("%Y-%m-%d")
        save_setting(f"market_state_gate:{trading_date}", _json.dumps(gate, ensure_ascii=False))
        logger.info(f"Market state gate recorded: {gate}")
        return {"status": "ok", **gate}
    except Exception as e:
        logger.error(f"Error in update_market_state_gate task: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task(name="tasks.update_theme_heat")
def update_theme_heat(scope: str = "CONCEPT"):
    """题材热度榜（借鉴 easy-stock 主题热点页）：午间/盘后各一次，CONCEPT+INDUSTRY。

    热度为规则化研究评分（SHADOW），不进交易资格。接口失败逐项 fail-open。
    """
    try:
        from core.theme_heat import collect_theme_heat

        results = {}
        for scope_name in ("CONCEPT", "INDUSTRY"):
            try:
                results[scope_name] = collect_theme_heat(scope=scope_name)
            except Exception as exc:
                logger.warning(f"theme heat {scope_name} failed: {exc}")
                results[scope_name] = {"saved": 0, "error": str(exc)[:120]}
        logger.info(f"Theme heat task done: {results}")
        return {"status": "ok", "results": results}
    except Exception as e:
        logger.error(f"Error in update_theme_heat task: {e}")
        return {"status": "error", "error": str(e)}
