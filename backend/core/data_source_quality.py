from typing import Any, Dict


def build_data_source_quality_report() -> Dict[str, Any]:
    """Return current multi-source availability and a concise professional diagnosis."""
    try:
        from core.multi_source_sync import MultiSourceSync

        syncer = MultiSourceSync()
        sources = syncer.manager.get_status_report()
    except Exception as exc:
        return {
            "status": "error",
            "available_count": 0,
            "sources": {},
            "message": f"数据源状态检查失败: {str(exc)[:120]}",
            "recommendations": ["检查网络、代理和数据源依赖"],
        }

    available = [name for name, info in sources.items() if info.get("status") == "available"]
    degraded = [name for name, info in sources.items() if info.get("status") != "available"]
    status = "ok" if len(available) >= 2 else ("warn" if available else "error")

    recommendations = []
    if len(available) < 2:
        recommendations.append("至少保持两个可用数据源，避免单点数据故障")
    if degraded:
        recommendations.append(f"关注异常数据源: {', '.join(degraded)}")

    return {
        "status": status,
        "available_count": len(available),
        "total_count": len(sources),
        "sources": sources,
        "message": f"可用数据源 {len(available)}/{len(sources)}",
        "recommendations": recommendations,
    }
