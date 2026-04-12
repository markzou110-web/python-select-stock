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
        try:
            self.redis_client = redis.Redis.from_url(redis_url)
            self.pubsub = self.redis_client.pubsub()
            self.pubsub.subscribe('scan_progress')
            self.redis_available = True
        except Exception as e:
            print(f"Warning: Redis is unavailable. WebSockets cross-process might fail. {e}")
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
        # We need an async reader for redis to not block the event loop
        # For simplicity with sync redis client, we poll with asyncio.sleep
        while True:
            try:
                message = self.redis_client.pubsub().get_message(ignore_subscribe_messages=True)
                if message and message.get('type') == 'message':
                    data = json.loads(message['data'])
                    await self.broadcast(data)
            except Exception:
                pass
            await asyncio.sleep(0.05)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        text_data = json.dumps(message)
        dead_connections = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(text_data)
            except Exception:
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
