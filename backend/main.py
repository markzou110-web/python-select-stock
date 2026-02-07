import os
import sys
import time
import threading
import asyncio
import socket
from fastapi import FastAPI, BackgroundTasks

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

# Load environment
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Force direct connection
def force_direct_connection():
    os.environ['HTTPS_PROXY'] = ''
    os.environ['HTTP_PROXY'] = ''
    os.environ['all_proxy'] = ''
    os.environ['ALL_PROXY'] = ''
force_direct_connection()

socket.setdefaulttimeout(30)

from core.db import get_db_engine, init_db, get_setting, save_setting
from core.db_news import init_news_tables
from core.theme_tracker import ThemeTracker
from core.risk_detector import RiskDetector
from core.data import get_index_data, get_hot_sectors

# Global instances
theme_tracker = None
risk_detector = None

from services.sentinel_service import IntradaySentinel
from services.scan_service import run_market_scan
from services.news_service import background_news_sync

# Initialize Sentinel with the scan function
sentinel = IntradaySentinel(run_market_scan)

app = FastAPI(title="Alpha Vision API", version="6.0.0")

# CORS Setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and include routers
from routes import stock_routes, sync_routes, news_routes, trade_routes, settings_routes, backtest_routes

app.include_router(stock_routes.router)
app.include_router(sync_routes.router)
app.include_router(news_routes.router)
app.include_router(trade_routes.router)
app.include_router(settings_routes.router)
app.include_router(backtest_routes.router)

@app.on_event("startup")
async def startup_event():
    print("🏗️ Initializing database...")
    init_db()
    init_news_tables()

    print("🎯 Initializing theme tracker & risk detector...")
    global theme_tracker, risk_detector
    engine = get_db_engine()
    if engine:
        theme_tracker = ThemeTracker(engine)
        risk_detector = RiskDetector(engine)
        # Pass instances to routes
        news_routes.theme_tracker = theme_tracker
        news_routes.risk_detector = risk_detector
        from services import news_service
        news_service.theme_tracker = theme_tracker

    print("🚀 Starting Intraday Sentinel...")
    if get_setting("sentinel_time") is None:
        save_setting("sentinel_time", "14:30")
    sentinel.start()

    if theme_tracker:
        print("🔥 Running initial theme update...")
        try:
            theme_tracker.update_themes()
        except Exception as e:
            print(f"⚠️ Initial theme update failed: {e}")

        print("📰 Starting background news sync thread...")
        threading.Thread(target=background_news_sync, daemon=True).start()

    print("🔥 Pre-warming Index and Sector cache...")
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, get_index_data)
    loop.run_in_executor(None, get_hot_sectors)

@app.get("/api/health")
def health_check():
    return {"status": "healthy", "timestamp": time.time()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
