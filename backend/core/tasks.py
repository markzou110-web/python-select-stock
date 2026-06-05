from .celery_app import celery_app
from .logging_config import logger
from .notifier import notifier
from .db import get_db_engine
from .data import get_market_snapshot
from .indicators import calculate_indicators
from core.strategy import evaluate_exit_signals
from core.risk_engine import safe_float
from core.trading_calendar import is_a_share_intraday_session, is_a_share_trading_day
import pandas as pd
from datetime import datetime
from sqlalchemy import text

_ALERT_DEDUPE_CACHE = {}
_ALERT_DEDUPE_SECONDS = 30 * 60


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
            signals = evaluate_exit_signals(df_labeled, entry_price, high_since_entry)
            
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
            # 为保证移动端通知显示美观，若多于 5 个预警，仅展示前 5 个并做优雅截断，避免消息堆叠
            if len(alerts_triggered) > 5:
                body = "\n".join(alerts_triggered[:5]) + f"\n... 等共 {len(alerts_triggered)} 个风控预警信号，请点击查看仪表板。"
            else:
                body = "\n".join(alerts_triggered)
                
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

    summary = {"slot": slot, "operation_alerts": 0, "watch_alerts": 0, "pruned": 0}
    try:
        from routers.paper_trade import check_operation_triggers
        from routers.watchlist import auto_prune_watchlist, check_watchlist_triggers, refresh_watchlist_decisions

        if slot in {"open_risk", "morning_confirm", "late_decision", "price_watch"}:
            operation = check_operation_triggers(notify=True, trade_mode="REAL")
            summary["operation_alerts"] = len(operation.get("alerts") or [])

        if slot in {"morning_confirm", "candidate_scan", "late_decision"}:
            watch = check_watchlist_triggers(notify=True)
            summary["watch_alerts"] = int(watch.get("count") or 0)

        if slot in {"candidate_scan", "after_close_review"}:
            refresh_watchlist_decisions()
            pruned = auto_prune_watchlist(max_watch_days=15)
            summary["pruned"] = int(pruned.get("updated") or 0)

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
