from .celery_app import celery_app
from .logging_config import logger
from .notifier import notifier
from .db import get_db_engine
from .data import get_market_snapshot, is_snapshot_stale
from .indicators import calculate_indicators
from core.strategy import evaluate_exit_signals
from core.risk_engine import safe_float
from core.trading_calendar import is_a_share_intraday_session, is_a_share_trading_day
import asyncio
import pandas as pd
from datetime import datetime
from sqlalchemy import text

_ALERT_DEDUPE_CACHE = {}
_ALERT_DEDUPE_SECONDS = 30 * 60


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

        result = send_theme_momentum_alert(slot=slot, notify=True)
        logger.info(f"Theme momentum watch completed: {result}")
        return result
    except Exception as exc:
        logger.error(f"Theme momentum watch failed: {exc}")
        return {"bark": False, "count": 0, "reason": "error", "slot": slot, "detail": str(exc)}


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


def _should_push_alert(code: str, reason: str, now: datetime) -> bool:
    key = f"{code}:{reason}"
    last_ts = _ALERT_DEDUPE_CACHE.get(key)
    now_ts = now.timestamp()
    if last_ts and now_ts - last_ts < _ALERT_DEDUPE_SECONDS:
        return False
    _ALERT_DEDUPE_CACHE[key] = now_ts
    return True


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

        # 2. 获取全市场快照（为了拿到最新价）
        snapshot = get_market_snapshot()
        if snapshot.empty:
            return "Failed to fetch snapshot"
        if is_snapshot_stale(snapshot):
            return "Failed to fetch fresh snapshot"
            
        snapshot_map = snapshot.set_index('code')['price'].to_dict()
        snapshot_high_map = snapshot.set_index('code')['high'].to_dict() if 'high' in snapshot.columns else {}

        # 在循环外部，仅抓取一次大盘基准指数历史 K 线（用于 RS 计算），彻底消除循环内 24 次冗余的网络请求
        from core.data import get_index_hist
        bench_df = get_index_hist("000001")

        codes = df_paper['code'].unique().tolist()
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
                ORDER BY date DESC LIMIT 40
            """)
            with engine.connect() as conn:
                df_hist = pd.read_sql(query, conn, params={"code": code})
                df_hist = df_hist.sort_values("日期")
            
            if len(df_hist) < 20: continue

            # 注入实时价并传入大盘基准，避免内部隐式重复抓取
            df_labeled = calculate_indicators(df_hist, current_price=curr_price, bench_df=bench_df)
            
            # 4. 评估信号
            signals = evaluate_exit_signals(df_labeled, entry_price, high_since_entry, code=code)
            
            if signals:
                # 过滤出需要推送的信号 (warning 和 critical)
                important_signals = [s for s in signals if s['level'] in ['warning', 'critical']]
                if important_signals:
                    sig = important_signals[0]
                    reason = sig.get('reason', '')
                    if not _should_push_alert(code, reason, now):
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
            
            loop.run_until_complete(notifier.send(
                title, 
                body, 
                group="AlphaVision_Alert",
                url="http://localhost:3000"  # 默认跳转到仪表板
            ))
            logger.info(f"Sent {len(alerts_triggered)} alerts via push channels.")

        return f"Processed {len(df_paper)} positions, triggered {len(alerts_triggered)} alerts"

    except Exception as e:
        logger.error(f"Error in check_realtime_alerts task: {e}")
        return str(e)


@celery_app.task(name="tasks.intraday_monitor_checkpoint")
def intraday_monitor_checkpoint(slot: str = "price_watch"):
    """Professional intraday workflow checkpoints for real trading operations."""
    now = datetime.now()
    if not is_a_share_intraday_session(now) and slot not in {"after_close_review"}:
        logger.info(f"Checkpoint {slot} skipped: market closed.")
        return "Market closed"

    summary = {
        "slot": slot,
        "operation_alerts": 0,
        "watch_alerts": 0,
        "watch_status_push": 0,
        "pruned": 0,
        "next_day_push": 0,
        "bark_self_check": 0,
    }
    try:
        from core.bark_health import send_bark_self_check
        from routers.paper_trade import check_operation_triggers
        from routers.watchlist import (
            auto_prune_watchlist,
            check_watchlist_triggers,
            refresh_watchlist_decisions,
            send_watchlist_status_report,
        )

        if slot == "open_risk":
            self_check = send_bark_self_check()
            summary["bark_self_check"] = 1 if (self_check.get("notification") or {}).get("bark") else 0

        if slot in {"open_risk", "morning_confirm", "late_decision", "price_watch"}:
            operation = check_operation_triggers(notify=True, trade_mode="REAL")
            summary["operation_alerts"] = len(operation.get("alerts") or [])

        if slot in {"morning_confirm", "candidate_scan", "late_decision"}:
            watch = check_watchlist_triggers(notify=True)
            summary["watch_alerts"] = int(watch.get("count") or 0)

        if slot == "open_risk":
            report = send_watchlist_status_report("morning")
            summary["watch_status_push"] = int(report.get("count") or 0) if report.get("bark") else 0
        elif slot == "late_decision":
            report = send_watchlist_status_report("late")
            summary["watch_status_push"] = int(report.get("count") or 0) if report.get("bark") else 0

        if slot in {"candidate_scan", "after_close_review"}:
            refresh_watchlist_decisions()
            pruned = auto_prune_watchlist(max_watch_days=15)
            summary["pruned"] = int(pruned.get("updated") or 0)

        if slot == "after_close_review":
            from core.db import get_scan_history_by_date
            from core.sentinel import send_after_close_watchlist, _send_bark_message

            scan_date = now.strftime("%Y-%m-%d")
            scan_results = get_scan_history_by_date(scan_date)
            pushed_body = send_after_close_watchlist(scan_results, scan_date=scan_date, now=now)
            summary["next_day_push"] = 1 if pushed_body else 0
            try:
                from core.db import get_setting, save_setting
                from routers.review import get_daily_strategy_report

                if get_setting("daily_strategy_report_last_date") != scan_date:
                    report_payload = get_daily_strategy_report(date=scan_date)
                    report_body = report_payload.get("body") or ""
                    if report_body and _send_bark_message(f"Alpha Vision 收盘策略日报 {scan_date}", report_body):
                        save_setting("daily_strategy_report_last_date", scan_date)
                        summary["daily_report_push"] = 1
                    else:
                        summary["daily_report_push"] = 0
                else:
                    summary["daily_report_push"] = 0
            except Exception as exc:
                logger.warning(f"Daily strategy report push skipped: {exc}")
                summary["daily_report_push"] = 0

        logger.info(f"Intraday checkpoint completed: {summary}")
        return summary
    except Exception as exc:
        logger.error(f"Intraday checkpoint {slot} error: {exc}")
        return {"slot": slot, "error": str(exc)}


@celery_app.task(name="tasks.daily_sync")
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
        
        # 同步完成后发送摘要推送
        try:
            import asyncio
            title = f"📊 Alpha Vision {slot}数据同步完成"
            body = "全市场行情数据已同步，板块雷达和选股模块将使用最新本地数据。"
            
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            loop.run_until_complete(notifier.send(
                title, 
                body, 
                group="AlphaVision_Sync",
                url="http://localhost:3000"
            ))
        except Exception as push_err:
            logger.error(f"Failed to send sync summary push: {push_err}")
            
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
    grade = str(item.get("early_trade_grade") or item.get("sop_grade") or item.get("评级") or item.get("grade") or "").upper()
    action = str(item.get("sop_action") or item.get("pa_trade_action") or item.get("trade_bucket") or "WATCH").upper()
    bucket = str(item.get("trade_bucket") or "").upper()
    if grade == "D" or bucket == "BLOCK" or action in {"AVOID", "D"}:
        return "排除/不买：评级或交易桶未通过，最多复盘观察"
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
    if not results:
        return False

    selected = sorted(results, key=_scan_score, reverse=True)[:limit]
    scan_date = selected[0].get("data_date") or datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"午间全量同步后选股 {scan_date}",
        "性质：午间候选清单，下午需结合放量站稳和市场情绪复核。",
        "",
    ]
    for item in selected:
        code = item.get("代码") or item.get("code") or ""
        name = item.get("名称") or item.get("name") or ""
        price = item.get("现价") or item.get("current_price") or item.get("price") or "--"
        score = _scan_display_score(item)
        grade = item.get("early_trade_grade") or item.get("sop_grade") or item.get("评级") or item.get("grade") or "--"
        lines.append(f"{name}({code}) 现价 {price} | 评分 {score:.1f} | {grade}")
        lines.append(f"  建议：{_noon_action_text(item)}")
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
    try:
        return bool(asyncio.run(notifier.send(
            f"Alpha Vision 午间选股 {scan_date}",
            body,
            channels=["bark"],
            group="AlphaVision_Noon",
            url="http://localhost:3000",
        )))
    except Exception as exc:
        logger.error(f"Noon scan Bark push failed: {exc}")
        return False


@celery_app.task(name="tasks.noon_sync_scan_review")
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

        from routers.scan import run_market_scan_task
        try:
            results = run_market_scan_task(
                strategy_type="tv_dual_strict",
                local_only=True,
                require_live_snapshot=True,
            ) or []
            summary["scan_count"] = len(results)
            summary["scan_push"] = _send_noon_scan_push(results)
        except Exception as scan_exc:
            logger.error(f"Noon scan step error: {scan_exc}")
            summary["errors"].append(f"scan: {scan_exc}")

        from routers.paper_trade import check_operation_triggers
        from routers.watchlist import check_watchlist_triggers, send_watchlist_status_report

        try:
            operation = check_operation_triggers(notify=True, trade_mode="REAL")
            summary["operation_alerts"] = len(operation.get("alerts") or [])
        except Exception as operation_exc:
            logger.error(f"Noon operation trigger step error: {operation_exc}")
            summary["errors"].append(f"operation: {operation_exc}")
        try:
            watch = check_watchlist_triggers(notify=True)
            summary["watch_alerts"] = int(watch.get("count") or 0)
        except Exception as watch_exc:
            logger.error(f"Noon watch trigger step error: {watch_exc}")
            summary["errors"].append(f"watch: {watch_exc}")
        try:
            report = send_watchlist_status_report("noon")
            summary["watch_status_push"] = int(report.get("count") or 0) if report.get("bark") else 0
        except Exception as report_exc:
            logger.error(f"Noon watch status step error: {report_exc}")
            summary["errors"].append(f"watch_report: {report_exc}")
        logger.info(f"Noon workflow completed: {summary}")
        return summary
    except Exception as exc:
        logger.error(f"Noon workflow error: {exc}")
        summary["error"] = str(exc)
        return summary


@celery_app.task(name="tasks.early_value_scan")
def early_value_scan():
    """Run the independent early-value watch strategy without changing the main scan."""
    now = datetime.now()
    if not is_a_share_trading_day(now):
        return {"status": "skipped", "reason": "non_trading_day", "strategy_type": "early_value"}
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

        loop.run_until_complete(notifier.send(
            title, body,
            group="AlphaVision_Report",
            url="http://localhost:3000"
        ))
        logger.info(f"Weekly entry timing report sent: {meta.get('effective_size')} stocks")
        return {"bark": True, "meta": meta, "aggregate": report.get("aggregate", {})}
    except Exception as e:
        logger.error(f"Error in weekly_entry_timing_report task: {e}")
        return {"bark": False, "error": str(e)}
