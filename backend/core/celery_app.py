from celery import Celery
import os
from .logging_config import logger
from .config import config

import redis

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "alphavision_tasks",
    broker=redis_url,
    backend=redis_url,
    include=["routers.scan"] # 注册 task 所在的模块
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
)

if __name__ == "__main__":
    celery_app.start()
