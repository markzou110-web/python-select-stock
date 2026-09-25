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
import hmac

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
from core.sync_scheduler import market_sync_scheduler


# --- App Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    config.setup_no_proxy()
    logger.info("Proxy disabled for network requests")

    logger.info("Initializing database...")
    init_db()

    # 必须在后台线程启动前预加载交易日历：tool_trade_date_hist_sina() 内部
    # 实例化 py_mini_racer.MiniRacer()（V8 引擎），若 sentinel / sync_scheduler
    # 后台线程并发触发会引起 V8 地址池重复初始化的 native crash
    # (address_pool_manager Check failed)，无法被 try/except 捕获，会杀进程。
    # 在主线程单线程预加载，V8 只初始化一次，后续后台线程命中缓存不再触碰 V8。
    from core.trading_calendar import preload_trade_calendar
    logger.info("Pre-loading trade calendar (single-thread V8 init)...")
    preload_trade_calendar()

    logger.info("Starting Intraday Sentinel...")
    sentinel.start()

    logger.info("Starting Market Sync Scheduler...")
    market_sync_scheduler.start()

    # 缓存预热已移至首次请求时懒加载。
    # 原来在启动时预热(get_index_data/get_hot_sectors等)，但 akshare 部分函数
    # 内部使用 V8/mini_racer 引擎，在 macOS arm64 上触发 FATAL 段错误
    # (libmini_racer Check failed)，该 native crash 无法被 try/except 捕获，
    # 会杀掉整个 uvicorn 进程导致服务无法启动。
    # 缓存在首次请求对应端点时会自动加载（有 TTL 缓存），不影响功能。
    logger.info("Cache pre-warming skipped (lazy-load on first request).")

    yield
    market_sync_scheduler.stop()


# --- FastAPI App ---
app = FastAPI(title="Alpha Vision API", version="5.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def security_headers_and_write_auth(request: Request, call_next):
    """Protect state-changing APIs when ENABLE_AUTH is enabled and add browser hardening."""
    if config.ENABLE_AUTH and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        authorization = request.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else request.headers.get("X-API-Key", "")
        expected = config.API_TOKEN or ""
        if not expected or not hmac.compare_digest(supplied, expected):
            return JSONResponse(status_code=401, content=error_response("UNAUTHORIZED", "写操作需要有效API令牌"))
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https:; connect-src 'self' http://127.0.0.1:* http://localhost:*; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'"
    return response

# --- Rate Limiting Middleware ---
if config.RATE_LIMIT_ENABLED:
    from core.rate_limiter import RateLimiter

    _rate_limiter = RateLimiter()
    _rate_limiter.register("/api/scan", config.RATE_LIMIT_SCAN)       # e.g. 10/minute
    _rate_limiter.register("/api/sync", config.RATE_LIMIT_SYNC)       # e.g. 1/hour
    _rate_limiter.register("/api/ai/analyze-", config.RATE_LIMIT_AI)

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
from routers.system import router as system_router
from routers.backtest import router as backtest_router
from routers.money_flow import router as money_flow_router
from routers.execution_intents import router as execution_intents_router
from routers.ai_analysis import router as ai_analysis_router

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
app.include_router(system_router)
app.include_router(backtest_router)
app.include_router(money_flow_router)
app.include_router(execution_intents_router)
app.include_router(ai_analysis_router)

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
    uvicorn.run(app, host=config.API_HOST, port=8000)
