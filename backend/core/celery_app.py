from celery import Celery
import os
import sys
from .logging_config import logger
from .config import config
config.setup_no_proxy()
from celery.schedules import crontab
from celery.signals import task_failure, task_postrun, task_prerun
from datetime import datetime
import json

import redis

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "alphavision_tasks",
    broker=redis_url,
    backend=redis_url,
    include=["routers.scan", "core.tasks"] # 注册 task 所在的模块
)

# 检查 Redis 连通性，如果不通则退化为单机同步模式
redis_available = False
try:
    client = redis.Redis.from_url(redis_url, socket_connect_timeout=1)
    client.ping()
    redis_available = True
    logger.info("Celery broker (Redis) is connected. Async mode enabled.")
except Exception as e:
    logger.warning(f"Redis is not available: {e}. Falling back to sync mode (task_always_eager).")

# Celery 基础配置
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Shanghai',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600, # 一次任务最长1小时
    task_always_eager=not redis_available, # 如果没有 Redis，就在当前线程同步执行！
    task_eager_propagates=True,
    broker_connection_retry_on_startup=True,
    task_default_queue='maintenance',
    task_routes={
        'tasks.check_realtime_alerts': {'queue': 'realtime'},
        'tasks.check_position_operation_alerts': {'queue': 'realtime'},
        'tasks.send_premarket_position_advice': {'queue': 'realtime'},
        'tasks.send_position_status_summary': {'queue': 'realtime'},
        'tasks.retry_pending_notifications': {'queue': 'realtime'},
        # 分钟级采集（每分钟涨停池 + 每5分钟分钟K，逐票外部调用可达 30-60s）从
        # realtime 拆到独立 collector 队列：macOS solo 单 worker 下它们会把
        # 风控/告警推送排队阻塞，还会挤掉下一分钟任务（expires=50 静默丢弃）。
        'tasks.collect_limit_up_leadership': {'queue': 'collector'},
        'tasks.collect_candidate_minute_bars': {'queue': 'collector'},
        'tasks.theme_momentum_watch': {'queue': 'realtime'},
        # Checkpoints can run a full-market primary TV scan; keeping them on the
        # realtime queue would still block price/risk alerts for 1-2 minutes.
        'tasks.intraday_monitor_checkpoint': {'queue': 'scan'},
        'tasks.recover_late_formal_scan': {'queue': 'scan'},
        'scan.run_market_scan_task': {'queue': 'scan'},
        'tasks.noon_sync_scan_review': {'queue': 'scan'},
        'tasks.early_value_scan': {'queue': 'scan'},
        'tasks.bottom_discovery_scan': {'queue': 'scan'},
        'tasks.daily_sync': {'queue': 'maintenance'},
        'tasks.database_backup': {'queue': 'maintenance'},
        'tasks.discover_event_catalysts': {'queue': 'maintenance'},
        'tasks.expire_execution_intents': {'queue': 'maintenance'},
        'tasks.weekly_entry_timing_report': {'queue': 'maintenance'},
        'tasks.send_daily_ai_review': {'queue': 'maintenance'},
    },
    # 定时任务配置 (Beat)
    beat_schedule={
        'check-alerts-every-5-minutes': {
            'task': 'tasks.check_realtime_alerts',
            'schedule': crontab(minute='*/5', hour='9-11,13-14', day_of_week='1-5'),
            'options': {'expires': 240},
        },
        'check-position-operation-alerts-every-5-minutes': {
            'task': 'tasks.check_position_operation_alerts',
            'schedule': crontab(minute='2-57/5', hour='9-11,13-14', day_of_week='1-5'),
            'options': {'expires': 240},
        },
        'premarket-position-advice-0845': {
            'task': 'tasks.send_premarket_position_advice',
            'schedule': crontab(hour=8, minute=45, day_of_week='1-5'),
            'options': {'expires': 900},
        },
        'position-status-summary-1125': {
            'task': 'tasks.send_position_status_summary',
            'schedule': crontab(hour=11, minute=25, day_of_week='1-5'),
            'kwargs': {'slot': 'morning'},
            'options': {'expires': 600},
        },
        'position-status-summary-1450': {
            'task': 'tasks.send_position_status_summary',
            'schedule': crontab(hour=14, minute=50, day_of_week='1-5'),
            'kwargs': {'slot': 'late'},
            'options': {'expires': 300},
        },
        'retry-pending-notifications-every-5-minutes': {
            'task': 'tasks.retry_pending_notifications',
            'schedule': crontab(minute='*/5', hour='8-22'),
            'options': {'expires': 240},
        },
        'collect-limit-up-leadership-every-minute': {
            'task': 'tasks.collect_limit_up_leadership',
            'schedule': crontab(minute='*', hour='9-11,13-14', day_of_week='1-5'),
            'options': {'expires': 50},
        },
        'collect-limit-up-leadership-after-close': {
            'task': 'tasks.collect_limit_up_leadership',
            'schedule': crontab(hour=15, minute=1, day_of_week='1-5'),
            'options': {'expires': 240},
        },
        # 涨停情绪聚合须在 15:01 最终涨停/炸板采集之后跑
        'update-limit-up-sentiment-1506': {
            'task': 'tasks.update_limit_up_sentiment',
            'schedule': crontab(hour=15, minute=6, day_of_week='1-5'),
            'options': {'expires': 600},
        },
        # 行业资金流排名：午间一次盘中快照 + 盘后最终值，供证据门当日实时参考
        'update-sector-fund-flow-1140': {
            'task': 'tasks.update_sector_fund_flow',
            'schedule': crontab(hour=11, minute=40, day_of_week='1-5'),
            'options': {'expires': 600},
        },
        'update-sector-fund-flow-1505': {
            'task': 'tasks.update_sector_fund_flow',
            'schedule': crontab(hour=15, minute=5, day_of_week='1-5'),
            'options': {'expires': 600},
        },
        # 龙虎榜收盘后由交易所披露（约 17:00 前后），17:05 拉近 3 个交易日
        'update-lhb-records-1705': {
            'task': 'tasks.update_lhb_records',
            'schedule': crontab(hour=17, minute=5, day_of_week='1-5'),
            'options': {'expires': 1800},
        },
        'collect-candidate-minute-bars-every-5-minutes': {
            'task': 'tasks.collect_candidate_minute_bars',
            'schedule': crontab(minute='*/5', hour='9-11,13-14', day_of_week='1-5'),
            'options': {'expires': 240},
        },
        'intraday-open-risk-0935': {
            'task': 'tasks.intraday_monitor_checkpoint',
            'schedule': crontab(hour=9, minute=35, day_of_week='1-5'),
            'kwargs': {'slot': 'open_risk'},
            'options': {'expires': 600},
        },
        'theme-momentum-watch-0935': {
            'task': 'tasks.theme_momentum_watch',
            'schedule': crontab(hour=9, minute=35, day_of_week='1-5'),
            'options': {'expires': 300},
            'kwargs': {'slot': '09:35'},
        },
        'theme-momentum-watch-0945': {
            'task': 'tasks.theme_momentum_watch',
            'schedule': crontab(hour=9, minute=45, day_of_week='1-5'),
            'options': {'expires': 300},
            'kwargs': {'slot': '09:45'},
        },
        'theme-momentum-watch-1000': {
            'task': 'tasks.theme_momentum_watch',
            'schedule': crontab(hour=10, minute=0, day_of_week='1-5'),
            'options': {'expires': 300},
            'kwargs': {'slot': '10:00'},
        },
        'theme-momentum-watch-1310': {
            'task': 'tasks.theme_momentum_watch',
            'schedule': crontab(hour=13, minute=10, day_of_week='1-5'),
            'options': {'expires': 300},
            'kwargs': {'slot': '13:10'},
        },
        'theme-momentum-watch-1400': {
            'task': 'tasks.theme_momentum_watch',
            'schedule': crontab(hour=14, minute=0, day_of_week='1-5'),
            'options': {'expires': 300},
            'kwargs': {'slot': '14:00'},
        },
        'intraday-morning-confirm-1030': {
            'task': 'tasks.intraday_monitor_checkpoint',
            'schedule': crontab(hour=10, minute=30, day_of_week='1-5'),
            'kwargs': {'slot': 'morning_confirm'},
            'options': {'expires': 600},
        },
        'noon-sync-1135': {
            'task': 'tasks.noon_sync_scan_review',
            'schedule': crontab(hour=11, minute=35, day_of_week='1-5'),
            'options': {'expires': 900},
            'kwargs': {'sync_first': True, 'run_review': False},
        },
        'early-value-independent-scan-1315': {
            'task': 'tasks.early_value_scan',
            'schedule': crontab(hour=13, minute=15, day_of_week='1-5'),
            'options': {'expires': 900},
        },
        'bottom-discovery-morning-0945': {
            'task': 'tasks.bottom_discovery_scan',
            'schedule': crontab(hour=9, minute=45, day_of_week='1-5'),
            'kwargs': {'slot': '09:45'},
            'options': {'expires': 900},
        },
        'bottom-discovery-afternoon-1325': {
            'task': 'tasks.bottom_discovery_scan',
            'schedule': crontab(hour=13, minute=25, day_of_week='1-5'),
            'kwargs': {'slot': '13:25'},
            'options': {'expires': 900},
        },
        'intraday-late-decision-1450': {
            'task': 'tasks.intraday_monitor_checkpoint',
            'schedule': crontab(hour=14, minute=50, day_of_week='1-5'),
            'kwargs': {'slot': 'late_decision'},
            'options': {'expires': 300},
        },
        'intraday-late-recovery-1455': {
            'task': 'tasks.recover_late_formal_scan',
            'schedule': crontab(hour=14, minute=55, day_of_week='1-5'),
            'options': {'expires': 240},
        },
        'intraday-after-close-review-1510': {
            'task': 'tasks.intraday_monitor_checkpoint',
            'schedule': crontab(hour=15, minute=10, day_of_week='1-5'),
            'kwargs': {'slot': 'after_close_review'},
            'options': {'expires': 900},
        },
        'expire-execution-intents-1510': {
            'task': 'tasks.expire_execution_intents',
            'schedule': crontab(hour=15, minute=10, day_of_week='1-5'),
            'options': {'expires': 600},
        },
        'event-catalyst-discovery-1630': {
            'task': 'tasks.discover_event_catalysts',
            'schedule': crontab(hour=16, minute=30, day_of_week='1-5'),
            'options': {'expires': 3600},
        },
        # 收盘AI复核：18:00全市场同步完成后，复核当日头部候选并随日报推送Bark
        'daily-ai-review-1810': {
            'task': 'tasks.send_daily_ai_review',
            'schedule': crontab(hour=18, minute=10, day_of_week='1-5'),
            'options': {'expires': 3600},
        },
        'database-backup-2030': {
            'task': 'tasks.database_backup',
            'schedule': crontab(hour=20, minute=30),
            'options': {'expires': 3600},
        },
        # 备份补跑：20:30 错过（停机/beat 未跑）后 21:30 再试一次；任务幂等
        # （当日已有备份即跳过），不会重复备份
        'database-backup-retry-2130': {
            'task': 'tasks.database_backup',
            'schedule': crontab(hour=21, minute=30),
            'options': {'expires': 3600},
        },
        # 买入时点周报：每周一 09:00，对比尾盘买 vs 次日开盘买的胜率
        'weekly-entry-timing-report-monday-0900': {
            'task': 'tasks.weekly_entry_timing_report',
            'schedule': crontab(hour=9, minute=0, day_of_week='1'),
            'options': {'expires': 3600},
        },
        # Elder NH-NL 宽度指标：盘后 17:30 统计全市场 250 日新高/新低 + MA50 上方占比
        'market-breadth-extremes-1730': {
            'task': 'tasks.update_market_breadth_extremes',
            'schedule': crontab(hour=17, minute=30, day_of_week='1-5'),
            'options': {'expires': 3600},
        },
        # Elder 退出后回顾：每月 1 日 20:40 回顾两个月前平仓的交易并写交易日志
        'post-exit-monthly-review-1st-2040': {
            'task': 'tasks.post_exit_monthly_review',
            'schedule': crontab(hour=20, minute=40, day_of_month='1'),
            'options': {'expires': 3600},
        },
        # 数据新鲜度看门狗：盘后 19:35 交叉比对各数据链路最新日期，断流即 Bark
        'data-freshness-watchdog-1935': {
            'task': 'tasks.check_data_freshness',
            'schedule': crontab(hour=19, minute=35, day_of_week='1-5'),
            'options': {'expires': 1800},
        },
        # 每周数据维护：周六 04:00 清理审计/点时快照/分钟bar 类高增长表
        'weekly-data-maintenance-saturday-0400': {
            'task': 'tasks.weekly_data_maintenance',
            'schedule': crontab(hour=4, minute=0, day_of_week='6'),
            'options': {'expires': 7200},
        },
        # Redis 健康看门：每 5 分钟探测，状态翻转推 Bark（eager 只在 import 时
        # 判定一次，Redis 运行中宕机 = 定时任务静默丢失，必须分钟级可知）
        'redis-health-watch-5min': {
            'task': 'tasks.redis_health_watch',
            'schedule': crontab(minute='*/5', hour='7-23'),
            'options': {'expires': 240},
        },
        # 市场状态闸门（SHADOW）：每日记录 R3 十日动量状态，为转正积累对照样本
        'market-state-gate-daily-1915': {
            'task': 'tasks.update_market_state_gate',
            'schedule': crontab(hour=19, minute=15, day_of_week='1-5'),
            'options': {'expires': 1800},
        },
        # 题材热度榜（借鉴 easy-stock 主题热点页）：午间/盘后各一次（依赖 11:40/15:05 资金流先落库）
        'theme-heat-1145': {
            'task': 'tasks.update_theme_heat',
            'schedule': crontab(hour=11, minute=45, day_of_week='1-5'),
            'kwargs': {'scope': 'CONCEPT'},
            'options': {'expires': 1200},
        },
        'theme-heat-1520': {
            'task': 'tasks.update_theme_heat',
            'schedule': crontab(hour=15, minute=20, day_of_week='1-5'),
            'options': {'expires': 1800},
        },
        # Full-market sync is owned by MarketSyncScheduler in the API process so
        # progress is observable and it cannot race an embedded Celery beat.
    },
)

