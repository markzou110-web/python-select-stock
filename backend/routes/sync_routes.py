from fastapi import APIRouter, BackgroundTasks
from core.state import sync_progress
from services.sync_service import background_sync_task

router = APIRouter(prefix="/api/sync", tags=["sync"])

@router.get("/status")
def get_sync_status():
    """Returns the current synchronization progress."""
    return sync_progress

@router.post("/daily")
def start_sync(background_tasks: BackgroundTasks):
    """Triggers a background data synchronization task."""
    if sync_progress["is_running"]:
        return {"status": "already_running", "progress": sync_progress}
    
    background_tasks.add_task(background_sync_task)
    return {"status": "started", "message": "Background sync task initiated."}
