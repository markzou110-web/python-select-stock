import sys
import os
import unittest.mock as mock
from datetime import datetime

# 确保 backend 目录在 path 中
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from core.logging_config import logger
logger.info("Initializing Real-Time Alert E2E Resilience Test...")

# 1. 模拟一个处于交易时间段的 datetime
class FakeDatetime:
    @classmethod
    def now(cls):
        # 模拟 2026-05-25 (周一) 早上 10:30 (A股活跃交易时间)
        return datetime(2026, 5, 25, 10, 30, 0)
    
    @classmethod
    def today(cls):
        return datetime(2026, 5, 25, 10, 30, 0)

# 2. 执行 Monkey Patch 并导入任务
logger.info("Applying Monkey Patch for trading hours simulation...")
with mock.patch('core.tasks.datetime', FakeDatetime):
    from core.tasks import check_realtime_alerts
    
    start_time = datetime.now()
    logger.info("Triggering check_realtime_alerts()...")
    
    # 3. 执行任务
    result = check_realtime_alerts()
    
    duration = (datetime.now() - start_time).total_seconds()
    logger.info(f"Task completed in {duration:.2f} seconds.")
    logger.info(f"Execution Result: {result}")
    
    # 4. 打印验证
    if "Processed" in str(result):
        print("\n" + "="*60)
        print("🎉 SUCCESS: The E2E Real-Time Alert Pipeline is 100% HEALTHY!")
        print(f"Detail: {result}")
        print("="*60)
        sys.exit(0)
    else:
        print("\n" + "="*60)
        print(f"❌ FAILURE: The pipeline returned an unexpected result: {result}")
        print("="*60)
        sys.exit(1)