if sys.platform == "darwin":
    # macOS Objective-C frameworks are not safe after Celery's default fork.
    celery_app.conf.worker_pool = "solo"
    celery_app.conf.worker_concurrency = 1


@task_prerun.connect
def audit_task_started(task_id=None, task=None, **kwargs):
    from .audit_log import record_task_run
    record_task_run(str(task_id or ""), getattr(task, "name", None), "STARTED", started_at=datetime.now())


@task_postrun.connect
def audit_task_finished(task_id=None, task=None, state=None, retval=None, **kwargs):
    from .audit_log import record_task_run
    summary = _task_result_summary(retval)
    audit_status = str(state or "SUCCESS")
    error_message = None
    if audit_status == "SUCCESS" and isinstance(retval, dict):
        if retval.get("status") == "error":
            audit_status = "FAILURE"
            error_message = str(
                retval.get("error") or retval.get("detail") or retval.get("reason")
                or "unknown task failure"
            )
        elif retval.get("errors"):
            audit_status = "SUCCESS_WITH_ERRORS"
    record_task_run(
        str(task_id or ""),
        getattr(task, "name", None),
        audit_status,
        finished_at=datetime.now(),
        result_summary=summary,
        error_message=error_message,
    )


@task_failure.connect
def audit_task_failed(task_id=None, sender=None, exception=None, **kwargs):
    from .audit_log import record_task_run
    record_task_run(
        str(task_id or ""),
        getattr(sender, "name", None),
        "FAILURE",
        finished_at=datetime.now(),
        error_message=str(exception or "unknown task failure"),
    )

