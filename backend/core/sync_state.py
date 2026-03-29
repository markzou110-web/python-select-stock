"""
Sync state module - shared state for data synchronization background tasks.
"""
import threading

sync_progress_lock = threading.Lock()
sync_progress = {
    "is_running": False,
    "total": 0,
    "current": 0,
    "success": 0,
    "fail": 0,
    "start_time": None,
    "status_text": "等待中...",
    "stop_requested": False
}


def update_sync_progress(current: int, total: int, success: int, failed: int, overall_total: int):
    """Update sync progress callback."""
    with sync_progress_lock:
        sync_progress["current"] = current
        sync_progress["success"] = success
        sync_progress["fail"] = failed
        sync_progress["total"] = overall_total
        if sync_progress["stop_requested"]:
            sync_progress["status_text"] = f"正在停止... ({current}/{total})"
            return False
        sync_progress["status_text"] = f"正在同步... ({current}/{total})"
        return True
