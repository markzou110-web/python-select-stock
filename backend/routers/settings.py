"""
Settings router - system configuration and test endpoints.

Extracted from api.py.
"""
from fastapi import APIRouter
from typing import Dict, Any

from core.config import config
from core.db import save_setting

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
def get_settings_api() -> Dict[str, Any]:
    """获取系统设置（不暴露敏感信息）"""
    return config.get_bark_safe_status()


@router.post("/settings")
def save_settings_api(data: dict):
    if "sentinel_time" in data:
        save_setting("sentinel_time", data["sentinel_time"])
        # Note: sentinel.trigger_time update is handled in api.py lifespan
    return {"status": "success"}


@router.get("/test/push")
def test_push_notification() -> Dict[str, Any]:
    """测试 Bark 推送功能"""
    import requests
    from core.config import config
    from core.logging_config import logger

    BARK_KEY = config.BARK_KEY

    mock_data = [
        {"code": "600519", "name": "测试茅台", "price": 1800.0},
        {"code": "300750", "name": "测试时代", "price": 450.0}
    ]

    names = [s['name'] for s in mock_data]
    codes = [s['code'] for s in mock_data]
    title = "Alpha Vision 哨兵提醒"
    body = f"【测试推送】\n发现 {len(names)} 只标的：\n" + "、".join([f"{n}({c})" for n, c in zip(names, codes)])

    try:
        if config.is_bark_configured():
            url = config.BARK_URL_TEMPLATE.format(key=BARK_KEY, title=title, body=body)
            requests.get(url, timeout=5)
            return {"status": "success", "message": f"Push sent: {body}"}
        else:
            return {"status": "success", "message": "Bark not configured. Push skipped."}
    except Exception as e:
        logger.error(f"Test push error: {e}")
        return {"status": "error", "message": "Internal server error"}
