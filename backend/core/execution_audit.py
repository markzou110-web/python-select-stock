"""Execution-decision audit labels; classification does not alter stock selection."""
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List

from sqlalchemy import bindparam, text

from core.risk_constants import (
    PERSISTENT_B_SHADOW_LOOKBACK_DAYS,
    PERSISTENT_B_SHADOW_MAX_DAILY_RISE_PCT,
    PERSISTENT_B_SHADOW_MIN_PUSH_DAYS,
    PERSISTENT_B_SHADOW_MIN_QUALITY_SCORE,
    PERSISTENT_B_SHADOW_MIN_SECTOR_ALIGNMENT,
    STRONG_EXCEPTION_MIN_OPPORTUNITY_SCORE,
    STRONG_EXCEPTION_MIN_RISK_REWARD,
)


HARD_BLOCKER_MARKERS = (
    "回避", "结构失效", "结构不进入交易池", "冲高回落", "涨停/近涨停", "高开",
    "异常价格", "板块下跌", "禁止实盘", "禁止追涨", "关键点时字段不完整", "仅供研究",
)
WAIT_BLOCKER_MARKERS = (
    "确认价", "量能未确认", "次日确认", "等待回踩", "换手不足", "交易计划未确认",
    "站稳未确认", "距冻结确认价",
)
SOFT_BLOCKER_MARKERS = (
    "降级观察", "市场退潮", "板块退潮", "策略近期负期望", "综合机会分<",
    "资金流出", "资金流数据缺失", "原始策略分<", "周线中性", "周线交易区间",
    "强板块后排", "个股适配不足", "5日涨幅偏高", "涨幅偏高且质量未确认",
)


def classify_trade_blockers(blockers: Iterable[Any]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {"hard": [], "wait": [], "soft": [], "other": []}
    for raw in blockers or []:
        blocker = str(raw).strip()
        if not blocker:
            continue
        if any(marker in blocker for marker in HARD_BLOCKER_MARKERS):
            group = "hard"
        elif any(marker in blocker for marker in WAIT_BLOCKER_MARKERS):
            group = "wait"
        elif any(marker in blocker for marker in SOFT_BLOCKER_MARKERS):
            group = "soft"
        else:
            group = "other"
        groups[group].append(blocker)
    return {key: list(dict.fromkeys(values)) for key, values in groups.items()}


def load_recent_push_counts(
    engine,
    codes: Iterable[Any],
    strategy_type: str,
    as_of: Any,
    lookback_days: int = PERSISTENT_B_SHADOW_LOOKBACK_DAYS,
) -> Dict[str, int]:
    """Count distinct point-in-time push days, including the current scan day."""
    normalized = sorted({str(code).zfill(6) for code in codes if code not in (None, "")})
    if engine is None or not normalized:
        return {code: 1 for code in normalized}
    try:
        current = datetime.fromisoformat(str(as_of)[:10]).date()
    except (TypeError, ValueError):
        current = date.today()
    cutoff = current - timedelta(days=max(1, int(lookback_days)))
    stmt = text("""
        SELECT code, DATE(COALESCE(data_date, date)) AS signal_date
        FROM scan_history
        WHERE code IN :codes
          AND strategy_type = :strategy_type
          AND DATE(COALESCE(data_date, date)) BETWEEN :cutoff AND :current
        GROUP BY code, DATE(COALESCE(data_date, date))
    """).bindparams(bindparam("codes", expanding=True))
    history: Dict[str, set] = {code: {current.isoformat()} for code in normalized}
    try:
        with engine.connect() as conn:
            rows = conn.execute(stmt, {
                "codes": normalized,
                "strategy_type": strategy_type,
                "cutoff": cutoff.isoformat(),
                "current": current.isoformat(),
            }).fetchall()
        for code, signal_date in rows:
            history.setdefault(str(code).zfill(6), {current.isoformat()}).add(str(signal_date)[:10])
    except Exception:
        return {code: 1 for code in normalized}
    return {code: len(days) for code, days in history.items()}


def assess_persistent_b_shadow(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate a repeated high-quality B signal without granting trade permission."""
    groups = classify_trade_blockers(candidate.get("trade_blockers") or [])
    checks = {
        "strict_strategy": str(candidate.get("strategy_type") or "") == "tv_dual_strict",
        "grade_b": str(candidate.get("sop_grade") or "") == "B",
        "quality": float(candidate.get("sop_quality_score") or 0) >= PERSISTENT_B_SHADOW_MIN_QUALITY_SCORE,
        "repeated_push": int(candidate.get("recent_push_days") or 0) >= PERSISTENT_B_SHADOW_MIN_PUSH_DAYS,
        "opportunity": float(candidate.get("trade_opportunity_score") or 0) >= STRONG_EXCEPTION_MIN_OPPORTUNITY_SCORE,
        "risk_reward": float(candidate.get("pa_risk_reward") or candidate.get("risk_reward") or 0) >= STRONG_EXCEPTION_MIN_RISK_REWARD,
        "sector_alignment": float(candidate.get("sector_alignment_score") or 0) >= PERSISTENT_B_SHADOW_MIN_SECTOR_ALIGNMENT,
        "not_extended": float(candidate.get("涨幅%") or candidate.get("pct") or 0) <= PERSISTENT_B_SHADOW_MAX_DAILY_RISE_PCT,
        "no_hard_blocker": not groups["hard"],
    }
    # ponytail: 高涨幅样本仍需进入反事实队列才能验证“等回踩”是否有效；
    # 生产权限不变，若未来要实盘化必须升级为真实限价/回踩成交模型。
    eligible = all(passed for check, passed in checks.items() if check != "not_extended")
    entry_mode = "WAIT_PULLBACK" if not checks["not_extended"] else "CONFIRMATION_REVIEW"
    return {
        "eligible": eligible,
        "mode": "SHADOW",
        "entry_mode": entry_mode,
        "checks": checks,
        "hard_blockers": groups["hard"],
        "wait_blockers": groups["wait"],
        "soft_blockers": groups["soft"] + groups["other"],
        "instruction": "影子等待回踩，不可交易" if eligible and entry_mode == "WAIT_PULLBACK" else (
            "影子验证，不可交易" if eligible else "未进入持续B级影子队列"
        ),
    }
