"""
Settings router - system configuration and test endpoints.

Extracted from api.py.
"""
from fastapi import APIRouter
from typing import Dict, Any

from core.config import config
from core.db import save_setting, get_setting

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
def get_settings_api() -> Dict[str, Any]:
    """获取系统设置（不暴露敏感信息）"""
    return config.get_bark_safe_status()


@router.post("/settings")
def save_settings_api(data: dict):
    if "sentinel_time" in data:
        save_setting("sentinel_time", data["sentinel_time"])
    if "sentinel_schedule_times" in data:
        save_setting("sentinel_schedule_times", data["sentinel_schedule_times"])
    return {"status": "success"}


@router.get("/settings/webhook")
def get_webhook_settings() -> Dict[str, Any]:
    """获取 WebHook 推送渠道配置"""
    return {
        "bark": {"configured": config.is_bark_configured()},
        "feishu": {"url": get_setting("feishu_webhook_url", "")},
        "dingtalk": {"url": get_setting("dingtalk_webhook_url", "")},
        "wecom": {"url": get_setting("wecom_webhook_url", "")},
    }


@router.post("/settings/webhook")
def save_webhook_settings(data: dict):
    """保存 WebHook 推送渠道配置"""
    channels = {
        "feishu": "feishu_webhook_url",
        "dingtalk": "dingtalk_webhook_url",
        "wecom": "wecom_webhook_url",
    }
    saved = []
    for channel, db_key in channels.items():
        if channel in data:
            save_setting(db_key, data[channel])
            saved.append(channel)
    return {"status": "success", "saved": saved}


@router.post("/settings/webhook/test")
def test_webhook(channel: str = "bark") -> Dict[str, Any]:
    """测试推送渠道连通性"""
    from core.notifier import notifier

    title = "Alpha Vision 测试"
    body = "这是一条测试消息，确认推送渠道已连通。"

    try:
        notifier.send(title, body, channels=[channel])
        return {"status": "success", "message": f"Test message sent via {channel}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/test/push")
def test_push_notification() -> Dict[str, Any]:
    """测试 Bark 推送功能"""
    from core.notifier import notifier

    mock_data = [
        {"code": "600519", "name": "测试茅台", "price": 1800.0},
        {"code": "300750", "name": "测试时代", "price": 450.0}
    ]

    names = [s['name'] for s in mock_data]
    codes = [s['code'] for s in mock_data]
    title = "Alpha Vision 哨兵提醒"
    body = f"【测试推送】\n发现 {len(names)} 只标的：\n" + "、".join([f"{n}({c})" for n, c in zip(names, codes)])

    try:
        notifier.send(title, body)
        return {"status": "success", "message": f"Push sent: {body}"}
    except Exception as e:
        return {"status": "error", "message": "Internal server error"}