def _task_result_summary(retval) -> str:
    if not isinstance(retval, dict):
        summary = f"{type(retval).__name__}"
        if isinstance(retval, list):
            summary += f" count={len(retval)}"
        return summary

    keep_keys = (
        "status", "error", "detail", "reason", "slot", "sync", "scan_count", "scan_push",
        "operation_alerts", "watch_alerts", "watch_status_push",
        "formal_scan_completed", "formal_scan_count", "formal_scan_push", "pruned", "next_day_push",
        "next_day_reviewed", "next_day_confirmed", "next_day_confirmation_bark",
        "daily_report_push", "bark_self_check", "codes", "bars", "sent", "failed",
        "snapshot_bars", "eastmoney_bars", "errors", "source_paused", "sealed", "broken", "saved",
        "notification", "count", "bark", "live_refreshed",
    )
    compact = {}
    for key in keep_keys:
        if key not in retval:
            continue
        value = retval.get(key)
        if key == "sync" and value is not None:
            value = str(value)[:160]
        if key == "notification" and isinstance(value, dict):
            value = {k: bool(v) for k, v in value.items()}
        compact[key] = value
    if not compact:
        compact = {"type": "dict", "count": len(retval)}
    try:
        return json.dumps(compact, ensure_ascii=False, default=str)[:1000]
    except Exception:
        return f"dict count={len(retval)}"


if __name__ == "__main__":
    celery_app.start()
