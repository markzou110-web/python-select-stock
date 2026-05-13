import os
import sys
import asyncio
from pathlib import Path

# Add backend to sys.path
backend_dir = Path(__file__).parent.parent
sys.path.append(str(backend_dir))

from core.notifier import notifier

async def test_push():
    print("--- Bark Push Test ---")
    print(f"Testing with key from environment: {os.getenv('BARK_KEY')}")
    
    title = "Alpha Vision 测试"
    body = "看到这条消息说明您的 Bark 配置已成功生效！🚀"
    
    # We use the notifier's test method
    success = await notifier.test_channel("bark")
    
    if success:
        print("\n✅ 测试消息已成功发送！请检查您的手机 Bark App。")
    else:
        print("\n❌ 发送失败，请检查网络连接或 Key 是否正确。")

if __name__ == "__main__":
    asyncio.run(test_push())
