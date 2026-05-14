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


def send_intraday_notification(stock_list: List[Dict[str, Any]]) -> Optional[str]:
    """
    Sends a push notification via the Notifier for the Sentinel.
    Only sends A/B grade stocks with actionable information.
    """
    if not stock_list:
        return None

    # 仅推送 A 和 B 级标的
    ab_stocks = [s for s in stock_list if s.get('sop_grade') in ('A', 'B')]
    if not ab_stocks:
        logger.info("Sentinel: No A/B grade stocks to push.")
        return None

    # 获取大盘状态
    regime_emoji = {"OFFENSIVE": "🚀 进攻模式", "DEFENSIVE": "⚠️ 防守模式", "CRITICAL": "🛡️ 严格防守"}
    from core.data import get_market_regime
    regime = get_market_regime()
    regime_str = regime_emoji.get(regime.get('status', ''), '❓ 未知')

    now_str = datetime.now().strftime("%H:%M")
    title = f"Alpha Vision 哨兵 {now_str}"

    lines = [f"大盘：{regime_str}", ""]
    sector_emoji = {'LEAD': '🚀领涨', 'FOLLOW': '📈跟涨', 'FLAT': '➖横盘', 'DOWN': '📉下跌'}

    for s in ab_stocks[:5]:
        grade = s.get('sop_grade', '?')
        grade_icon = "🟢" if grade == "A" else "🔵"
        name = s.get('名称', s.get('name', ''))
        code = s.get('代码', s.get('code', ''))
        sector = s.get('行业', '')
        s_trend = sector_emoji.get(s.get('sector_trend', ''), '')
        s_pct = s.get('sector_pct', 0)
        entry = s.get('entry_price', 0)
        stop = s.get('stop_price', 0)
        win_rate = s.get('历史胜率', 'N/A')
        pf = s.get('回测统计', {}).get('profit_factor', 'N/A')

        lines.append(f"{grade_icon} {grade}级 {name} ({code})")
        if sector:
            lines.append(f"  板块: {sector} {s_trend}{'+' if s_pct >= 0 else ''}{s_pct}%")
        lines.append(f"  入场: {entry} | 止损: {stop}")
        lines.append(f"  胜率: {win_rate} | 盈亏比: {pf}")
        # 加分项
        bonuses = s.get('sop_bonuses', [])
        if bonuses:
            lines.append(f"  ⭐ {'、'.join(bonuses)}")
        lines.append("")

    total_a = sum(1 for s in stock_list if s.get('sop_grade') == 'A')
    total_b = sum(1 for s in stock_list if s.get('sop_grade') == 'B')
    lines.append(f"A级{total_a}只 | B级{total_b}只")

    body = "\n".join(lines)
    logger.info(f"Notification: {body}")

    from core.notifier import notifier
    try:
        asyncio.run(notifier.send(title, body, channels=["bark"]))
    except Exception as e:
        logger.error(f"Push notification failed: {e}")

    return body



class IntradaySentinel:
    def __init__(self):
        self.last_top_5 = []
        self.thread = None
        self._stop = False
        self.schedule_times = ["14:20"]
        self.triggered_today = set()

    def update_schedule(self, times_str: Optional[str] = None):
        """实时更新调度时间点"""
        if times_str is None:
            times_str = get_setting("sentinel_schedule_times", "14:20")
        self.schedule_times = [t.strip() for t in times_str.split(",") if t.strip()]
        logger.info(f"Sentinel schedule updated to: {self.schedule_times}")

    def _load_schedule(self):
        # 保持兼容性调用 update_schedule
        self.update_schedule()

    def start(self):
        self._load_schedule()
        self.triggered_today.clear()
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop:
            now = datetime.now()
            current_time = now.strftime("%H:%M")

            # Reset tracking every day
            if current_time == "00:00":
                self.triggered_today.clear()

            if current_time in self.schedule_times and current_time not in self.triggered_today:
                logger.info(f"Sentinel Triggered at {current_time}: Automated check...")
                self.triggered_today.add(current_time)
                try:
                    from routers.scan import run_market_scan_task
                    results = run_market_scan_task(local_only=False)
                    if results:
                        self.last_top_5 = results[:5]
                        send_intraday_notification(self.last_top_5)

                    # --- 新增: 拟合实盘风控检查 ---
                    logger.info("Sentinel: Running Paper Trading Wind Control...")
                    from routers.paper_trade import run_wind_control
                    wc_res = run_wind_control()
                    if wc_res.get("closed_count", 0) > 0:
                        logger.info(f"Wind Control: Closed {wc_res['closed_count']} positions.")
                except Exception as e:
                    logger.error(f"Sentinel Scan Error: {e}")

            if now.minute % 10 == 0 and now.second < 30:
                self._load_schedule()

            time.sleep(30)


sentinel = IntradaySentinel()


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

app.include_router(market_router)
app.include_router(sync_router)
app.include_router(scan_router)
app.include_router(paper_router)
app.include_router(stock_router)
app.include_router(settings_router)
app.include_router(alert_router)

@app.get("/api/market/regime")
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
