from celery import Celery
import os
import sys
from .logging_config import logger
from .config import config
config.setup_no_proxy()
from celery.schedules import crontab
from celery.signals import task_failure, task_postrun, task_prerun
from datetime import datetime

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
    # 定时任务配置 (Beat)
    beat_schedule={
        'check-alerts-every-5-minutes': {
            'task': 'tasks.check_realtime_alerts',
            'schedule': 300.0, # 每 5 分钟检查一次
        },
        'collect-limit-up-leadership-every-minute': {
            'task': 'tasks.collect_limit_up_leadership',
            'schedule': crontab(minute='*', hour='9-11,13-14', day_of_week='1-5'),
        },
        'collect-limit-up-leadership-after-close': {
            'task': 'tasks.collect_limit_up_leadership',
            'schedule': crontab(hour=15, minute=1, day_of_week='1-5'),
        },
        'collect-candidate-minute-bars-every-5-minutes': {
            'task': 'tasks.collect_candidate_minute_bars',
            'schedule': crontab(minute='*/5', hour='9-11,13-14', day_of_week='1-5'),
        },
        'intraday-open-risk-0935': {
            'task': 'tasks.intraday_monitor_checkpoint',
            'schedule': crontab(hour=9, minute=35),
            'kwargs': {'slot': 'open_risk'},
        },
        'intraday-morning-confirm-1030': {
            'task': 'tasks.intraday_monitor_checkpoint',
            'schedule': crontab(hour=10, minute=30),
            'kwargs': {'slot': 'morning_confirm'},
        },
        'intraday-candidate-scan-1420': {
            'task': 'tasks.intraday_monitor_checkpoint',
            'schedule': crontab(hour=14, minute=20),
            'kwargs': {'slot': 'candidate_scan'},
        },
        'intraday-late-decision-1450': {
            'task': 'tasks.intraday_monitor_checkpoint',
            'schedule': crontab(hour=14, minute=50),
            'kwargs': {'slot': 'late_decision'},
        },
        'intraday-after-close-review-1510': {
            'task': 'tasks.intraday_monitor_checkpoint',
            'schedule': crontab(hour=15, minute=10),
            'kwargs': {'slot': 'after_close_review'},
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
    summary = f"{type(retval).__name__}"
    if isinstance(retval, (list, dict)):
        summary += f" count={len(retval)}"
    record_task_run(
        str(task_id or ""),
        getattr(task, "name", None),
        str(state or "SUCCESS"),
        finished_at=datetime.now(),
        result_summary=summary,
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

if __name__ == "__main__":
    celery_app.start()
