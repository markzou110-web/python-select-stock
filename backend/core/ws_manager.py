import asyncio
import json
import os
from typing import List
from fastapi import WebSocket
import redis
import threading

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.loop = None
        self._listen_thread = None
        self._stop_flag = False
        try:
            self.redis_client = redis.Redis.from_url(redis_url)
            self.pubsub = self.redis_client.pubsub()
            self.pubsub.subscribe('scan_progress')
            self.redis_available = True
        except Exception as e:
            from .logging_config import logger
            logger.warning(f"Redis is unavailable. WebSockets cross-process might fail. {e}")
            self.redis_client = None
            self.pubsub = None
            self.redis_available = False

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        if self._listen_thread is None and self.redis_available:
            self._listen_thread = True
            # start a background task listening to redis pubsub
            asyncio.create_task(self._listen_to_redis())

    async def _listen_to_redis(self):
        """Listen to Redis pubsub for cross-process messages. Stops when flagged or no connections."""
        while not self._stop_flag:
            try:
                # 无活跃连接时降低轮询频率
                if not self.active_connections:
                    await asyncio.sleep(1.0)
                    continue

                message = self.pubsub.get_message(ignore_subscribe_messages=True)
                if message and message.get('type') == 'message':
                    data = json.loads(message['data'])
                    await self.broadcast(data)
            except redis.ConnectionError:
                # Redis 断开连接，等待重连
                await asyncio.sleep(5.0)
            except Exception:
                pass
            await asyncio.sleep(0.05)

    def stop(self):
        """Graceful shutdown: stop the Redis listener."""
        self._stop_flag = True

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        text_data = json.dumps(message)
        dead_connections = []
        for connection in list(self.active_connections):
            try:
                # 单连接 2 秒超时：半开/卡死客户端曾会让顺序 await 拖住整个事件
                # 循环（扫描进度每 100 票广播一次，一个死连接冻结所有浏览器进度条）
                await asyncio.wait_for(connection.send_text(text_data), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                dead_connections.append(connection)

        for dead in dead_connections:
            self.disconnect(dead)

    def broadcast_threadsafe(self, message: dict):
        """Publish message to Redis, so any process (like Celery) can send to WebSockets"""
        if self.redis_available and self.redis_client:
            try:
                self.redis_client.publish('scan_progress', json.dumps(message))
            except Exception:
                pass
        else:
            # Fallback for single-process run without Redis
            if self.active_connections and self.loop:
                try:
                    asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)
                except Exception:
                    pass

manager = ConnectionManager()

