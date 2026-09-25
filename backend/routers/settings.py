"""
Settings router - system configuration and test endpoints.

Extracted from api.py.
"""
import re
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from core.config import config
from core.db import save_setting, get_setting
from core.bark_scan_selection import (
    BARK_SCAN_STRATEGIES,
    BARK_TV_OBSERVATION_STRATEGY,
    normalize_bark_scan_strategy,
)
from core.sync_scheduler import SYNC_SCHEDULE_DEFAULT

router = APIRouter(prefix="/api", tags=["settings"])

# 修复 R3-6: schedule_times 格式校验。防止垃圾值（如 "25:99"/""/"noon"）
# 静默禁用 sentinel/sync 扫描调度。
_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _validate_schedule_times(raw: str) -> str:
    """校验逗号分隔的 HH:MM 时间字符串。无效则返回默认值并记录。"""
    if not raw or not raw.strip():
        raise HTTPException(status_code=400, detail="schedule_times不能为空")
    times = [t.strip() for t in str(raw).split(",") if t.strip()]
    valid = [t for t in times if _TIME_PATTERN.match(t)]
    if not valid:
        raise HTTPException(status_code=400, detail="schedule_times 格式无效，需为 HH:MM 逗号分隔（如 14:20,14:50）")
    return ",".join(valid)


@router.get("/settings")
def get_settings_api() -> Dict[str, Any]:
    """获取系统设置（不暴露敏感信息）"""
    return {
        "configured": config.is_bark_configured(),
        "bark_key": get_setting("bark_key", ""),
        "bark_scan_strategy": normalize_bark_scan_strategy(
            get_setting("bark_scan_strategy", BARK_TV_OBSERVATION_STRATEGY)
        ),
        "bark_scan_strategy_options": [
            {"value": value, **details}
            for value, details in BARK_SCAN_STRATEGIES.items()
        ],
        "sentinel_schedule_times": get_setting(
            "sentinel_schedule_times", config.SENTINEL_SCHEDULE_TIMES
        ),
        "market_sync_schedule_times": get_setting("market_sync_schedule_times", SYNC_SCHEDULE_DEFAULT),
    }


@router.post("/settings")
def save_settings_api(data: dict):
    if "bark_key" in data:
        save_setting("bark_key", data["bark_key"])
    if "bark_scan_strategy" in data:
        strategy = str(data["bark_scan_strategy"] or "").strip()
        if strategy not in BARK_SCAN_STRATEGIES:
            raise HTTPException(status_code=400, detail="不支持的 Bark 选股策略")
        save_setting("bark_scan_strategy", strategy)
    if "sentinel_schedule_times" in data:
        # 修复 R3-6: 校验格式，防止垃圾值静默禁用扫描
        validated = _validate_schedule_times(data["sentinel_schedule_times"])
        save_setting("sentinel_schedule_times", validated)

        # 实时通知后台哨兵更新调度时间
        try:
            from api import sentinel
            sentinel.update_schedule(validated)
        except Exception:
            pass
    if "market_sync_schedule_times" in data:
        validated_sync = _validate_schedule_times(data["market_sync_schedule_times"])
        save_setting("market_sync_schedule_times", validated_sync)
        try:
            from core.sync_scheduler import market_sync_scheduler
            market_sync_scheduler.update_schedule(data["market_sync_schedule_times"])
        except Exception:
            pass
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
        import asyncio
        result = asyncio.run(notifier.send(title, body, channels=[channel]))
        sent = bool(result.get(channel.lower().strip()))
        return {
            "status": "success" if sent else "error",
            "message": f"Test message sent via {channel}" if sent else f"Test message failed via {channel}",
            "delivery": result,
        }
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
        import asyncio
        result = asyncio.run(notifier.send(title, body, channels=["bark"]))
        sent = bool(result.get("bark"))
        return {
            "status": "success" if sent else "error",
            "message": f"Push sent: {body}" if sent else "Bark push failed and was queued for retry",
            "delivery": result,
        }
    except Exception as e:
        return {"status": "error", "message": "Internal server error"}
