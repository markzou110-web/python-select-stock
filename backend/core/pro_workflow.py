from typing import Any, Dict, List


def classify_strategy_health(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Turn verified return metrics into an actionable strategy operating state."""
    signals = int(metrics.get("signals") or 0)
    expected = float(metrics.get("expected_return") or 0)
    ci95_high = float(metrics.get("ci95_high") or 0)
    win_rate = float(metrics.get("win_rate") or 0)

    if signals < 20:
        return {"status": "OBSERVE", "weight": 0.5, "reason": f"仅 {signals} 个有效样本，继续观察"}
    if expected < 0 and (ci95_high < 0 or expected <= -1):
        return {"status": "PAUSED", "weight": 0.0, "reason": "近期净期望显著为负，暂停进入可交易池"}
    if expected < 0 or win_rate < 45:
        return {"status": "DOWNWEIGHT", "weight": 0.5, "reason": "近期验证偏弱，降低策略权重并仅保留高质量信号"}
    return {"status": "ACTIVE", "weight": 1.0, "reason": "近期验证保持正期望，可正常使用"}


def recommend_strategy_template(
    market_regime: str = "UNKNOWN",
    risk_status: str = "ok",
    recent_win_rate: float = 0,
    strategy_health: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Choose a scan template profile for the next run based on regime and recent quality."""
    regime = (market_regime or "UNKNOWN").upper()
    health_status = (strategy_health or {}).get("status")
    defensive = (
        risk_status in {"warning", "error"}
        or recent_win_rate < 45
        or regime in {"DEFENSIVE", "CRITICAL"}
        or health_status in {"DOWNWEIGHT", "PAUSED"}
    )

    if defensive:
        return {
            "profile": "防守精选",
            "strategy_type": "pine",
            "params": {
                "strategy_type": "pine",
                "pine_min_signals": 4,
                "vol_multiplier": 1.8,
                "rsi_min": 58,
                "turnover_min": 4.0,
                "use_weekly": True,
                "max_open_gap_pct": 2.0,
            },
            "reason": (strategy_health or {}).get("reason") or "市场或组合风险偏高，优先减少信号数量并提高右侧确认要求",
            "strategy_health": strategy_health or {},
        }

    if regime in {"BULL", "RISK_ON", "CONFIRM"} or recent_win_rate >= 55:
        return {
            "profile": "进攻共振",
            "strategy_type": "tv_dual_strict",
            "params": {
                "strategy_type": "tv_dual_strict",
                "pine_min_signals": 3,
                "vol_multiplier": 1.5,
                "rsi_min": 55,
                "turnover_min": 3.0,
                "use_weekly": False,
                "max_open_gap_pct": 3.0,
            },
            "reason": "近期验证质量较好，可保持共振策略并允许正常触发频率",
            "strategy_health": strategy_health or {},
        }

    return {
        "profile": "均衡观察",
        "strategy_type": "consensus",
        "params": {
            "strategy_type": "consensus",
            "vol_multiplier": 1.6,
            "rsi_min": 55,
            "turnover_min": 3.5,
            "use_weekly": True,
            "max_open_gap_pct": 2.5,
        },
        "reason": "市场状态不明朗，使用均衡参数等待板块和量能进一步确认",
        "strategy_health": strategy_health or {},
    }


def build_alert_priority(level: str, pl_pct: float, reasons: List[str]) -> Dict[str, Any]:
    """Map raw alert fields to actionable priority and a stable de-duplication key."""
    text = " ".join(reasons or [])
    if level == "critical" or pl_pct <= -8 or "止损" in text:
        return {
            "priority": "P0",
            "label": "必须立即处理",
            "dedup_minutes": 20,
            "action_line": "低于风控价立即减仓或平仓，不等待尾盘",
        }
    if level == "warning" or pl_pct <= -4 or "EMA20" in text:
        return {
            "priority": "P1",
            "label": "需要盘中决策",
            "dedup_minutes": 40,
            "action_line": "若无法收回关键线，尾盘前减仓；若放量转强则继续观察",
        }
    return {
        "priority": "P2",
        "label": "观察提醒",
        "dedup_minutes": 60,
        "action_line": "记录原因，等待下一次价格确认",
    }


def build_premarket_checklist(
    health: Dict[str, Any],
    exposure: Dict[str, Any],
    data_quality: Dict[str, Any],
    template_recommendation: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a concise pre-market checklist from existing operational diagnostics."""
    checks = []
    blockers = []

    health_status = health.get("status", "error")
    checks.append({
        "key": "system_health",
        "label": "系统健康",
        "status": health_status,
        "action": "修复数据库/数据源/Bark 配置后再运行扫描" if health_status == "error" else "可进入盘前流程",
    })
    if health_status == "error":
        blockers.append("系统健康检查失败")

    exposure_status = exposure.get("status", "ok")
    checks.append({
        "key": "portfolio_exposure",
        "label": "组合暴露",
        "status": exposure_status,
        "action": "今日只处理减仓/止损，暂停新增实盘" if exposure_status == "warning" else "仓位预算允许新增候选",
    })

    local_data = data_quality.get("local_data") or {}
    quality_summary = local_data.get("summary") or {}
    repair_count = len([
        item for item in (quality_summary.get("abnormal_move_samples") or [])
        if item.get("likely_reason") == "suspected_corporate_action_gap"
    ])
    data_status = "warning" if repair_count else data_quality.get("status", "ok")
    checks.append({
        "key": "data_quality",
        "label": "行情质量",
        "status": data_status,
        "action": f"先处理 {repair_count} 条疑似复权断点" if repair_count else "可用于扫描和复盘",
    })

    checks.append({
        "key": "template",
        "label": "今日模板",
        "status": "ok",
        "action": f"建议使用 {template_recommendation.get('profile')}：{template_recommendation.get('reason')}",
    })

    status = "blocked" if blockers else ("warning" if any(item["status"] == "warning" for item in checks) else "ready")
    return {"status": status, "checks": checks, "blockers": blockers}
