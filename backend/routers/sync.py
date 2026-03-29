from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, BackgroundTasks

from core.logging_config import logger
from core.sync_state import sync_progress, sync_progress_lock, update_sync_progress
from core.data import get_sector_map
from core.db import get_db_engine, init_db

router = APIRouter(prefix="/api/sync", tags=["sync"])


def background_sync_task():
    """Background task to sync stock data."""
    global sync_progress

    with sync_progress_lock:
        sync_progress["is_running"] = True
        sync_progress["stop_requested"] = False
        sync_progress["start_time"] = datetime.now().isoformat()
        sync_progress["success"] = 0
        sync_progress["fail"] = 0
        sync_progress["current"] = 0
        sync_progress["status_text"] = "正在初始化板块映射..."

    try:
        from core.multi_source_sync import MultiSourceSync
        from core.db import get_db_engine, init_db
        from sqlalchemy import text

        engine = get_db_engine()
        if not engine:
            logger.error("Database connection failed")
            return

        init_db(engine)

        with sync_progress_lock:
            sync_progress["status_text"] = "正在初始化多数据源同步..."

        syncer = MultiSourceSync()

        logger.info("Checking data source status...")
        report = syncer.manager.get_status_report()
        available_count = sum(1 for info in report.values() if info['status'] == 'available')
        logger.info(f"Available data sources: {available_count}/{len(report)}")

        with sync_progress_lock:
            sync_progress["status_text"] = "正在获取最新股票基础列表..."

        logger.info("Fetching fresh stock list from data sources...")
        fresh_list = syncer.get_stock_list()
        if fresh_list is not None and not fresh_list.empty:
            from core.db import save_stock_basic
            save_stock_basic(fresh_list, engine)
            all_codes = fresh_list['code'].tolist()
            logger.info(f"Updated stock_basic with {len(fresh_list)} records from cloud.")

            with sync_progress_lock:
                sync_progress["status_text"] = "正在补充行业/板块映射..."
            logger.info("Filling missing sector information from industry boards...")
            get_sector_map()
        else:
            logger.warning("Cloud fetch failed, using DB fallback for code list.")
            with engine.connect() as conn:
                all_codes_result = conn.execute(text("SELECT code FROM stock_basic ORDER BY code")).fetchall()
                if not all_codes_result:
                    all_codes_result = conn.execute(text("SELECT DISTINCT code FROM daily_k ORDER BY code")).fetchall()
                all_codes = [row[0] for row in all_codes_result]

            all_codes = [c for c in all_codes if str(c).startswith(('60', '688', '00', '30'))]

        if not all_codes:
            logger.error("No stocks found in database or network list")
            with sync_progress_lock:
                sync_progress["is_running"] = False
            return

        with sync_progress_lock:
            sync_progress["total"] = len(all_codes)
            sync_progress["status_text"] = f"正在同步 {len(all_codes)} 只股票..."

        logger.info(f"Starting sync for {len(all_codes)} stocks using multi-source...")

        def should_stop():
            with sync_progress_lock:
                return sync_progress.get("stop_requested", False)

        results = syncer.sync_batch(
            all_codes,
            delay_range=(0.0, 0.1),
            progress_callback=lambda current, total, success, failed: update_sync_progress(
                current, total, success, failed, len(all_codes)
            ),
            max_workers=8,
            check_stop=should_stop
        )

        if sync_progress["stop_requested"]:
            logger.info("Sync task was stopped by user.")
            with sync_progress_lock:
                sync_progress["status_text"] = "已停止"
        else:
            logger.info(f"Sync completed normally: {results}")
            with sync_progress_lock:
                sync_progress["status_text"] = f"同步完成: 成功{results['success']}, 跳过{results.get('skipped', 0)}, 失败{results['failed']}"

    except Exception as e:
        logger.error(f"Background sync error: {e}")
        import traceback
        traceback.print_exc()
        with sync_progress_lock:
            sync_progress["status_text"] = f"同步出错: {str(e)[:50]}"
    finally:
        with sync_progress_lock:
            sync_progress["is_running"] = False
            sync_progress["stop_requested"] = False


@router.post("/stop")
def stop_sync():
    """Stop the ongoing synchronization."""
    with sync_progress_lock:
        if not sync_progress["is_running"]:
            return {"status": "not_running"}
        sync_progress["stop_requested"] = True
        sync_progress["status_text"] = "停止指令已发送，等待当前股票处理完成..."
    return {"status": "stopping"}


@router.post("/daily")
def start_sync(background_tasks: BackgroundTasks):
    """Start daily data synchronization."""
    with sync_progress_lock:
        if sync_progress["is_running"]:
            return {"status": "already_running", "progress": sync_progress}

    background_tasks.add_task(background_sync_task)
    return {"status": "started"}


@router.get("/status")
def get_sync_status() -> Dict[str, Any]:
    """Get current synchronization progress."""
    with sync_progress_lock:
        return sync_progress.copy()
