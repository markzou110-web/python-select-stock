"""
Alpha Vision API - Main entry point.

Routes are registered from the routers/ module.
This file handles app initialization, middleware, lifespan events, and the Intraday Sentinel.
"""
from fastapi import FastAPI, BackgroundTasks, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import time
import socket
import threading
import asyncio
import requests
import warnings

# Suppress pandas FutureWarnings caused by akshare
warnings.simplefilter(action='ignore', category=FutureWarning)

from core.config import config
from core.logging_config import logger
from core.db import get_db_engine, init_db, get_setting, save_setting
from core.ws_manager import manager as ws_manager
from core.errors import AlphaVisionError, error_response

# Bark Key from config (not hardcoded)
BARK_KEY = config.BARK_KEY

socket.setdefaulttimeout(config.AKSHARE_TIMEOUT)  # 防止网络请求无限挂起


from core.sentinel import sentinel


# --- App Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    config.setup_no_proxy()
    logger.info("Proxy disabled for network requests")

    logger.info("Initializing database...")
    init_db()

    logger.info("Starting Intraday Sentinel...")
    sentinel.start()

    # 异步预热核心缓存
    from core.data import get_index_data, get_hot_sectors, get_sector_map
    from routers.market import fetch_mine_sweeper_data
    loop = asyncio.get_running_loop()
    logger.info("Pre-warming Index and Sector cache...")
    loop.run_in_executor(None, get_index_data)
    loop.run_in_executor(None, get_hot_sectors)
    loop.run_in_executor(None, get_sector_map)
    loop.run_in_executor(None, fetch_mine_sweeper_data)

    yield


# --- FastAPI App ---
app = FastAPI(title="Alpha Vision API", version="5.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

# --- Rate Limiting Middleware ---
if config.RATE_LIMIT_ENABLED:
    from core.rate_limiter import RateLimiter

    _rate_limiter = RateLimiter()
    _rate_limiter.register("/api/scan", config.RATE_LIMIT_SCAN)       # e.g. 10/minute
    _rate_limiter.register("/api/sync", config.RATE_LIMIT_SYNC)       # e.g. 1/hour

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        path = request.url.path
        # Exempt WebSocket, health checks, and status polling
        if (path.startswith("/api/ws") or 
            path.startswith("/api/scan/status") or
            path in ("/docs", "/openapi.json", "/api/health", "/api/sync/status")):
            return await call_next(request)

        if not _rate_limiter.check(path):
            rule = _rate_limiter.get_matching_rule(path)
            logger.warning(f"Rate limited: {path} (rule: {rule})")
            return JSONResponse(
                status_code=429,
                content=error_response("RATE_LIMITED", f"请求过于频繁，限制为 {rule}"),
            )
        return await call_next(request)

# --- Global Exception Handler ---
_ERROR_STATUS_MAP = {
    "DB_ERROR": 503,
    "SCAN_ERROR": 500,
    "DATA_SOURCE_ERROR": 502,
    "VALIDATION_ERROR": 400,
}

@app.exception_handler(AlphaVisionError)
async def alpha_vision_error_handler(request: Request, exc: AlphaVisionError):
    """Catch all AlphaVisionError subclasses and return structured JSON."""
    status_code = _ERROR_STATUS_MAP.get(exc.code, 500)
    logger.warning(f"AlphaVisionError [{exc.code}]: {exc.message}")
    return JSONResponse(
        status_code=status_code,
        content=error_response(exc.code, exc.message),
    )

# --- Register Routers ---
from routers.market import router as market_router
from routers.sync import router as sync_router
from routers.scan import router as scan_router
from routers.paper_trade import router as paper_router
from routers.stock import router as stock_router
from routers.settings import router as settings_router
from routers.alert import router as alert_router
from routers.kline import router as kline_router
from routers.review import router as review_router
from routers.watchlist import router as watchlist_router
from routers.strategy_templates import router as strategy_templates_router

app.include_router(market_router)
app.include_router(sync_router)
app.include_router(scan_router)
app.include_router(paper_router)
app.include_router(stock_router)
app.include_router(settings_router)
app.include_router(alert_router)
app.include_router(kline_router)
app.include_router(review_router)
app.include_router(watchlist_router)
app.include_router(strategy_templates_router)

@app.get("/api/market/pulse")
async def get_market_pulse():
    """获取大盘多指数综合判准"""
    from core.data import get_market_regime
    return get_market_regime()

@app.websocket("/api/ws/scan-progress")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # We don't expect messages from client, just keep connection open
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
