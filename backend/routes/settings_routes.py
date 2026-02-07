from fastapi import APIRouter, HTTPException
from core.db import get_settings_all, save_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])

@router.get("/")
def get_settings_api():
    try:
        settings = get_settings_all()
        return settings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/")
def save_settings_api(data: dict):
    try:
        for k, v in data.items():
            save_setting(k, str(v))
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
