from fastapi import WebSocket
from typing import List
import json
import asyncio

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.loop = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        if self.loop is None:
            self.loop = asyncio.get_running_loop()

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
        """Invoke from a synchronous thread to safely send a message."""
        if not self.active_connections or self.loop is None:
            return
        
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)
        except Exception:
            pass

manager = ConnectionManager()
