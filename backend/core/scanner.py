"""
Alpha Vision - Core Market Scanning Engine.
Decoupled business logic from FastAPI Routers.
"""
import time
import os
import re
import hashlib
import pandas as pd
from datetime import datetime, timedelta, date
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import HTTPException
import akshare as ak
from sqlalchemy import bindparam, text

from core.logging_config import logger
from core.config import config
from core.ws_manager import manager as ws_manager
from core.db import (
    get_db_engine, save_scan_results, load_from_db, save_scan_audit_log,
    save_point_in_time_snapshot, load_active_event_catalysts,
)
from core.data import (
    get_market_snapshot, get_index_hist, get_sector_map, get_sector_trends, get_market_regime,
    is_snapshot_stale,
)
from core.indicators import (
    calculate_indicators, calculate_pine_indicators,
    get_weekly_indicators, batch_calculate_indicators
)
from core.strategy import (
    check_strategy, check_pine_strategy, check_tv_zp_strategy, check_tv_dual_strategy, check_tv_reversal_watch, check_consensus_strategy,
    calculate_historical_win_rate, calculate_pine_win_rate, calculate_tv_zp_win_rate, calculate_tv_dual_win_rate, calculate_consensus_win_rate,
    calculate_research_pattern_win_rate,
    STRATEGY_LOGIC_VERSION, BACKTEST_ENGINE_VERSION, EXIT_RULE_VERSION
)
from core.price_action import analyze_price_action
from core.timeframe_context import build_completed_timeframe_context
from core.chip_distribution import build_chip_distribution
from core.pa_execution_policy import classify_price_action_execution
from core.risk_engine import compute_paper_risk_levels
from core.risk_constants import (
    A_EOD_CONTROLLED_POLICY_VERSION,
    A_EOD_MAX_5D_GAIN_PCT,
    A_EOD_MAX_ENTRY_EXTENSION_PCT,
    A_EOD_MIN_QUALITY_SCORE,
    A_EOD_MIN_RISK_REWARD,
    A_MINUS_TRIAL_MAX_DAILY_RISE_PCT,
    A_MINUS_TRIAL_MIN_PRICE_ACTION_SCORE,
    A_MINUS_TRIAL_MIN_QUALITY_SCORE,
    A_MINUS_TRIAL_MIN_RISK_REWARD,
    A_MINUS_TRIAL_POLICY_VERSION,
    BACKTEST_STOP_LOSS_PCT,
    EVIDENCE_GATE_MODE,
    MONTHLY_SECTOR_LEADERS_PER_SECTOR,
    SOP_A_GRADE_5D_PENALTY_PER_PCT,
    SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT,
    SOP_A_GRADE_MAX_5D_GAIN_PCT,
    SOP_A_GRADE_MIN_SCORE,
    TRADE_GATE_POLICY_VERSION,
    TRADE_GATE_V2_ENABLED,
    TRADE_GATE_V2_SOFT_CONDITION_MARKERS,
    PRIMARY_TV_STRATEGY,
    TV_MA_ONLY_MIN_PA_SCORE,
    TV_SIGNAL_WARMUP_DAYS,
    UNIVERSE_LIQUIDITY_LOOKBACK_DAYS,
    UNIVERSE_MIN_AVG_AMOUNT_YUAN,
    UNIVERSE_NEW_ONE_PRICE_MAX_DAYS,
)  # 与实盘硬止损同源，保证回测胜率反映真实规则
from core.scan_preflight import build_scan_preflight
from core.trading_calendar import shift_a_share_trading_date

# 数据预检熔断开关：True 时，若 preflight 报告 blocking（数据异常/陈旧），
# perform_market_scan 会中止并返回空结果，避免坏数据静默产生假信号。
# 调试/紧急时可设为 False 绕过。
SCAN_PREFLIGHT_ENFORCE = True

# 大盘状态自适应阈值开关（改动 #7）：True 时，perform_market_scan 根据大盘状态
# (OFFENSIVE/CRITICAL/DEFENSIVE → bull/bear/volatile) 自动收紧/放宽 threshold、
# vol_multiplier、rsi_min、stop_loss_pct 等参数（启用此前未使用的 REGIME_PARAMS）。
# 调试/紧急时可设为 False 回退到旧的固定参数。
SCAN_REGIME_ADAPTIVE = True

# 改动 #17：失败样本闭环。扫描时预查近 FAILURE_LOOKBACK_DAYS 天内同代码同策略的
# 失败次数，超过 FAILURE_VETO_MIN_COUNT 次的候选在 SOP 评级中一票否决（降级为 D）。
# 改动 A4：原阈值 2 次过严——活跃票 90 天内被止损 2 次很常见（尤其弱市期），
# 提到 3 次避免误杀正常波动。同时改为按 (code, strategy_type) 配对（见 _inject_failure_pattern）。
FAILURE_LOOKBACK_DAYS = 90
FAILURE_VETO_MIN_COUNT = 3
# 改动 A5：小样本胜率折扣。无 Wilson 下界时对原始胜率打此折扣，
# 惩罚样本量不足（如样本=3、胜率=100% 的票折扣后=70%，更接近真实置信度）。
SMALL_SAMPLE_WIN_RATE_DISCOUNT = 0.7

_DYNAMIC_REJECTION_VALUE = re.compile(
    r"[（(]\s*[-+]?\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?\s*(?:%|x|天)?\s*[)）]",
    re.IGNORECASE,
)
_REJECTION_REASON_LIMIT = 50
_REJECTION_REASON_SAMPLE_LIMIT = 3
_REJECTION_REASON_CODES = (
    ("距60日低点涨幅不在", "DISTANCE_FROM_60D_LOW_OUT_OF_RANGE"),
    ("距20日低点涨幅不在", "DISTANCE_FROM_20D_LOW_OUT_OF_RANGE"),
    ("底部量能尚未收缩", "BOTTOM_VOLUME_NOT_CONTRACTED"),
    ("当日量能已过度放大", "CURRENT_VOLUME_OVEREXPANDED"),
    ("底部波动尚未收敛", "BOTTOM_VOLATILITY_NOT_CONTRACTED"),
    ("EMA20仍快速下行", "EMA20_FALLING_FAST"),
    ("量能尚未开始确认", "VOLUME_NOT_CONFIRMED"),
    ("历史数据不足", "INSUFFICIENT_HISTORY"),
    ("样本不足", "INSUFFICIENT_SAMPLE"),
    ("信号不足", "INSUFFICIENT_SIGNALS"),
    ("扫描异常", "SCAN_EXCEPTION"),
)


def _normalize_rejection_reason(reason: Any) -> tuple[str, str, Optional[str]]:
    """Return a stable code/label while retaining one raw diagnostic sample."""
    raw = str(reason or "未知").strip()
    label = "扫描异常" if raw.startswith("异常:") else _DYNAMIC_REJECTION_VALUE.sub("", raw)
    label = re.sub(r"\s+", " ", label).strip(" ,，;；") or "未知"
    code = next(
        (value for prefix, value in _REJECTION_REASON_CODES if label.startswith(prefix)),
        None,
    )
    if code is None:
        digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:10].upper()
        code = f"REJECT_{digest}"
    return code, label, raw if raw != label else None


def _summarize_rejection_reasons(
    raw_reasons: Dict[str, int],
) -> tuple[Dict[str, int], Dict[str, Any]]:
    """Aggregate dynamic rejection strings and cap persisted audit cardinality."""
    grouped: Dict[str, Dict[str, Any]] = {}
    for raw_reason, raw_count in raw_reasons.items():
        code, label, sample = _normalize_rejection_reason(raw_reason)
        item = grouped.setdefault(label, {
            "reason_code": code,
            "label": label,
            "count": 0,
            "samples": [],
        })
        item["count"] += int(raw_count or 0)
        if sample and sample not in item["samples"] and len(item["samples"]) < _REJECTION_REASON_SAMPLE_LIMIT:
            item["samples"].append(sample)

    ordered = sorted(grouped.values(), key=lambda item: (-item["count"], item["label"]))
    kept = ordered[:_REJECTION_REASON_LIMIT]
    overflow = ordered[_REJECTION_REASON_LIMIT:]
    if overflow:
        kept.append({
            "reason_code": "OTHER_REJECTIONS",
            "label": "其他失败原因",
            "count": sum(item["count"] for item in overflow),
            "samples": [],
        })

    return (
        {item["label"]: item["count"] for item in kept},
        {
            "total_rejections": sum(int(value or 0) for value in raw_reasons.values()),
            "unique_raw": len(raw_reasons),
            "unique_normalized": len(grouped),
            "reasons": kept,
        },
    )

# 改动(上班族Bark v2)：破位反抽陷阱多维评分。检测信号前 N 天内单日大跌后，
# 通过"量能/反抽强度/MA20破位时长/V型未确认"四维评分区分真陷阱与黄金坑洗盘，
# 避免单一-5%规则误杀强势股洗盘。
BREAKDOWN_LOOKBACK_DAYS = 5          # 检测窗口（天）
BREAKDOWN_DROP_PCT = -5.0            # 触发评分的单日跌幅阈值
TRAP_VETO_SCORE = 70                 # 评分>=此值 → 一票否决并进入BLOCK
TRAP_RISK_SCORE = 40                 # 评分>=此值 → 加风险标注（不否决，排序扣分）
TRAP_VOLUME_RATIO_THRESHOLD = 2.0    # 恐慌抛售量比阈值（>=此值视为真洗盘，量能维度0分）
TRAP_MA20_BREAK_DAYS = 3             # MA20下方停留天数阈值（>=此值加分）

# 改动(上班族Bark)：实盘信号门槛收紧。True 时仅 A 级 + 多重共振(🔥核心热点)判为可交易，
# 默认只允许完整确认的候选进入交易桶（上班族无暇盯盘纠错，宁缺毋滥）。
STRICT_REAL_SIGNAL_GATE = True
REVIVAL_LOOKBACK_DAYS = 10
REVIVAL_SOURCE_STRATEGIES = ("tv_dual", "tv_dual_strict", "squeeze", "tv_zp")
MOMENTUM_ACCEL_LOOKBACK_DAYS = 5
EXECUTION_PLAN_FREEZE_SESSIONS = 3
CONFIRMATION_PRICE_TOLERANCE_PCT = 0.05
MAX_FROZEN_ENTRY_EXTENSION_PCT = 3.0
EARLY_VALUE_MIN_RISE_FROM_20D_LOW = 10.0
EARLY_VALUE_MAX_RISE_FROM_20D_LOW = 20.0
EARLY_VALUE_MAX_5D_RISE = 12.0
EARLY_VALUE_MIN_VOLUME_RATIO = 1.05
EARLY_VALUE_MAX_VOLUME_RATIO = 2.20
BOTTOM_DISCOVERY_LOOKBACK_DAYS = 60
BOTTOM_DISCOVERY_MAX_RISE_FROM_LOW = 12.0
BOTTOM_DISCOVERY_NO_NEW_LOW_TOLERANCE = 0.005
BOTTOM_DISCOVERY_MAX_BASE_VOLUME_RATIO = 0.95
BOTTOM_DISCOVERY_MAX_CURRENT_VOLUME_RATIO = 1.80
BOTTOM_DISCOVERY_MAX_RANGE_CONTRACTION_RATIO = 1.05
BOTTOM_DISCOVERY_MIN_EMA20_SLOPE_5D_PCT = -1.5
from core.sector_strength import (
    build_previous_month_sector_context,
    build_sector_history_context,
    build_sector_leaders,
    build_sector_strength,
    classify_sector_role,
)
from core.money_flow import get_money_flow_rank
from core.industry_prosperity import build_industry_prosperity
from core.decision_layer import apply_decision_layer, apply_growth_segment_context
from routers.market import fetch_mine_sweeper_data
from core.data_source_quality import get_suspected_adjustment_gap_codes
from core.execution_audit import assess_persistent_b_shadow, classify_trade_blockers, load_recent_push_counts
from core.execution_insights import (
    build_distance_to_trade, build_execution_rr, build_frozen_plan_state,
    get_active_execution_plan,
)


EXECUTABLE_PA_ACTIONS = {"READY"}
BLOCKED_PA_SETUPS = {"外包K", "交易区间假突破"}
MIN_RAW_EXECUTION_SCORE = 60.0
MAX_EXECUTION_RISK_PCT = 16.0
# 强信号分级加权：原始策略分(raw_score)≥此值时，视为信号强度极高，
# 在连续质量分中增加确认依据，使强信号更容易通过执行复核。
# （避免历史胜率数据不足的新票/冷门票被拖累到C/D）。
STRONG_SIGNAL_RAW_THRESHOLD = 95.0
HARD_EXECUTION_RISK_PCT = 20.0
MIN_EXECUTION_SECTOR_ALIGNMENT = 70.0
WEAK_SECTOR_ALIGNMENT = 50.0
MIN_EXECUTION_SECTOR_STRENGTH = 70.0
MIN_EXECUTION_STOCK_SECTOR_FIT = 60.0
HIGH_TURNOVER_MKT_CAP_YI = 150.0
HIGH_TURNOVER_MIN_PCT = 1.5
LOW_TURNOVER_MKT_CAP_YI = 300.0
LOW_TURNOVER_MIN_PCT = 1.0
SMALL_CAP_MIN_EXECUTION_YI = 50.0
MID_CAP_MIN_EXECUTION_YI = 50.0
LARGE_CAP_TURNOVER_CONFIRM_YI = 500.0
EARLY_ENTRY_MAX_CONFIRM_GAP_PCT = 0.8
CORE_TRADE_STRATEGIES = {"tv_dual_strict", "tv_dual", "h2"}
DISCOVERY_ONLY_STRATEGIES = set()  # tv_dual 已升格为核心交易策略，发现层白名单为空
H1_STRONG_SECTOR_ALIGNMENT = 85.0
H1_EARLY_SECTOR_ALIGNMENT = 90.0
SWEET_SPOT_OPPORTUNITY_LOW = 70.0
SWEET_SPOT_OPPORTUNITY_HIGH = 80.0
SWEET_SPOT_SECTOR_ALIGNMENT = 70.0
BARK_PROFILE_SECTOR_PHASE_BONUS = {"SECTOR_CLIMAX", "SECTOR_CONFIRM", "SECTOR_EARLY"}
BARK_PROFILE_SETUP_BONUS = {"H1首次入场", "强多头趋势K"}
SECTOR_CORE_ROLES = {"LEADER", "CORE"}
SECTOR_REAR_ROLES = {"FOLLOWER", "LAGGARD"}
CAPITAL_EVENT_KEYWORDS = ("定增", "增发", "非公开发行", "限售股解禁", "解禁", "减持")


def _should_include_sector_watch(strategy_type: str) -> bool:
    return strategy_type == "sector_watch"


def _money_flow_label(item: Dict[str, Any]) -> str:
    amount = float(item.get("main_net_inflow_yi") or 0)
    is_main_metric = item.get("flow_metric") in (None, "main_net_inflow")
    prefix = "主力" if is_main_metric else "资金净"
    if amount > 0:
        return f"{prefix}流入+{amount:.2f}亿"
    if amount < 0:
        return f"{prefix}流出{amount:.2f}亿"
    return "资金中性"


def _blocker_category(blocker: str) -> str:
    value = str(blocker or "")
    if "距冻结确认价" in value:
        return "frozen_plan_extension"
    if "确认价" in value or "等待突破确认" in value:
        return "price_confirmation"
    if "量能未确认" in value or "换手不足" in value:
        return "liquidity_confirmation"
    if "历史信号复活" in value:
        return "signal_revival"
    if "动量加速" in value:
        return "momentum_acceleration"
    if any(text_value in value for text_value in ("涨停/近涨停", "涨幅偏高", "5日涨幅偏高")):
        return "price_acceleration"
    if any(text_value in value for text_value in ("板块强度弱", "弱板块联动", "板块联动<")):
        return "weak_sector"
    if any(text_value in value for text_value in ("H1首次入场", "H2二次入场", "交易计划未确认")):
        return "setup_confirmation"
    return value


def _is_soft_trade_gate_condition(blocker: str) -> bool:
    """trade-gate-v2：判断阻断文本是否属于弱条件（应降级为 trade_cautions）。"""
    return any(marker in blocker for marker in TRADE_GATE_V2_SOFT_CONDITION_MARKERS)


def _dedupe_trade_blockers(blockers: List[str]) -> List[str]:
    preferred = {
        "price_acceleration": ("重大业绩催化但涨停不可成交", "涨停/近涨停", "5日涨幅偏高", "涨幅偏高"),
        "price_confirmation": ("未站上确认价", "未站稳", "等待突破确认"),
        "weak_sector": ("板块强度弱", "弱板块联动", "板块联动<"),
        "setup_confirmation": ("交易计划未确认", "H2二次入场", "H1首次入场"),
    }
    selected: Dict[str, str] = {}
    for blocker in blockers:
        category = _blocker_category(blocker)
        current = selected.get(category)
        if current is None:
            selected[category] = blocker
            continue
        ranking = preferred.get(category, ())
        def rank(value: str) -> int:
            return next((idx for idx, marker in enumerate(ranking) if marker in value), len(ranking))
        if rank(blocker) < rank(current):
            selected[category] = blocker
    return list(selected.values())


def _market_cap_bucket(mkt_cap_yi: float) -> str:
    if mkt_cap_yi <= 0:
        return "UNKNOWN"
    if mkt_cap_yi < 30:
        return "MICRO"
    if mkt_cap_yi < SMALL_CAP_MIN_EXECUTION_YI:
        return "SMALL"
    if mkt_cap_yi < 150:
        return "MID"
    if mkt_cap_yi < LARGE_CAP_TURNOVER_CONFIRM_YI:
        return "LARGE"
    return "MEGA"


def _is_trend_continuation_candidate(res: Dict[str, Any]) -> bool:
    """Identify right-side trend continuation candidates without making them direct buys."""
    mkt_cap_yi = float(res.get("mkt_cap_yi") or 0)
    cap_bucket = _market_cap_bucket(mkt_cap_yi)
    if cap_bucket in {"UNKNOWN", "MICRO", "SMALL"}:
        return False

    trend_phase = str(res.get("pa_trend_phase") or "")
    setup = str(res.get("pa_trade_setup") or res.get("price_action_pattern") or "")
    regime = str(res.get("price_action_regime") or "")
    sector_alignment = float(res.get("sector_alignment_score") or 0)
    sector_phase = str(res.get("sector_phase") or "")
    close_position = float(res.get("pa_close_position") or 0)
    upper_shadow_pct = float(res.get("pa_upper_shadow_pct") or 0)
    volume_ok = bool(res.get("pa_volume_confirmed")) or str(res.get("pa_volume_pattern") or "") in {"放量突破", "量能确认"}
    structure_ok = (
        "二次入场" in trend_phase
        or "H2" in setup
        or str(res.get("pa_h2_quality") or "") in {"强", "中"}
    )
    trend_ok = regime in {"多头趋势", "向上突破"} and sector_phase in {"SECTOR_EARLY", "SECTOR_CONFIRM"}
    close_ok = close_position >= 0.6 and upper_shadow_pct < 3
    return trend_ok and structure_ok and sector_alignment >= 75 and close_ok and (volume_ok or sector_alignment >= 85)


def _is_h1_first_entry(setup: str) -> bool:
    return "H1" in setup or "首次入场" in setup


def _h1_execution_confirmed(res: Dict[str, Any], sector_alignment: float) -> bool:
    return (
        sector_alignment >= H1_STRONG_SECTOR_ALIGNMENT
        and _has_volume_confirmation(res)
        and _has_stable_close_confirmation(res)
        and float(res.get('涨幅%', 0) or 0) < _near_limit_pct(res.get('代码'))
    )


def _trade_quality_confirmed(res: Dict[str, Any], sector_strength: float, stock_sector_fit: float) -> bool:
    return (
        res.get("pa_execution_tier") == "NORMAL"
        and sector_strength >= MIN_EXECUTION_SECTOR_STRENGTH
        and stock_sector_fit >= MIN_EXECUTION_STOCK_SECTOR_FIT
        and _strong_sector_core_candidate(res, sector_strength, stock_sector_fit)
        and not _strong_sector_rear_candidate(res, sector_strength)
        and _as_float(res.get('sector_alignment_score')) >= MIN_EXECUTION_SECTOR_ALIGNMENT
        and _pa_plan_action(res) == "READY"
        and _has_stable_close_confirmation(res)
    )


def _a_minus_trial_qualified(
    res: Dict[str, Any],
    blockers: List[str],
    sector_strength: float,
    stock_sector_fit: float,
) -> bool:
    """Allow fully confirmed names into the controlled A- trial without letter grades."""
    health = res.get("a_minus_trial_health") or {}
    checks = {
        "health_enabled": health.get("enabled") is True,
        "tv_trade_strategy": str(res.get("strategy_type") or "") in CORE_TRADE_STRATEGIES,
        "core_resonance": res.get("共振") == "🔥 核心热点",
        "quality": _as_float(res.get("sop_quality_score")) >= A_MINUS_TRIAL_MIN_QUALITY_SCORE,
        "below_primary_quality": _as_float(res.get("sop_quality_score")) < SOP_A_GRADE_MIN_SCORE,
        "price_action": _as_float(res.get("price_action_score")) >= A_MINUS_TRIAL_MIN_PRICE_ACTION_SCORE,
        "no_sop_veto": not list(res.get("sop_vetoes") or []),
        "ready": _pa_plan_action(res) == "READY",
        "volume": _has_volume_confirmation(res),
        "stable_close": _has_stable_close_confirmation(res),
        "strong_sector_core": _strong_sector_core_candidate(res, sector_strength, stock_sector_fit),
        "sector_alignment": _as_float(res.get("sector_alignment_score")) >= MIN_EXECUTION_SECTOR_ALIGNMENT,
        "risk_reward": (
            _as_float(res.get("pa_risk_reward") or res.get("risk_reward"))
            >= A_MINUS_TRIAL_MIN_RISK_REWARD
        ),
        "not_extended": _as_float(res.get("涨幅%")) < A_MINUS_TRIAL_MAX_DAILY_RISE_PCT,
        "no_execution_blocker": not blockers,
    }
    res["a_minus_trial_checks"] = checks
    return all(checks.values())


_A_EOD_SOFT_BLOCKER_MARKERS = (
    "板块强度不足，降级观察",
    "强板块但个股适配不足，降级观察",
    "强板块后排角色，等待转强为核心股",
    "板块联动<",
    "周线中性，降级观察",
    "周线交易区间，降级观察",
)


def _a_eod_controlled_qualified(
    res: Dict[str, Any],
    blockers: List[str],
    *,
    price_triggered: bool,
    volume_confirmed: bool,
    close_confirmed: bool,
    near_limit: bool,
) -> tuple[bool, List[str], List[str]]:
    """Qualify the calibrated small-position route without weakening hard gates."""
    cautions = [
        blocker for blocker in blockers
        if any(marker in blocker for marker in _A_EOD_SOFT_BLOCKER_MARKERS)
    ]
    hard_blockers = [blocker for blocker in blockers if blocker not in cautions]
    current_price = _candidate_price(res)
    entry_price = _effective_entry_price(res)
    extension_pct = (
        max(0.0, (current_price - entry_price) / entry_price * 100)
        if current_price > 0 and entry_price > 0 else 999.0
    )
    quality_score = _as_float(res.get("sop_quality_score"))
    checks = {
        "tv_trade_strategy": str(res.get("strategy_type") or "") in CORE_TRADE_STRATEGIES,
        "core_resonance": res.get("共振") == "🔥 核心热点",
        "needs_controlled_route": quality_score < SOP_A_GRADE_MIN_SCORE or bool(cautions),
        "quality": _as_float(res.get("sop_quality_score")) >= A_EOD_MIN_QUALITY_SCORE,
        "price_action": res.get("pa_execution_tier") == "NORMAL",
        "not_extended_5d": _as_float(res.get("pct_5d")) <= A_EOD_MAX_5D_GAIN_PCT,
        "risk_reward": _as_float(res.get("pa_risk_reward") or res.get("risk_reward")) >= A_EOD_MIN_RISK_REWARD,
        "no_sop_veto": not list(res.get("sop_vetoes") or []),
        "ready": _pa_plan_action(res) == "READY",
        "price_triggered": price_triggered,
        "volume": volume_confirmed,
        "stable_close": close_confirmed,
        "entry_extension": extension_pct <= A_EOD_MAX_ENTRY_EXTENSION_PCT,
        "not_near_limit": not near_limit,
        "no_hard_blocker": not hard_blockers,
    }
    res["a_eod_trial_checks"] = checks
    res["a_eod_entry_extension_pct"] = round(extension_pct, 2) if extension_pct < 999 else None
    return all(checks.values()), cautions, hard_blockers


def _bark_profile_setup_confirmed(res: Dict[str, Any], setup: str, sector_alignment: float) -> bool:
    if _is_h1_first_entry(setup):
        return _h1_execution_confirmed(res, sector_alignment)
    return str(res.get('price_action_signal') or "") == "强多头趋势K" or setup == "强多头趋势K"


def _build_scan_money_flow_map(limit: int = 6000) -> Dict[str, Dict[str, Any]]:
    try:
        rank = get_money_flow_rank(indicator="今日", limit=limit, force_refresh=False)
        items = rank.get("items") or []
        flow_map = {str(item.get("code", "")).zfill(6): item for item in items if item.get("code")}
        logger.info(f"Loaded money flow rank map: {len(flow_map)} items ({rank.get('status')}, cache={rank.get('cache_hit')})")
        return flow_map
    except Exception as exc:
        logger.warning(f"Money flow rank map unavailable: {exc}")
        return {}


def _load_sector_fund_flow_map() -> Dict[str, float]:
    """行业 → 5日主力净流入（亿元）；接口降级/异常时返回空表（决策层不降权）。"""
    try:
        from core.money_flow import get_sector_money_flow_rank

        payload = get_sector_money_flow_rank(
            indicator="5日", sector_type="行业资金流", limit=100, force_refresh=False
        )
        items = payload.get("items") or []
        flow_map = {
            str(item.get("name") or "").strip(): float(item.get("main_net_inflow_yi") or 0)
            for item in items
            if item.get("name")
        }
        logger.info(
            f"Loaded sector fund flow map: {len(flow_map)} industries "
            f"({payload.get('status')}, cache={payload.get('cache_hit')})"
        )
        return flow_map
    except Exception as exc:
        logger.warning(f"Sector fund flow map unavailable: {exc}")
        return {}


def _apply_money_flow_to_results(results: List[Dict[str, Any]], flow_map: Dict[str, Dict[str, Any]]) -> None:
    if not results:
        return
    if not flow_map:
        for res in results:
            res.setdefault("北向", "---")
            res["money_flow_status"] = "missing"
        return
    for res in results:
        code = str(res.get("代码") or "").zfill(6)
        item = flow_map.get(code)
        if not item:
            res.setdefault("北向", "---")
            res["money_flow_status"] = "missing"
            continue
        res["北向"] = _money_flow_label(item)
        res["money_flow"] = {
            "main_net_inflow_yi": item.get("main_net_inflow_yi"),
            "main_net_ratio": item.get("main_net_ratio"),
            "pct": item.get("pct"),
            "source": item.get("source"),
            "flow_metric": item.get("flow_metric"),
            "metric_label": item.get("metric_label"),
        }
        amount = _as_float(item.get("main_net_inflow_yi"))
        ratio = _as_float(item.get("main_net_ratio"))
        res["money_flow_status"] = "negative" if amount < 0 and ratio < 0 else "ok"


def _pa_plan_action(res: Dict[str, Any]) -> str:
    plan = res.get('pa_trade_plan') or {}
    return str(plan.get('action') or res.get('pa_trade_action') or "").upper()


def _daily_limit_pct(code: str) -> float:
    code = str(code or "")
    if code.startswith(("43", "83", "87", "88", "92")):
        return 30.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    return 10.0


def _near_limit_pct(code: str) -> float:
    return _daily_limit_pct(code) - 0.2


def _daily_limit_tolerance_pct(code: str) -> float:
    return _daily_limit_pct(code) + 0.5


def _is_abnormal_price_move(code: str, pct: Any) -> bool:
    try:
        pct_value = abs(float(pct or 0))
    except (TypeError, ValueError):
        return False
    return pct_value > _daily_limit_tolerance_pct(code)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _apply_liquidity_and_new_stock_filters(
    candidates: pd.DataFrame,
    history: pd.DataFrame,
    *,
    min_avg_amount_yuan: float = UNIVERSE_MIN_AVG_AMOUNT_YUAN,
    lookback_days: int = UNIVERSE_LIQUIDITY_LOOKBACK_DAYS,
) -> tuple[pd.DataFrame, Dict[str, int]]:
    """Apply the five-session liquidity gate and explicit one-price IPO exclusion.

    Historical ``daily_k`` rows do not persist amount, so missing values use the
    project's canonical hand unit: close * volume * 100. A live snapshot amount,
    when present, is kept as the exact value for that session.
    """
    stats = {"insufficient_liquidity": 0, "new_one_price_stock": 0}
    if candidates is None or candidates.empty or history is None or history.empty:
        return candidates.copy(), stats

    work = history.copy()
    work["code"] = work["code"].astype(str).str.zfill(6)
    for column in ("开盘", "最高", "最低", "收盘", "成交量"):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    exact_amount = pd.to_numeric(work.get("amount"), errors="coerce") if "amount" in work.columns else pd.Series(float("nan"), index=work.index)
    estimated_amount = work["收盘"] * work["成交量"] * 100.0
    work["__amount"] = exact_amount.where(exact_amount > 0, estimated_amount)
    work["__amount_estimated"] = ~(exact_amount > 0)
    work = work.sort_values(["code", "日期"])

    metrics: Dict[str, Dict[str, Any]] = {}
    for code, group in work.groupby("code", sort=False):
        recent = group.tail(max(1, int(lookback_days)))
        avg_amount = float(recent["__amount"].mean()) if not recent.empty else 0.0
        listed_days = int(len(group))
        one_price = bool(
            listed_days <= UNIVERSE_NEW_ONE_PRICE_MAX_DAYS
            and ((group["最高"] - group["最低"]).abs() <= 0.001).all()
            and ((group["开盘"] - group["收盘"]).abs() <= 0.001).all()
        )
        metrics[code] = {
            "avg_amount_5d": avg_amount,
            "avg_amount_5d_estimated": bool(recent["__amount_estimated"].any()),
            "new_one_price_stock": one_price,
        }

    result = candidates.copy()
    result["code"] = result["code"].astype(str).str.zfill(6)
    result["avg_amount_5d"] = result["code"].map(lambda code: metrics.get(code, {}).get("avg_amount_5d", 0.0))
    result["avg_amount_5d_estimated"] = result["code"].map(lambda code: metrics.get(code, {}).get("avg_amount_5d_estimated", True))
    new_mask = result["code"].map(lambda code: metrics.get(code, {}).get("new_one_price_stock", False)).astype(bool)
    liquid_mask = result["avg_amount_5d"] > float(min_avg_amount_yuan)
    stats["new_one_price_stock"] = int(new_mask.sum())
    stats["insufficient_liquidity"] = int((~liquid_mask & ~new_mask).sum())
    return result[liquid_mask & ~new_mask].copy(), stats


def _inject_missing_fundamentals(res: Dict[str, Any], fund_map: Dict[str, Dict[str, Any]]) -> None:
    """Fill fundamentals for candidates added after the primary strategy scan."""
    code = str(res.get("代码") or res.get("code") or "").zfill(6)
    fund = fund_map.get(code)
    if not fund:
        return
    if res.get("ROE") is None:
        res["ROE"] = round(float(fund.get("roe") or 0), 2)
    if res.get("净利YOY") is None:
        res["净利YOY"] = round(float(fund.get("net_profit_yoy") or 0), 2)
    res.setdefault("fundamental_data_source", "stock_fundamentals")


def _build_snapshot_audit(snapshot_df: pd.DataFrame, data_mode: str, as_of: Any) -> Dict[str, Any]:
    """Describe which point-in-time filters can actually be enforced for this snapshot."""
    from core.risk_constants import (
        POINT_IN_TIME_CORE_FIELD_MIN_COVERAGE,
        POINT_IN_TIME_FILTER_FIELD_MIN_COVERAGE,
    )

    required = ("price", "pct_chg", "turnover", "mkt_cap")
    total = len(snapshot_df)
    coverage = {
        field: round(float(snapshot_df[field].notna().mean()), 4)
        if total and field in snapshot_df.columns else 0.0
        for field in required
    }
    effective_filters = ["target_market", "exclude_st_delist", "positive_pct_change"]
    degradation_reasons = []
    blocking_reasons = []
    if coverage["turnover"] >= POINT_IN_TIME_FILTER_FIELD_MIN_COVERAGE:
        effective_filters.append("turnover_min")
        if coverage["turnover"] < 1.0:
            degradation_reasons.append("少量股票缺少换手率，已按股票隔离")
    else:
        degradation_reasons.append("换手率字段不完整，未能对全部股票执行换手率过滤")
        blocking_reasons.append("换手率覆盖不足")
    if coverage["mkt_cap"] >= POINT_IN_TIME_FILTER_FIELD_MIN_COVERAGE:
        effective_filters.append("market_cap_min")
        if coverage["mkt_cap"] < 1.0:
            degradation_reasons.append("少量股票缺少市值，已按股票隔离")
    else:
        degradation_reasons.append("市值字段不完整，未能对全部股票执行市值过滤")
        blocking_reasons.append("市值覆盖不足")
    if (
        coverage["price"] < POINT_IN_TIME_CORE_FIELD_MIN_COVERAGE
        or coverage["pct_chg"] < POINT_IN_TIME_CORE_FIELD_MIN_COVERAGE
    ):
        degradation_reasons.append("价格或涨跌幅字段不完整")
        blocking_reasons.append("核心价格字段覆盖不足")
    return {
        "as_of": as_of,
        "data_mode": data_mode,
        "field_coverage": coverage,
        "effective_filters": effective_filters,
        "research_only": bool(blocking_reasons),
        "degradation_reasons": degradation_reasons,
        "blocking_degradation_reasons": blocking_reasons,
    }


def _discovery_pool_mask(snapshot_df: pd.DataFrame, strategy_type: str) -> tuple[pd.Series, str]:
    """Keep discovery recall separate from later trade confirmation."""
    pct = pd.to_numeric(snapshot_df["pct_chg"], errors="coerce")
    if strategy_type == "early_value":
        return pct.between(-3.0, 5.0, inclusive="both"), "EARLY_DISCOVERY"
    if strategy_type == "bottom_discovery":
        return pct.between(-4.0, 4.0, inclusive="both"), "BOTTOM_DISCOVERY"
    if strategy_type == "high_tight_flag":
        return pct.between(-3.0, 5.0, inclusive="both"), "HIGH_TIGHT_FLAG_SHADOW"
    if strategy_type == "limit_up_shakeout":
        return pct.between(-12.0, 5.0, inclusive="both"), "LIMIT_UP_SHAKEOUT_SHADOW"
    if strategy_type == "turtle_breakout":
        return pct.gt(0), "TURTLE_BREAKOUT_SHADOW"
    if strategy_type == "ma_volume":
        return pct.between(-20.0, 20.0, inclusive="both"), "MA_VOLUME_SHADOW"
    if strategy_type == "uptrend_limit_down":
        return pct.le(-7.0), "UPTREND_LIMIT_DOWN_SHADOW"
    if strategy_type == "rps_breakout":
        return pct.between(-20.0, 20.0, inclusive="both"), "RPS_BREAKOUT_SHADOW"
    if strategy_type == "trader_vic_2b":
        return pct.between(-12.0, 12.0, inclusive="both"), "TRADER_VIC_2B_SHADOW"
    if strategy_type == "sector_watch":
        return pct.between(-2.0, 8.0, inclusive="both"), "PULLBACK_DISCOVERY"
    return pct > 0, "MOMENTUM_DISCOVERY"


def _apply_research_only_gate(results: List[Dict[str, Any]], audit: Dict[str, Any]) -> None:
    """Prevent incomplete point-in-time inputs from becoming executable recommendations."""
    if not audit.get("research_only"):
        return
    reason = "关键点时字段不完整，本次扫描仅供研究，禁止交易执行"
    for row in results:
        row["trade_eligible"] = False
        row["trade_bucket"] = "OBSERVE"
        row["research_only"] = True
        blockers = list(row.get("trade_blockers") or [])
        row["trade_blockers"] = list(dict.fromkeys([*blockers, reason]))
    audit["status"] = "RESEARCH_ONLY"


def _candidate_price(res: Dict[str, Any]) -> float:
    return _as_float(res.get('现价') or res.get('price') or res.get('收盘'))


def _effective_entry_price(res: Dict[str, Any]) -> float:
    return _as_float(get_active_execution_plan(res)["entry"])


def _confirmation_price_reached(current_price: float, entry_price: float) -> bool:
    if current_price <= 0 or entry_price <= 0:
        return False
    return current_price >= entry_price * (1 - CONFIRMATION_PRICE_TOLERANCE_PCT / 100)


def _load_market_snapshot(force_refresh: bool = False) -> pd.DataFrame:
    try:
        return get_market_snapshot(force_refresh=force_refresh)
    except TypeError:
        return get_market_snapshot()


def _has_volume_confirmation(res: Dict[str, Any]) -> bool:
    if res.get('pa_close_time_eligible') is False:
        return False
    return bool(res.get('pa_volume_confirmed')) or res.get('pa_volume_pattern') in {'放量突破', '缩量回调后放量反包'}


def _apply_close_confirmation_timing(
    res: Dict[str, Any],
    as_of: Any,
    data_mode: str,
    now: Any = None,
) -> None:
    """Classify whether the observed bar is late enough for execution confirmation."""
    as_of_text = str(as_of or "")
    try:
        observed = pd.Timestamp(as_of)
        if pd.isna(observed):
            raise ValueError("invalid as_of")
        current = pd.Timestamp(now or datetime.now())
        date_only = len(as_of_text) == 10 and as_of_text.count("-") == 2
        if date_only and observed.date() < current.date():
            phase = "AFTER_CLOSE"
            eligible = True
        else:
            clock = current if data_mode == "LOCAL_DB" and date_only else observed
            minute_of_day = clock.hour * 60 + clock.minute
            if minute_of_day >= 15 * 60:
                phase = "AFTER_CLOSE"
                eligible = True
            elif minute_of_day >= 14 * 60 + 30:
                phase = "LATE_SESSION"
                eligible = True
            else:
                phase = "INTRADAY_PROVISIONAL"
                eligible = False
    except (TypeError, ValueError):
        phase = "LEGACY_UNKNOWN"
        eligible = True
    res["pa_close_confirmation_as_of"] = as_of_text[:19] or None
    res["pa_close_confirmation_phase"] = phase
    res["pa_close_time_eligible"] = eligible
    raw_volume_confirmed = bool(res.get('pa_volume_confirmed')) or res.get('pa_volume_pattern') in {
        '放量突破', '缩量回调后放量反包'
    }
    res["pa_volume_confirmation_state"] = (
        "CONFIRMED" if raw_volume_confirmed and eligible
        else "PROVISIONAL" if raw_volume_confirmed
        else "NOT_CONFIRMED"
    )
    res["pa_volume_confirmation_final"] = raw_volume_confirmed and eligible


_OBSERVATION_ONLY_PLAN_FLAGS = (
    "revival_watch_only",
    "momentum_acceleration_watch_only",
    "bottom_discovery_watch_only",
    "tv_reversal_watch_only",
    "sector_watch_only",
    "early_value_watch_only",
    "high_tight_flag_watch_only",
    "turtle_breakout_watch_only",
    "limit_up_shakeout_watch_only",
    "ma_volume_watch_only",
    "uptrend_limit_down_watch_only",
    "rps_breakout_watch_only",
    "trader_vic_2b_watch_only",
)


def _is_observation_only_plan(res: Dict[str, Any]) -> bool:
    return any(bool(res.get(flag)) for flag in _OBSERVATION_ONLY_PLAN_FLAGS)


def _apply_price_action_execution_stage(
    res: Dict[str, Any],
    *,
    executable: Optional[bool] = None,
) -> str:
    """Expose one unambiguous PA lifecycle state for UI and Bark."""
    action = _pa_plan_action(res)
    confirmation = str(res.get("pa_confirmation_state") or "")
    frozen = bool(res.get("execution_plan_frozen"))

    if _is_observation_only_plan(res):
        stage = "OBSERVATION_ONLY"
    elif action == "AVOID" or str(res.get("pa_pullback_status") or "") == "INVALIDATED":
        stage = "BLOCKED"
    elif res.get("pa_close_time_eligible") is False:
        stage = "INTRADAY_PREVIEW"
    elif executable and frozen and confirmation == "ENTRY_CONFIRMED":
        stage = "NEXT_SESSION_EXECUTABLE"
    elif frozen:
        stage = "NEXT_SESSION_REVIEW"
    elif confirmation == "ENTRY_CONFIRMED":
        stage = "EOD_CONFIRMED"
    elif action == "READY":
        stage = "SETUP_READY"
    else:
        stage = "WAITING_SETUP"

    labels = {
        "OBSERVATION_ONLY": "观察策略｜需生成新计划",
        "BLOCKED": "结构失效｜禁止买入",
        "INTRADAY_PREVIEW": "盘中预观察｜14:30后确认",
        "SETUP_READY": "结构就绪｜等待价量确认",
        "EOD_CONFIRMED": "尾盘已确认｜次一交易日复核",
        "NEXT_SESSION_REVIEW": "次日复核｜尚不可执行",
        "NEXT_SESSION_EXECUTABLE": "次日确认｜可执行候选",
        "WAITING_SETUP": "等待新结构",
    }
    res["pa_execution_stage"] = stage
    res["pa_execution_stage_label"] = labels[stage]
    res["pa_signal_date"] = res.get("frozen_plan_date") or res.get("data_date") or res.get("date")
    return stage


def _has_stable_close_confirmation(res: Dict[str, Any]) -> bool:
    if res.get('pa_close_time_eligible') is False:
        return False
    close_position = _as_float(res.get('pa_close_position'), -1.0)
    upper_shadow_pct = _as_float(res.get('pa_upper_shadow_pct'), -1.0)
    close_ok = close_position < 0 or close_position >= 0.6
    shadow_ok = upper_shadow_pct < 0 or upper_shadow_pct < 3
    return close_ok and shadow_ok


def _is_h2_second_entry(setup: str, res: Dict[str, Any]) -> bool:
    h2_state = str(res.get('pa_h2_state') or "")
    if h2_state:
        return h2_state == "H2_TRIGGERED"
    trend_phase = str(res.get('pa_trend_phase') or "")
    return "H2" in setup or "二次入场" in setup or "二次入场" in trend_phase


def _has_pullback_reversal_volume(res: Dict[str, Any]) -> bool:
    return str(res.get('pa_volume_pattern') or "") == "缩量回调后放量反包"


def _sector_strength_score(res: Dict[str, Any]) -> float:
    existing = res.get('sector_strength_score')
    if existing is not None:
        return _as_float(existing)
    momentum = _as_float(res.get('sector_momentum_score'))
    breadth = _as_float(res.get('sector_breadth'))
    phase_bonus = {
        "SECTOR_CLIMAX": 10,
        "SECTOR_CONFIRM": 8,
        "SECTOR_EARLY": 6,
        "SECTOR_WARMUP": 3,
        "SECTOR_FADE": -10,
    }.get(str(res.get('sector_phase') or ""), 0)
    return round(max(0, min(100, momentum * 0.65 + breadth * 0.25 + phase_bonus)), 1)


def _stock_sector_fit_score(res: Dict[str, Any]) -> float:
    existing = res.get('stock_sector_fit_score')
    if existing is not None:
        return _as_float(existing)
    relative_pct = _as_float(res.get('sector_relative_pct'))
    stock_pct = _as_float(res.get('涨幅%'))
    rank = int(_as_float(res.get('stock_rank_in_sector')))
    role = str(res.get('sector_role') or "")
    role_bonus = {"LEADER": 18, "CORE": 12, "FOLLOWER": 4, "LAGGARD": -8}.get(role, 0)
    rank_bonus = 10 if 0 < rank <= 3 else 6 if 0 < rank <= 8 else 0
    relative_score = max(0, min(45, 25 + relative_pct * 5))
    activity_score = min(20, max(0, stock_pct) * 2)
    return round(max(0, min(100, relative_score + activity_score + rank_bonus + role_bonus)), 1)


def _combined_sector_alignment(sector_strength: float, stock_fit: float) -> float:
    return round(max(0, min(100, sector_strength * 0.6 + stock_fit * 0.4)), 1)


def _sector_role_value(res: Dict[str, Any]) -> str:
    return str(res.get('sector_role') or res.get('sector_position') or "").upper()


def _strong_sector_core_candidate(res: Dict[str, Any], sector_strength: float, stock_fit: float) -> bool:
    role = _sector_role_value(res)
    if sector_strength < 75 or stock_fit < MIN_EXECUTION_STOCK_SECTOR_FIT:
        return False
    if role:
        return role in SECTOR_CORE_ROLES
    return stock_fit >= 70


def _strong_sector_rear_candidate(res: Dict[str, Any], sector_strength: float) -> bool:
    role = _sector_role_value(res)
    return sector_strength >= MIN_EXECUTION_SECTOR_STRENGTH and role in SECTOR_REAR_ROLES


def _is_sweet_spot_trade_model(res: Dict[str, Any], sector_alignment: float) -> bool:
    opportunity = _as_float(res.get('trade_opportunity_score'))
    setup = str(res.get('pa_trade_setup') or "")
    return (
        str(res.get('strategy_type') or "") in CORE_TRADE_STRATEGIES
        and SWEET_SPOT_OPPORTUNITY_LOW <= opportunity < SWEET_SPOT_OPPORTUNITY_HIGH
        and sector_alignment >= SWEET_SPOT_SECTOR_ALIGNMENT
        and _pa_plan_action(res) == "READY"
        and (_is_h2_second_entry(setup, res) or _has_pullback_reversal_volume(res))
    )


def _observe_promotion_candidate(res: Dict[str, Any], sector_alignment: float) -> bool:
    current_price = _candidate_price(res)
    entry_price = _effective_entry_price(res)
    if current_price <= 0 or entry_price <= 0:
        return False
    confirm_gap_pct = (entry_price - current_price) / entry_price * 100
    opportunity = _as_float(res.get('trade_opportunity_score'))
    setup = str(res.get('pa_trade_setup') or "")
    return (
        str(res.get('strategy_type') or "") in CORE_TRADE_STRATEGIES
        and _pa_plan_action(res) == "READY"
        and _sector_strength_score(res) >= 60
        and sector_alignment >= MIN_EXECUTION_SECTOR_ALIGNMENT
        and opportunity >= 60
        and -0.3 <= confirm_gap_pct <= 1.2
        and _has_stable_close_confirmation(res)
        and (_has_volume_confirmation(res) or _is_h2_second_entry(setup, res) or str(res.get('sector_phase') or "") in BARK_PROFILE_SECTOR_PHASE_BONUS)
    )


def _h2_execution_confirmed(res: Dict[str, Any], sector_strength: float) -> bool:
    return (
        sector_strength >= MIN_EXECUTION_SECTOR_ALIGNMENT
        and (_has_volume_confirmation(res) or _has_pullback_reversal_volume(res))
        and _has_stable_close_confirmation(res)
        and _as_float(res.get('pa_risk_pct')) <= MAX_EXECUTION_RISK_PCT
        and str(res.get('pa_h2_quality') or "") in {"强", "中"}
    )


def _trade_setup_quality(res: Dict[str, Any], setup: str, sector_alignment: float, sector_strength: float) -> str:
    if _is_h1_first_entry(setup):
        return "H1_TRADABLE" if _h1_execution_confirmed(res, sector_alignment) else "H1_RAW"
    if _is_h2_second_entry(setup, res):
        return "H2_TRADABLE" if _h2_execution_confirmed(res, sector_strength) else "H2_RAW"
    return "OTHER"


def _trade_setup_quality_label(quality: str) -> str:
    return {
        "H1_TRADABLE": "H1可交易形态",
        "H1_RAW": "H1原始形态",
        "H2_TRADABLE": "H2可交易形态",
        "H2_RAW": "H2原始形态",
    }.get(quality, "其他形态")


def _revival_action_label(level: str) -> str:
    if level == "MOMENTUM_ACCELERATION":
        return "历史信号复活：动量加速，次日小仓复核"
    if level == "FOLLOW_SMALL":
        return "历史信号复活：可小仓复核"
    if level == "NEXT_DAY_CONFIRM":
        return "历史信号复活：等次日确认"
    return "历史信号复活：禁止追涨"


def _fetch_recent_signal_map(
    engine,
    codes: List[str],
    data_date: str,
    lookback_days: int = REVIVAL_LOOKBACK_DAYS,
) -> Dict[str, Dict[str, Any]]:
    if not codes:
        return {}
    code_params = {f"code_{idx}": str(code).zfill(6) for idx, code in enumerate(codes)}
    placeholders = ", ".join(f":code_{idx}" for idx in range(len(code_params)))
    strategy_params = {f"strategy_{idx}": strategy for idx, strategy in enumerate(REVIVAL_SOURCE_STRATEGIES)}
    strategy_placeholders = ", ".join(f":strategy_{idx}" for idx in range(len(strategy_params)))
    params = {
        **code_params,
        **strategy_params,
        "data_date": str(data_date)[:10],
        "lookback_days": int(lookback_days),
    }
    query = text(f"""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY code
                       ORDER BY COALESCE(data_date, date) DESC, scanned_at DESC NULLS LAST
                   ) AS rn
            FROM scan_history
            WHERE code IN ({placeholders})
              AND COALESCE(data_date, date) < CAST(:data_date AS date)
              AND COALESCE(data_date, date) >= CAST(:data_date AS date) - (CAST(:lookback_days AS integer) * interval '1 day')
              AND COALESCE(strategy_type, 'squeeze') IN ({strategy_placeholders})
        )
        SELECT code, name, COALESCE(data_date, date) AS signal_date, scanned_at, strategy_type,
               price, pct, score, industry, resonance, price_action_score, price_action_summary,
               pa_entry_price, pa_stop_price, pa_target_price, pa_risk_reward, pa_trade_action, pa_trade_setup
        FROM ranked
        WHERE rn = 1
    """)
    try:
        with engine.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return {str(row._mapping["code"]).zfill(6): dict(row._mapping) for row in rows}
    except Exception as exc:
        logger.warning(f"Historical revival lookup skipped: {exc}")
        return {}


def _fetch_active_execution_plan_map(
    engine,
    codes: List[str],
    data_date: str,
    freeze_sessions: int = EXECUTION_PLAN_FREEZE_SESSIONS,
) -> Dict[str, Dict[str, Any]]:
    """Load the earliest still-valid READY plan so the trigger does not move daily."""
    if not codes:
        return {}
    code_params = {f"plan_code_{idx}": str(code).zfill(6) for idx, code in enumerate(codes)}
    placeholders = ", ".join(f":plan_code_{idx}" for idx in range(len(code_params)))
    params = {
        **code_params,
        "data_date": str(data_date)[:10],
        "min_plan_date": shift_a_share_trading_date(
            str(data_date)[:10], -max(1, int(freeze_sessions))
        ),
    }
    query = text(f"""
        WITH active AS (
            SELECT s.code, COALESCE(s.data_date, s.date) AS plan_date,
                   s.pa_entry_price, s.pa_stop_price, s.pa_target_price,
                   s.price_action_detail,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.code
                       ORDER BY COALESCE(s.data_date, s.date) ASC, s.scanned_at ASC NULLS LAST
                   ) AS rn
            FROM scan_history s
            WHERE s.code IN ({placeholders})
              AND COALESCE(s.data_date, s.date) < CAST(:data_date AS date)
              AND COALESCE(s.data_date, s.date) >= CAST(:min_plan_date AS date)
              AND s.pa_trade_action = 'READY'
              AND s.pa_entry_price > 0
              AND s.pa_stop_price > 0
              AND s.pa_stop_price < s.pa_entry_price
              AND NOT EXISTS (
                  SELECT 1 FROM daily_k d
                  WHERE d.code = s.code
                    AND d.date > COALESCE(s.data_date, s.date)
                    AND d.date < CAST(:data_date AS date)
                    AND d.low <= s.pa_stop_price
              )
        )
        SELECT code, plan_date, pa_entry_price, pa_stop_price, pa_target_price,
               price_action_detail
        FROM active WHERE rn = 1
    """)
    try:
        with engine.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        plans = {}
        for row in rows:
            plan = dict(row._mapping)
            plan["plan_expiry_date"] = shift_a_share_trading_date(
                str(plan.get("plan_date"))[:10], max(1, int(freeze_sessions))
            )
            plans[str(plan["code"]).zfill(6)] = plan
        return plans
    except Exception as exc:
        logger.warning(f"Frozen execution plan lookup skipped: {exc}")
        return {}


def _apply_frozen_execution_plan(res: Dict[str, Any], plan: Dict[str, Any]) -> None:
    if not plan or _is_observation_only_plan(res):
        return
    current = _candidate_price(res)
    entry = _as_float(plan.get("pa_entry_price"))
    stop = _as_float(plan.get("pa_stop_price"))
    if entry <= 0 or stop <= 0 or stop >= entry or (current > 0 and current <= stop):
        return
    res["generated_confirmation_price"] = res.get("pa_entry_price")
    res["generated_stop_price"] = res.get("pa_stop_price")
    res["frozen_plan_date"] = str(plan.get("plan_date") or "")[:10]
    res["frozen_plan_expiry_date"] = str(plan.get("plan_expiry_date") or "")[:10] or None
    res["frozen_plan_valid_sessions"] = EXECUTION_PLAN_FREEZE_SESSIONS
    res["frozen_confirmation_price"] = round(entry, 2)
    res["frozen_stop_price"] = round(stop, 2)
    detail = plan.get("price_action_detail") or {}
    if not isinstance(detail, dict):
        detail = {}
    close_guard = _as_float(detail.get("pa_close_guard_price") or stop)
    res["frozen_close_guard_price"] = round(close_guard, 2)
    target = _as_float(plan.get("pa_target_price"))
    res["frozen_target_price"] = round(target, 2) if target > 0 else None
    extension_pct = (current - entry) / entry * 100 if current > 0 else 0.0
    res["frozen_entry_extension_pct"] = round(extension_pct, 2)
    res["frozen_confirmation_triggered"] = _confirmation_price_reached(current, entry)
    res["execution_plan_frozen"] = True
    res["active_execution_plan_source"] = "FROZEN"
    res["active_confirmation_price"] = round(entry, 2)
    res["active_stop_price"] = round(stop, 2)
    res["active_close_guard_price"] = round(close_guard, 2)
    res["active_target_price"] = round(target, 2) if target > 0 else None


def _classify_historical_revival(res: Dict[str, Any], history: Dict[str, Any]) -> Dict[str, Any]:
    current = _candidate_price(res)
    pct = _as_float(res.get('涨幅%'))
    pa_entry = _effective_entry_price(res)
    pa_stop = _as_float(res.get('pa_stop_price') or res.get('stop_price'))
    old_entry = _as_float(history.get('pa_entry_price') or history.get('price'))
    old_stop = _as_float(history.get('pa_stop_price'))
    old_price = _as_float(history.get('price'))
    volume_ok = _has_volume_confirmation(res)
    close_ok = _has_stable_close_confirmation(res)
    sector_alignment = _as_float(res.get('sector_alignment_score'), 50)
    sector_ok = sector_alignment >= MIN_EXECUTION_SECTOR_ALIGNMENT
    not_broken = current > 0 and current >= max(old_stop, pa_stop, 0)
    confirm_price = max(pa_entry, old_entry, 0)
    price_ok = _confirmation_price_reached(current, confirm_price)
    near_price_ok = current > 0 and confirm_price > 0 and current >= confirm_price * 0.997
    strong_day = pct >= 7 or res.get('limit_up_status') in {"SEALED", "BROKEN"}
    momentum_accel = (
        not_broken
        and strong_day
        and near_price_ok
        and volume_ok
        and close_ok
        and sector_alignment >= 85
        and _as_float(res.get('price_action_score')) >= 70
        and _as_float(res.get('pa_trap_risk')) < 60
        and pct >= 9
    )

    blockers = []
    if not not_broken:
        blockers.append("跌破历史信号失效位")
    if not strong_day:
        blockers.append("当日强度不足")
    if not price_ok and not momentum_accel:
        blockers.append("未站稳历史/今日确认价")
    if not volume_ok:
        blockers.append("量能未确认")
    if not close_ok:
        blockers.append("冲高回落风险")
    if not sector_ok:
        blockers.append("板块联动不足")
    if _as_float(res.get('pa_trap_risk')) >= 70:
        blockers.append("多头陷阱风险偏高")
    if res.get('pa_position_strategy') == '区间上沿不追价':
        blockers.append("交易区间上沿不追价")

    if momentum_accel:
        level = "MOMENTUM_ACCELERATION"
    elif not_broken and strong_day and price_ok and volume_ok and close_ok and sector_ok and not blockers:
        level = "FOLLOW_SMALL"
    elif not_broken and strong_day:
        level = "NEXT_DAY_CONFIRM"
    else:
        level = "AVOID_CHASE"

    return {
        "revival_level": level,
        "revival_action": _revival_action_label(level),
        "revival_blockers": blockers,
        "revival_source_date": str(history.get("signal_date") or "")[:10],
        "revival_source_strategy": history.get("strategy_type"),
        "revival_source_price": round(old_price, 2) if old_price else None,
        "revival_source_entry": round(old_entry, 2) if old_entry else None,
        "revival_source_stop": round(old_stop, 2) if old_stop else None,
        "revival_reason": "历史入选后未破位，今日出现强势再启动" if level != "AVOID_CHASE" else "历史信号已失效或强度不足",
    }


def _build_historical_revival_candidates(
    candidates: pd.DataFrame,
    existing_codes: set,
    hist_map: Dict[str, pd.DataFrame],
    signal_map: Dict[str, Dict[str, Any]],
    strategy_type: str,
) -> List[Dict[str, Any]]:
    if candidates is None or candidates.empty or not signal_map:
        return []

    revival: List[Dict[str, Any]] = []
    for _, row in candidates.iterrows():
        code = str(row.get('code', '')).zfill(6)
        if not code or code in existing_codes:
            continue
        history = signal_map.get(code)
        df_hist = hist_map.get(code)
        if not history or df_hist is None or df_hist.empty:
            continue

        price = _as_float(row.get('price'))
        pct = _as_float(row.get('pct_chg'))
        old_stop = _as_float(history.get('pa_stop_price'))
        old_entry = _as_float(history.get('pa_entry_price') or history.get('price'))
        if price <= 0 or price < max(old_stop, 0):
            continue
        if pct < 7 and (old_entry <= 0 or price < old_entry):
            continue

        base_score = _as_float(history.get('score'), 55)
        revival_score = round(max(base_score, 55) + min(max(pct, 0), 12) * 1.2, 1)
        revival.append({
            "代码": code,
            "名称": row.get('name') or history.get('name') or code,
            "现价": round(price, 2),
            "涨幅%": round(pct, 2),
            "Score": revival_score,
            "raw_score": revival_score,
            "strategy_type": strategy_type,
            "signal": "历史信号复活",
            "reason": f"近{REVIVAL_LOOKBACK_DAYS}日历史入选后再启动",
            "revival_watch_only": True,
            "revival_history": history,
            "历史信号日期": str(history.get("signal_date") or "")[:10],
            "历史信号策略": history.get("strategy_type"),
            "历史入选价": round(_as_float(history.get("price")), 2) if history.get("price") is not None else None,
        })
    return revival


def _build_momentum_acceleration_candidates(
    candidates: pd.DataFrame,
    existing_codes: set,
    hist_map: Dict[str, pd.DataFrame],
    sector_map: Dict[str, str],
    sector_strength: Dict[str, Dict[str, Any]],
    strategy_type: str,
) -> List[Dict[str, Any]]:
    """Find strong limit-up/large-candle acceleration names missed by base TV signals."""
    if candidates is None or candidates.empty:
        return []

    watch: List[Dict[str, Any]] = []
    for _, row in candidates.iterrows():
        code = str(row.get('code', '')).zfill(6)
        if not code or code in existing_codes:
            continue
        df_hist = hist_map.get(code)
        if df_hist is None or df_hist.empty or len(df_hist) < 30:
            continue

        price = _as_float(row.get('price'))
        pct = _as_float(row.get('pct_chg'))
        turnover = _as_float(row.get('turnover'))
        if price <= 0 or pct < 7:
            continue

        work = df_hist.copy().reset_index(drop=True)
        close = pd.to_numeric(work.get('收盘'), errors='coerce')
        high = pd.to_numeric(work.get('最高'), errors='coerce')
        low = pd.to_numeric(work.get('最低'), errors='coerce')
        open_ = pd.to_numeric(work.get('开盘'), errors='coerce')
        vol = pd.to_numeric(work.get('成交量'), errors='coerce')
        if close.isna().tail(6).any() or high.isna().tail(6).any():
            continue

        recent_close = close.tail(MOMENTUM_ACCEL_LOOKBACK_DAYS + 1)
        pct_5d = (float(recent_close.iloc[-1]) / float(recent_close.iloc[0]) - 1) * 100 if float(recent_close.iloc[0]) > 0 else 0
        daily_pct = close.pct_change().tail(MOMENTUM_ACCEL_LOOKBACK_DAYS) * 100
        strong_days = int((daily_pct >= 7).sum())
        limit_like_days = int((daily_pct >= _near_limit_pct(code) - 0.2).sum())
        prior_high_60 = float(high.iloc[:-1].tail(60).max()) if len(high) > 1 else 0
        near_new_high = prior_high_60 > 0 and price >= prior_high_60 * 0.98
        day_range = max(_as_float(high.iloc[-1]) - _as_float(low.iloc[-1]), 0.01)
        close_position = (price - _as_float(low.iloc[-1])) / day_range
        upper_shadow_pct = max(0.0, _as_float(high.iloc[-1]) - max(_as_float(open_.iloc[-1]), price)) / max(price, 0.01) * 100
        vol_ma20 = float(vol.iloc[:-1].tail(20).mean()) if len(vol) > 20 else 0
        volume_ratio = float(vol.iloc[-1]) / vol_ma20 if vol_ma20 > 0 else 0

        sector = sector_map.get(code, row.get('industry') or '未知')
        strength = sector_strength.get(sector, {})
        sector_alignment = _as_float(strength.get('sector_momentum_score'), 50) * 0.55 + _as_float(strength.get('sector_breadth'), 50) * 0.25
        strong_momentum = strong_days >= 2 or limit_like_days >= 1
        execution_shape = close_position >= 0.65 and upper_shadow_pct < 4
        liquidity_ok = turnover <= 0 or turnover >= 3
        if not (strong_momentum and near_new_high and execution_shape and liquidity_ok):
            continue

        score = round(55 + min(max(pct, 0), 12) * 1.5 + min(max(pct_5d, 0), 30) * 0.45 + min(volume_ratio, 3) * 4, 1)
        watch.append({
            "代码": code,
            "名称": row.get('name') or code,
            "现价": round(price, 2),
            "涨幅%": round(pct, 2),
            "Score": score,
            "raw_score": score,
            "strategy_type": strategy_type,
            "signal": "强趋势涨停加速",
            "reason": "强趋势涨停/大阳加速，进入高动量观察池",
            "momentum_acceleration_watch_only": True,
            "momentum_acceleration_action": "强趋势涨停加速：次日确认后小仓复核",
            "momentum_acceleration_reason": f"{MOMENTUM_ACCEL_LOOKBACK_DAYS}日涨幅{pct_5d:.1f}%，强势日{strong_days}天，接近/突破阶段新高",
            "momentum_acceleration_metrics": {
                "pct_5d": round(pct_5d, 2),
                "strong_days": strong_days,
                "limit_like_days": limit_like_days,
                "volume_ratio": round(volume_ratio, 2),
                "close_position": round(close_position, 2),
                "upper_shadow_pct": round(upper_shadow_pct, 2),
                "near_new_high": near_new_high,
                "sector_alignment_proxy": round(sector_alignment, 1),
            },
        })
    return watch


def _check_early_value_strategy(df: pd.DataFrame, code: str, name: str, price: float) -> tuple[bool, Dict[str, Any]]:
    """Early right-side tracking: not a buy strategy, only a cost-basis watchlist."""
    if df is None or df.empty or len(df) < 60:
        return False, {"reason": "早期性价比样本不足"}

    work = df.copy().reset_index(drop=True)
    close = pd.to_numeric(work.get('收盘'), errors='coerce')
    high = pd.to_numeric(work.get('最高'), errors='coerce')
    low = pd.to_numeric(work.get('最低'), errors='coerce')
    open_ = pd.to_numeric(work.get('开盘'), errors='coerce')
    vol = pd.to_numeric(work.get('成交量'), errors='coerce')
    if close.tail(25).isna().any() or low.tail(25).isna().any() or vol.tail(25).isna().any():
        return False, {"reason": "早期性价比数据不完整"}

    current = _as_float(price) or float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    if current <= 0 or prev_close <= 0:
        return False, {"reason": "早期性价比价格无效"}

    prior_low20 = float(low.iloc[:-1].tail(20).min())
    rise_from_low20 = (current - prior_low20) / prior_low20 * 100 if prior_low20 > 0 else 0
    if not (EARLY_VALUE_MIN_RISE_FROM_20D_LOW <= rise_from_low20 <= EARLY_VALUE_MAX_RISE_FROM_20D_LOW):
        return False, {"reason": f"距20日低点涨幅不在10%-20%区间({rise_from_low20:.1f}%)"}

    pct_1d = (current - prev_close) / prev_close * 100
    close_5d_ago = float(close.iloc[-6]) if len(close) >= 6 else prev_close
    pct_5d = (current - close_5d_ago) / close_5d_ago * 100 if close_5d_ago > 0 else 0
    if pct_1d >= 7 or pct_5d > EARLY_VALUE_MAX_5D_RISE or pct_1d >= _near_limit_pct(code) - 1:
        return False, {"reason": "涨幅已加速，等待回踩而非早期追踪"}

    ema20 = pd.to_numeric(work.get('EMA20'), errors='coerce') if 'EMA20' in work.columns else close.ewm(span=20, adjust=False).mean()
    ema60 = pd.to_numeric(work.get('EMA60'), errors='coerce') if 'EMA60' in work.columns else close.ewm(span=60, adjust=False).mean()
    ema20_now = float(ema20.iloc[-1])
    ema60_now = float(ema60.iloc[-1])
    recent_low3 = float(low.tail(3).min())
    pullback_not_broken = current >= ema20_now and recent_low3 >= ema20_now * 0.97 and ema20_now >= ema60_now * 0.98
    if not pullback_not_broken:
        return False, {"reason": "回踩未确认守住20日线/中期趋势"}

    vol_ma20 = float(vol.iloc[:-1].tail(20).mean())
    volume_ratio = float(vol.iloc[-1]) / vol_ma20 if vol_ma20 > 0 else 0
    vol_3_ratio = float(vol.tail(3).mean()) / vol_ma20 if vol_ma20 > 0 else 0
    volume_starting = (
        EARLY_VALUE_MIN_VOLUME_RATIO <= volume_ratio <= EARLY_VALUE_MAX_VOLUME_RATIO
        or (volume_ratio >= 0.9 and vol_3_ratio >= 1.0)
    )
    if not volume_starting:
        return False, {"reason": f"量能尚未开始确认({volume_ratio:.2f}x)"}

    day_range = max(float(high.iloc[-1]) - float(low.iloc[-1]), 0.01)
    close_position = (current - float(low.iloc[-1])) / day_range
    upper_shadow_pct = max(0.0, float(high.iloc[-1]) - max(float(open_.iloc[-1]), current)) / max(current, 0.01) * 100
    if close_position < 0.45 or upper_shadow_pct >= 4:
        return False, {"reason": "收盘承接不足或上影线偏长"}

    score = round(
        55
        + max(0, 10 - abs(rise_from_low20 - 15)) * 1.2
        + min(volume_ratio, 1.8) * 5
        + min(close_position, 1.0) * 8,
        1,
    )
    return True, {
        "代码": code,
        "名称": name,
        "现价": round(current, 2),
        "涨幅%": round(pct_1d, 2),
        "Score": score,
        "raw_score": score,
        "strategy_type": "early_value",
        "signal": "早期性价比追踪",
        "reason": "距20日低点10%-20%，回踩不破且量能开始确认",
        "early_value_watch_only": True,
        "early_value_metrics": {
            "rise_from_20d_low": round(rise_from_low20, 2),
            "pct_5d": round(pct_5d, 2),
            "volume_ratio": round(volume_ratio, 2),
            "vol_3_ratio": round(vol_3_ratio, 2),
            "ema20": round(ema20_now, 2),
            "ema60": round(ema60_now, 2),
            "recent_low3": round(recent_low3, 2),
            "close_position": round(close_position, 2),
            "upper_shadow_pct": round(upper_shadow_pct, 2),
        },
    }


def _check_bottom_discovery_strategy(
    df: pd.DataFrame,
    code: str,
    name: str,
    price: float,
    current_vol: float = 0,
    current_open: float = 0,
) -> tuple[bool, Dict[str, Any]]:
    """Point-in-time B0/B1 bottom discovery; this strategy never grants trade permission."""
    if df is None or df.empty or len(df) < 80:
        return False, {"reason": "底部发现样本不足"}

    work = df.copy().reset_index(drop=True)
    close = pd.to_numeric(work.get("收盘"), errors="coerce")
    high = pd.to_numeric(work.get("最高"), errors="coerce")
    low = pd.to_numeric(work.get("最低"), errors="coerce")
    open_ = pd.to_numeric(work.get("开盘"), errors="coerce")
    volume = pd.to_numeric(work.get("成交量"), errors="coerce")
    if any(series.tail(BOTTOM_DISCOVERY_LOOKBACK_DAYS).isna().any() for series in (close, high, low, volume)):
        return False, {"reason": "底部发现数据不完整"}

    current = _as_float(price) or float(close.iloc[-1])
    if current <= 0:
        return False, {"reason": "底部发现价格无效"}
    close.iloc[-1] = current
    high.iloc[-1] = max(float(high.iloc[-1]), current)
    if current_vol > 0:
        volume.iloc[-1] = current_vol
    if current_open > 0:
        open_.iloc[-1] = current_open

    low_60 = float(low.tail(BOTTOM_DISCOVERY_LOOKBACK_DAYS).min())
    rise_from_low = (current / low_60 - 1) * 100 if low_60 > 0 else 999
    if not 0 <= rise_from_low <= BOTTOM_DISCOVERY_MAX_RISE_FROM_LOW:
        return False, {"reason": f"距60日低点涨幅不在0%-12%区间({rise_from_low:.1f}%)"}

    prior_floor = float(low.iloc[-BOTTOM_DISCOVERY_LOOKBACK_DAYS:-3].min())
    recent_low3 = float(low.tail(3).min())
    no_new_low = recent_low3 >= prior_floor * (1 - BOTTOM_DISCOVERY_NO_NEW_LOW_TOLERANCE)
    if not no_new_low:
        return False, {"reason": "最近3日仍在创新低，暂按下跌中继处理"}

    prior_volume = float(volume.iloc[-25:-5].mean())
    base_volume_ratio = float(volume.iloc[-5:-1].mean()) / prior_volume if prior_volume > 0 else 0
    current_volume_ratio = float(volume.iloc[-1]) / prior_volume if prior_volume > 0 else 0
    if base_volume_ratio > BOTTOM_DISCOVERY_MAX_BASE_VOLUME_RATIO:
        return False, {"reason": f"底部量能尚未收缩({base_volume_ratio:.2f}x)"}
    if current_volume_ratio > BOTTOM_DISCOVERY_MAX_CURRENT_VOLUME_RATIO:
        return False, {"reason": f"当日量能已过度放大({current_volume_ratio:.2f}x)，不属于底部发现"}

    range_pct = (high - low).clip(lower=0) / close.replace(0, pd.NA)
    prior_range = float(range_pct.iloc[-25:-5].mean())
    recent_range = float(range_pct.tail(5).mean())
    range_contraction_ratio = recent_range / prior_range if prior_range > 0 else 999
    if range_contraction_ratio > BOTTOM_DISCOVERY_MAX_RANGE_CONTRACTION_RATIO:
        return False, {"reason": f"底部波动尚未收敛({range_contraction_ratio:.2f}x)"}

    ema5 = close.ewm(span=5, adjust=False).mean()
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema20_5d_ago = float(ema20.iloc[-6])
    ema20_slope = (float(ema20.iloc[-1]) / ema20_5d_ago - 1) * 100 if ema20_5d_ago > 0 else -999
    if ema20_slope < BOTTOM_DISCOVERY_MIN_EMA20_SLOPE_5D_PCT:
        return False, {"reason": f"EMA20仍快速下行({ema20_slope:.1f}%)"}

    prior_low3 = float(low.iloc[-6:-3].min())
    higher_low = recent_low3 > prior_low3
    reclaimed_short_ma = current >= float(ema5.iloc[-1]) and current >= float(ema10.iloc[-1])
    day_range = max(float(high.iloc[-1]) - float(low.iloc[-1]), 0.01)
    close_position = (current - float(low.iloc[-1])) / day_range
    bullish_reversal = current > float(open_.iloc[-1]) and close_position >= 0.6
    ignition_confirmed = higher_low and reclaimed_short_ma and bullish_reversal
    stage = "B1_REVERSAL" if ignition_confirmed else "B0_BASE"
    stage_label = "B1止跌转强" if ignition_confirmed else "B0底部候选"
    score = round(
        52
        + max(0, BOTTOM_DISCOVERY_MAX_RISE_FROM_LOW - rise_from_low) * 0.8
        + max(0, 1 - base_volume_ratio) * 12
        + max(0, 1 - range_contraction_ratio) * 10
        + (12 if ignition_confirmed else 0),
        1,
    )
    action = (
        "起涨预警：只观察，等待板块启动、确认价与量能共同确认"
        if ignition_confirmed
        else "底部观察：等待更高低点、短均线收复和反转K确认"
    )
    prev_close = float(close.iloc[-2])
    daily_pct = (current / prev_close - 1) * 100 if prev_close > 0 else 0.0
    rsi = pd.to_numeric(work.get("RSI"), errors="coerce")
    macd_dif = pd.to_numeric(work.get("MACD_DIF"), errors="coerce")
    return True, {
        "代码": code,
        "名称": name,
        "现价": round(current, 2),
        "涨幅%": round(daily_pct, 2),
        "RSI": round(float(rsi.iloc[-1]), 2) if rsi is not None and not pd.isna(rsi.iloc[-1]) else None,
        "DIF": round(float(macd_dif.iloc[-1]), 4) if macd_dif is not None and not pd.isna(macd_dif.iloc[-1]) else None,
        "Score": score,
        "raw_score": score,
        "strategy_type": "bottom_discovery",
        "signal": stage_label,
        "reason": action,
        "bottom_discovery_watch_only": True,
        "bottom_discovery_stage": stage,
        "bottom_discovery_action": action,
        "trade_eligible": False,
        "trade_bucket": "OBSERVE",
        "bottom_discovery_metrics": {
            "rise_from_60d_low": round(rise_from_low, 2),
            "base_volume_ratio": round(base_volume_ratio, 2),
            "current_volume_ratio": round(current_volume_ratio, 2),
            "range_contraction_ratio": round(range_contraction_ratio, 2),
            "ema20_slope_5d_pct": round(ema20_slope, 2),
            "higher_low": higher_low,
            "reclaimed_short_ma": reclaimed_short_ma,
            "bullish_reversal": bullish_reversal,
        },
    }


def _check_sequoia_research_strategy(
    df: pd.DataFrame,
    code: str,
    name: str,
    strategy_type: str,
) -> tuple[bool, Dict[str, Any]]:
    """Evaluate adapted pattern research without granting execution permission."""
    from core.sequoia_research import (
        high_tight_flag_signal_mask,
        limit_up_shakeout_signal_mask,
        ma_volume_signal_mask,
        rps_breakout_proximity_mask,
        signal_metrics,
        turtle_breakout_signal_mask,
        trader_vic_2b_signal_mask,
        uptrend_limit_down_signal_mask,
    )

    masks = {
        "high_tight_flag": lambda: high_tight_flag_signal_mask(df),
        "turtle_breakout": lambda: turtle_breakout_signal_mask(df),
        "limit_up_shakeout": lambda: limit_up_shakeout_signal_mask(df, code),
        "ma_volume": lambda: ma_volume_signal_mask(df),
        "uptrend_limit_down": lambda: uptrend_limit_down_signal_mask(df, code),
        "rps_breakout": lambda: rps_breakout_proximity_mask(df),
        "trader_vic_2b": lambda: trader_vic_2b_signal_mask(df),
    }
    if strategy_type not in masks:
        return False, {"reason": "未知研究策略"}
    mask = masks[strategy_type]()
    if mask.empty or not bool(mask.iloc[-1]):
        return False, {"reason": "未满足研究形态"}

    close = pd.to_numeric(df["收盘"], errors="coerce")
    open_ = pd.to_numeric(df["开盘"], errors="coerce")
    current = float(close.iloc[-1])
    previous = float(close.iloc[-2])
    daily_pct = (current / previous - 1) * 100 if previous > 0 else 0.0
    labels = {
        "high_tight_flag": ("HTF高位收敛", "高位窄幅缩量整理，等待放量突破整理区高点"),
        "turtle_breakout": ("20日新高突破", "简单突破基准已命中，等待收盘与次日价格确认"),
        "limit_up_shakeout": ("涨停后洗盘", "放量换手但支撑未破，等待再次转强，禁止直接抄底"),
        "ma_volume": ("均线放量金叉", "5日线上穿20日线并放量；研究信号，不代表追买"),
        "uptrend_limit_down": ("上升趋势急跌", "前一交易日20/60日线多头排列后放量跌停；只观察风险释放"),
        "rps_breakout": ("RPS强势突破观察", "120日相对强度前10%且接近120日高点；等待后续确认"),
        "trader_vic_2b": ("Trader Vic 2B反转", "20日支撑假跌破后放量收复；等待确认，不追突破"),
    }
    metric_fields = {
        "high_tight_flag": "high_tight_flag_metrics",
        "turtle_breakout": "turtle_breakout_metrics",
        "limit_up_shakeout": "limit_up_shakeout_metrics",
        "ma_volume": "ma_volume_metrics",
        "uptrend_limit_down": "uptrend_limit_down_metrics",
        "rps_breakout": "rps_breakout_metrics",
        "trader_vic_2b": "trader_vic_2b_metrics",
    }
    signal, instruction = labels[strategy_type]
    score = {
        "high_tight_flag": 66.0,
        "turtle_breakout": 70.0,
        "limit_up_shakeout": 62.0,
        "ma_volume": 65.0,
        "uptrend_limit_down": 55.0,
        "rps_breakout": 68.0,
        "trader_vic_2b": 68.0,
    }[strategy_type]
    flag = f"{strategy_type}_watch_only"
    return True, {
        "代码": code,
        "名称": name,
        "现价": round(current, 2),
        "涨幅%": round(daily_pct, 2),
        "Score": score,
        "raw_score": score,
        "strategy_type": strategy_type,
        "signal": signal,
        "reason": instruction,
        "shadow_instruction": instruction,
        "sequoia_research_shadow_only": True,
        "release_state": "SHADOW",
        "trade_eligible": False,
        "trade_bucket": "SHADOW",
        flag: True,
        metric_fields[strategy_type]: signal_metrics(df, strategy_type, code),
        "current_body_pct": round((current / float(open_.iloc[-1]) - 1) * 100, 2)
        if float(open_.iloc[-1]) > 0 else None,
    }


def _early_value_sector_started(res: Dict[str, Any], sector_info: Dict[str, Any]) -> bool:
    phase = str(sector_info.get("sector_phase") or "")
    score = _as_float(sector_info.get("sector_momentum_score"))
    breadth = _as_float(sector_info.get("sector_breadth"))
    pct_5d = _as_float(sector_info.get("sector_5d_pct"))
    return phase == "SECTOR_EARLY" or (score >= 58 and breadth >= 60 and 0 < pct_5d < 8)


def _apply_early_value_sector_filter(
    results: List[Dict[str, Any]],
    sector_map: Dict[str, str],
    sector_strength: Dict[str, Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int, bool]:
    kept: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    for res in results:
        sector = sector_map.get(str(res.get('代码', '')).zfill(6), res.get('行业') or '未知')
        sector_info = sector_strength.get(sector, {})
        res['行业'] = sector
        res.update(sector_info)
        if _early_value_sector_started(res, sector_info):
            res['early_value_sector_confirmed'] = True
            res['early_value_action'] = "早期性价比追踪：只观察，等回踩不破后放量站稳确认"
            kept.append(res)
        else:
            res['early_value_sector_confirmed'] = False
            res['early_value_sector_pending'] = True
            res['early_value_action'] = "早期性价比追踪：技术面符合，板块未启动，仅放入待确认观察池"
            pending.append(res)

    if kept:
        return kept, len(pending), False
    return pending, 0, bool(pending)


def _right_side_quality_confirmed(res: Dict[str, Any]) -> bool:
    """Right-side entries may be extended, but must prove quality and execution control."""
    action = _pa_plan_action(res)
    raw_score = _as_float(res.get('raw_score') or res.get('Score') or res.get('score'))
    pa_score = _as_float(res.get('price_action_score'))
    risk_pct = _as_float(res.get('pa_risk_pct'))
    current_price = _candidate_price(res)
    entry_price = _effective_entry_price(res)
    has_volume = _has_volume_confirmation(res)
    stable_close = _has_stable_close_confirmation(res)
    sector_alignment = _as_float(res.get('sector_alignment_score'), 50)
    setup = str(res.get('pa_trade_setup') or "")
    setup_quality = _trade_setup_quality(res, setup, sector_alignment, _sector_strength_score(res))
    strong_structure = (
        res.get('price_action_signal') == '强多头趋势K'
        or setup_quality in {"H1_TRADABLE", "H2_TRADABLE"}
        or res.get('pa_breakout_quality') == '强突破'
    )
    if setup_quality in {"H1_RAW", "H2_RAW"}:
        strong_structure = False
    sector_ok = sector_alignment >= MIN_EXECUTION_SECTOR_ALIGNMENT
    price_confirmed = not entry_price or not current_price or current_price >= entry_price
    risk_ok = risk_pct <= 0 or risk_pct <= MAX_EXECUTION_RISK_PCT
    return (
        action == "READY"
        and raw_score >= MIN_RAW_EXECUTION_SCORE
        and pa_score >= 70
        and risk_ok
        and price_confirmed
        and stable_close
        and sector_ok
        and (has_volume or strong_structure)
    )


def _early_entry_candidate(res: Dict[str, Any], cap_bucket: str) -> bool:
    """Allow a small, explicit pre-confirmation watchlist before the strict A entry."""
    action = _pa_plan_action(res)
    if action != "READY":
        return False
    strategy_type = str(res.get('strategy_type') or "")
    if strategy_type not in CORE_TRADE_STRATEGIES:
        return False
    current_price = _candidate_price(res)
    entry_price = _as_float(res.get('pa_entry_price') or res.get('entry_price'))
    if current_price <= 0 or entry_price <= 0:
        return False
    confirm_gap_pct = (entry_price - current_price) / entry_price * 100
    if confirm_gap_pct < 0 or confirm_gap_pct > EARLY_ENTRY_MAX_CONFIRM_GAP_PCT:
        return False
    if float(res.get('涨幅%', 0) or 0) >= _near_limit_pct(res.get('代码')):
        return False
    raw_score = _as_float(res.get('raw_score') or res.get('Score') or res.get('score'))
    pa_score = _as_float(res.get('price_action_score'))
    risk_pct = _as_float(res.get('pa_risk_pct'))
    sector_alignment = _as_float(res.get('sector_alignment_score'))
    sector_strength = _sector_strength_score(res)
    stock_sector_fit = _stock_sector_fit_score(res)
    if raw_score < MIN_RAW_EXECUTION_SCORE or pa_score < 70:
        return False
    if risk_pct > 0 and risk_pct > MAX_EXECUTION_RISK_PCT:
        return False
    if sector_alignment < 82:
        return False
    if sector_strength < MIN_EXECUTION_SECTOR_STRENGTH or stock_sector_fit < MIN_EXECUTION_STOCK_SECTOR_FIT:
        return False
    setup = str(res.get('pa_trade_setup') or "")
    if _is_h1_first_entry(setup) and sector_alignment < H1_EARLY_SECTOR_ALIGNMENT:
        return False
    if cap_bucket in {"MICRO", "UNKNOWN"}:
        return False
    if cap_bucket == "SMALL" and sector_alignment < 90:
        return False
    if not _has_stable_close_confirmation(res):
        return False
    has_volume_or_structure = (
        _has_volume_confirmation(res)
        or res.get('price_action_signal') == '强多头趋势K'
        or res.get('pa_h2_quality') == '强'
        or res.get('pa_breakout_quality') == '强突破'
    )
    return bool(has_volume_or_structure)


def _brooks_rank_adjustment(res: Dict[str, Any]) -> float:
    """Apply only execution-level PA adjustments; component quality is calibrated separately."""
    adjustment = 0.0
    action = (res.get('pa_trade_plan') or {}).get('action')
    if action == 'READY':
        adjustment += 4
    elif action == 'AVOID':
        adjustment -= 8
    if (res.get('pa_trap_risk') or 0) >= 75:
        adjustment -= 4
    if res.get('pa_trend_damage') in {'跌破EMA20', '跌破EMA60', '短线低点破坏'}:
        adjustment -= 4
    return max(-10.0, min(10.0, adjustment))


def _is_momentum_watch_candidate(res: Dict[str, Any], vetoes: List[str]) -> bool:
    """Keep strong movers visible without promoting them to executable candidates."""
    if not vetoes:
        return False
    fatal_vetoes = {
        "地雷预警", "市值<30亿", "板块下跌", "上影线过长",
        "Brooks风险偏高", "异常价格跳变", "价格行为回避", "低质量价格结构",
    }
    if any(v in fatal_vetoes for v in vetoes):
        return False
    has_momentum_veto = any(v in {"涨幅>7%", "5日涨>15%"} for v in vetoes)
    if not has_momentum_veto:
        return False

    pa_score = float(res.get('price_action_score') or 0)
    plan_action = (res.get('pa_trade_plan') or {}).get('action')
    structure_ok = (
        plan_action == 'READY'
        or pa_score >= 65
        or res.get('pa_h2_quality') == '强'
        or res.get('pa_breakout_quality') == '强突破'
        or res.get('price_action_signal') == '强多头趋势K'
    )
    volume_ok = bool(res.get('pa_volume_confirmed')) or res.get('pa_volume_pattern') == '放量突破'
    trend_ok = res.get('price_action_regime') in {'向上突破', '多头趋势'} or res.get('pa_weekly_context') == '周线多头'
    return structure_ok and (volume_ok or trend_ok)


def _apply_trade_execution_profile(res: Dict[str, Any]) -> None:
    """Classify scan hits into executable, observation, or blocked trade buckets."""
    action = _pa_plan_action(res)
    setup = str(res.get('pa_trade_setup') or "")
    strategy_type = str(res.get('strategy_type') or "")
    pa_execution = classify_price_action_execution(res)
    res['pa_execution_policy_version'] = pa_execution['version']
    res['pa_execution_tier'] = pa_execution['tier']
    res['pa_execution_tier_label'] = pa_execution['label']
    res['pa_execution_hard_blocked'] = pa_execution['hard_blocked']
    res['pa_execution_hard_reason'] = pa_execution['hard_reason']
    blockers: List[str] = []
    raw_score = _as_float(res.get('raw_score') or res.get('Score') or res.get('score'))
    risk_pct = _as_float(res.get('pa_risk_pct'))
    current_price = _candidate_price(res)
    entry_price = _effective_entry_price(res)
    turnover = _as_float(res.get('换手率') or res.get('turnover'))
    mkt_cap_yi = _as_float(res.get('mkt_cap_yi'))
    cap_bucket = _market_cap_bucket(mkt_cap_yi)
    res['mkt_cap_bucket'] = cap_bucket
    sector_alignment = _as_float(res.get('sector_alignment_score'))
    sector_strength = _sector_strength_score(res)
    stock_sector_fit = _stock_sector_fit_score(res)
    strong_sector_core = _strong_sector_core_candidate(res, sector_strength, stock_sector_fit)
    strong_sector_rear = _strong_sector_rear_candidate(res, sector_strength)
    setup_quality = _trade_setup_quality(res, setup, sector_alignment, sector_strength)
    res['pa_trade_setup_quality'] = setup_quality
    res['pa_trade_setup_quality_label'] = _trade_setup_quality_label(setup_quality)
    if strong_sector_core:
        res['sector_core_role_candidate'] = True
        res['sector_core_role_label'] = "强板块核心股"
    if strong_sector_rear:
        res['sector_rear_role_watch'] = True
        res['sector_core_role_label'] = "强板块后排观察"
    right_side_quality = _right_side_quality_confirmed(res)
    trend_continuation = _is_trend_continuation_candidate(res)
    early_entry = _early_entry_candidate(res, cap_bucket)
    observe_promotion = _observe_promotion_candidate(res, sector_alignment)
    if trend_continuation:
        res['trend_continuation_candidate'] = True
    if early_entry:
        res['early_trade_candidate'] = True
        res['early_trade_reason'] = f"距确认价<{EARLY_ENTRY_MAX_CONFIRM_GAP_PCT:.1f}%，主线强联动，允许小仓提前复核"
    if observe_promotion:
        res['observe_promotion_candidate'] = True
        res['observe_promotion_action'] = "观察转可买：回踩不破支撑后，放量站回确认价或尾盘站稳再小仓"

    if _is_abnormal_price_move(res.get('代码'), res.get('涨幅%')):
        blockers.append("异常价格跳变，排除交易")
    if pa_execution['hard_reason']:
        blockers.append(pa_execution['hard_reason'])
    elif action and action not in EXECUTABLE_PA_ACTIONS:
        blockers.append("交易计划未确认")
    elif not action and strategy_type in {"tv_dual", "tv_dual_strict"}:
        blockers.append("缺少价格行为交易计划")
    if strategy_type in DISCOVERY_ONLY_STRATEGIES:
        blockers.append("普通tv_dual仅用于发现，需严格双策略确认")
    if res.get("tv_reversal_watch_only"):
        blockers.append("强修复观察仅供观察，等待周线转强和次日确认")
    tv_execution_tier = str(res.get("tv_execution_tier") or "")
    if strategy_type == "tv_dual" and tv_execution_tier == "C":
        blockers.append("ZP单信号仅研究观察，禁止自动执行")
    elif strategy_type == "tv_dual" and tv_execution_tier == "B":
        if _as_float(res.get("price_action_score")) < TV_MA_ONLY_MIN_PA_SCORE:
            blockers.append(f"MA单信号价格行为分<{TV_MA_ONLY_MIN_PA_SCORE:g}，只观察")
        if action == "AVOID":
            blockers.append("MA单信号价格行为回避，禁止实盘")
        effective_regime = str(
            res.get("effective_market_regime") or res.get("market_regime") or "UNKNOWN"
        ).upper()
        if effective_regime != "OFFENSIVE":
            blockers.append("MA单信号仅进攻市场允许执行")
    if res.get("bottom_discovery_watch_only"):
        blockers.append("底部起涨发现仅供观察，等待板块、确认价和量能共同确认")
    if res.get("weekly_pattern_watch_only"):
        blockers.append("周线四形态未经样本外验证，仅供观察，不产生交易指令")
    if res.get("sequoia_research_shadow_only"):
        blockers.append("外部策略概念尚未通过滚动样本外验证，仅限SHADOW观察")
    if res.get('sector_trend') == 'DOWN':
        blockers.append("板块下跌")
    if res.get('sector_phase') == 'SECTOR_FADE' and res.get('sector_alignment_score', 0) < 60:
        blockers.append("板块扩散转弱")
    if 0 < sector_strength < WEAK_SECTOR_ALIGNMENT:
        blockers.append("板块强度弱，禁止实盘")
    elif 0 < sector_strength < MIN_EXECUTION_SECTOR_ALIGNMENT:
        blockers.append("板块强度不足，降级观察")
    if sector_strength >= MIN_EXECUTION_SECTOR_STRENGTH and 0 < stock_sector_fit < MIN_EXECUTION_STOCK_SECTOR_FIT:
        blockers.append("强板块但个股适配不足，降级观察")
    if strong_sector_rear:
        blockers.append("强板块后排角色，等待转强为核心股")
    if 0 < sector_alignment < WEAK_SECTOR_ALIGNMENT:
        blockers.append("弱板块联动，禁止实盘")
    if 0 < sector_alignment < MIN_EXECUTION_SECTOR_ALIGNMENT:
        blockers.append(f"板块联动<{MIN_EXECUTION_SECTOR_ALIGNMENT:.0f}，降级观察")
    weekly_context = str(res.get('pa_weekly_context') or "")
    weekly_permission = str(res.get('pa_weekly_permission') or "")
    trend_phase = str(res.get('pa_trend_phase') or "")
    if weekly_permission == "WAIT" or weekly_context in {"周线空头", "周线向下破位", "周线数据不足"}:
        blockers.append(f"{weekly_context or '周线数据不足'}，等待周线转强")
    if weekly_context in {"周线中性", "周线交易区间"}:
        blockers.append(f"{weekly_context}，降级观察")
    if trend_phase == "震荡观察":
        blockers.append("震荡观察胜率偏低，降级观察")
    if raw_score and raw_score < MIN_RAW_EXECUTION_SCORE:
        blockers.append(f"原始策略分<{MIN_RAW_EXECUTION_SCORE:.0f}，只观察")
    if risk_pct > HARD_EXECUTION_RISK_PCT:
        blockers.append(f"结构风险>{HARD_EXECUTION_RISK_PCT:.0f}%，禁止实盘")
    elif risk_pct > MAX_EXECUTION_RISK_PCT:
        blockers.append(f"结构风险>{MAX_EXECUTION_RISK_PCT:.0f}%，等待更优买点")
    if entry_price > 0 and current_price > 0:
        if (
            res.get('execution_plan_frozen')
            and res.get('frozen_confirmation_triggered')
            and _as_float(res.get('frozen_entry_extension_pct')) > MAX_FROZEN_ENTRY_EXTENSION_PCT
        ):
            blockers.append(
                f"距冻结确认价涨幅>{MAX_FROZEN_ENTRY_EXTENSION_PCT:.0f}%，等待回踩"
            )
        if not _confirmation_price_reached(current_price, entry_price):
            blockers.append("未站上确认价，等待突破确认")
        else:
            if not _has_volume_confirmation(res):
                blockers.append("站上确认价但量能未确认")
            if res.get('pa_close_time_eligible') is False:
                blockers.append("盘中仅临时触价，等待14:30后站稳确认")
            elif not _has_stable_close_confirmation(res):
                blockers.append("冲高回落，站稳未确认")
    if mkt_cap_yi >= LOW_TURNOVER_MKT_CAP_YI and 0 < turnover < LOW_TURNOVER_MIN_PCT:
        blockers.append("大市值低换手，右侧弹性不足")
    elif mkt_cap_yi >= HIGH_TURNOVER_MKT_CAP_YI and 0 < turnover < HIGH_TURNOVER_MIN_PCT:
        blockers.append("高市值换手不足，等待放量确认")
    if cap_bucket == "SMALL" and not (right_side_quality and sector_alignment >= 85):
        blockers.append("小市值弹性票，需主线强联动和价量确认")
    if cap_bucket == "MEGA" and 0 < turnover < 1.2:
        blockers.append("超大市值换手不足，等待机构资金确认")
    if res.get('money_flow_status') == 'missing':
        blockers.append("资金流数据缺失，降级观察")
    elif res.get('money_flow_status') == 'negative':
        blockers.append("主力资金流出，等待资金回流")
    if res.get('capital_event_risk'):
        blockers.append("近期资本事件利好兑现，等待二次确认")
    if _as_float(res.get('pct_5d')) > SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT:
        blockers.append("5日涨幅过度延伸，等待回踩")
    if res.get('revival_level') == "MOMENTUM_ACCELERATION":
        blockers.append("动量加速票，次日不高开追价后小仓复核")
    elif res.get('revival_level') == "NEXT_DAY_CONFIRM":
        blockers.append("历史信号复活仅观察，等次日确认")
    elif res.get('revival_level') == "AVOID_CHASE":
        blockers.append("历史信号复活但禁止追涨")
    if res.get('momentum_acceleration_watch_only'):
        blockers.append("强趋势加速观察，次日确认后小仓复核")
    for blocker in res.get('revival_blockers') or []:
        if blocker not in blockers:
            blockers.append(blocker)
    # 改动 #13 扫描端增强：涨停/近涨停默认加 blocker（等待隔日确认，防追高）。
    # 但若 apply_limit_up_features 注入的 limit_up_status='BROKEN'（曾封板但已开板），
    # 说明抛压已释放、未真正封死，反而是低吸机会——不加 blocker 并标记，后续可加分。
    near_limit = float(res.get('涨幅%', 0) or 0) >= _near_limit_pct(res.get('代码'))
    lu_status = res.get('limit_up_status')
    if near_limit and lu_status != 'BROKEN':
        blockers.append("涨停/近涨停，等待隔日确认")
    elif lu_status == 'BROKEN' and near_limit:
        res['limit_up_unsealed'] = True  # 封板失败=抛压释放，标记为低吸机会（只增字段不改契约）
    elif float(res.get('涨幅%', 0) or 0) > 7 and not right_side_quality:
        blockers.append("涨幅偏高且质量未确认，等待回踩/次日确认")
    if _is_h1_first_entry(setup) and not _h1_execution_confirmed(res, sector_alignment):
        blockers.append("H1首次入场仅强主线放量确认可小仓复核")
    if setup_quality == "H2_RAW":
        blockers.append("H2二次入场未满足量能/质量/风险确认，仅观察")

    blockers = _dedupe_trade_blockers(blockers)

    price_triggered = _confirmation_price_reached(current_price, entry_price)
    volume_confirmed = _has_volume_confirmation(res)
    close_confirmed = _has_stable_close_confirmation(res)
    if price_triggered and volume_confirmed and close_confirmed:
        confirmation_state = "ENTRY_CONFIRMED"
    elif price_triggered:
        confirmation_state = "PRICE_TRIGGERED"
    elif action == "READY":
        confirmation_state = "SETUP_READY"
    else:
        confirmation_state = "WAITING_SETUP"
    res["pa_setup_confirmed"] = action == "READY"
    res["pa_plan_triggered"] = price_triggered
    res["pa_close_confirmed"] = close_confirmed
    res["pa_confirmation_state"] = confirmation_state
    pa_execution_stage = _apply_price_action_execution_stage(res)
    if pa_execution_stage == "EOD_CONFIRMED":
        blockers.append("信号日尾盘确认，次一交易日复核执行")
        blockers = _dedupe_trade_blockers(blockers)

    score = float(res.get('final_rank_score', res.get('Score', 0)) or 0)
    chip_delta = float(res.get('chip_score_delta') or 0)
    if chip_delta:
        score += max(-8.0, min(6.0, chip_delta))
        res['chip_strategy_adjustment'] = round(max(-8.0, min(6.0, chip_delta)), 1)
        if res.get('chip_buy_impact') == '抑制买入':
            blockers.append('筹码峰迁移不利，抑制新买点')

    # ── trade-gate-v2：弱条件降级为 trade_cautions（不拦截TRADE，仅扣分/缩仓）──
    # 硬阻断（数据异常/结构失效/风险>20%/严重公告/不可成交/板块明确退潮）保留在
    # trade_blockers；关闭开关则恢复 v1 全拦截行为。
    cautions: List[str] = []
    if TRADE_GATE_V2_ENABLED:
        cautions = [b for b in blockers if _is_soft_trade_gate_condition(b)]
        if cautions:
            blockers = [b for b in blockers if b not in cautions]
        cautions = [
            item.replace("禁止自动执行", "降低仓位优先级")
            .replace("禁止实盘", "降低仓位优先级")
            .replace("降级观察", "降低仓位优先级")
            for item in cautions
        ]
        cautions.extend(str(item) for item in (res.get('sop_soft_vetoes') or []))
        cautions = list(dict.fromkeys(cautions))
    res['trade_gate_policy_version'] = (
        TRADE_GATE_POLICY_VERSION if TRADE_GATE_V2_ENABLED else "trade-gate-v1"
    )
    res['trade_cautions'] = cautions

    if action == "READY":
        score += 8
    elif action == "WATCH":
        score += 2
    elif action == "AVOID":
        score -= 20
    elif action:
        score -= 8

    if setup in BLOCKED_PA_SETUPS:
        score -= 12
    if res.get('sector_trend') == 'LEAD':
        score += 5
    if (res.get('sector_alignment_score') or 0) >= 75:
        score += 5
    if trend_continuation:
        score += 6
    if _has_pullback_reversal_volume(res):
        score += 6
        res['bark_success_profile_match'] = True
    if _is_h2_second_entry(setup, res) and _h2_execution_confirmed(res, sector_strength):
        score += 4
        res['h2_second_entry_boost'] = True
    if _is_sweet_spot_trade_model(res, sector_alignment):
        score += 8
        res['sweet_spot_trade_candidate'] = True
        res['sweet_spot_reason'] = "机会分70-79 + 强联动 + TV均线或ZP，按专门买点模型复核"
    if observe_promotion:
        score += 3
    if str(res.get('sector_phase') or "") in BARK_PROFILE_SECTOR_PHASE_BONUS:
        score += 4
        res['bark_success_profile_match'] = True
    if strong_sector_core:
        score += 5
        res['sector_confirmed_core_candidate'] = True
    elif strong_sector_rear:
        score -= 6
    elif sector_strength < MIN_EXECUTION_SECTOR_ALIGNMENT and stock_sector_fit >= 70:
        res['strong_stock_weak_sector_watch'] = True
    if _bark_profile_setup_confirmed(res, setup, sector_alignment):
        score += 3
        res['bark_success_profile_match'] = True
    if 0 < sector_alignment < WEAK_SECTOR_ALIGNMENT:
        score -= 12
    elif 0 < sector_alignment < MIN_EXECUTION_SECTOR_ALIGNMENT:
        score -= 6
    if weekly_context in {"周线中性", "周线交易区间"}:
        score -= 6
    if trend_phase == "震荡观察":
        score -= 5
    if _is_abnormal_price_move(res.get('代码'), res.get('涨幅%')):
        score -= 25
    if float(res.get('涨幅%', 0) or 0) > 7 and not right_side_quality:
        score -= 8
    # 改动 #13：曾封板但已开板（limit_up_unsealed）= 抛压释放，反而是低吸机会，加分鼓励
    if res.get('limit_up_unsealed'):
        score += 4
    if early_entry:
        score += 5
    if raw_score and raw_score < MIN_RAW_EXECUTION_SCORE:
        score -= 15
    if risk_pct > MAX_EXECUTION_RISK_PCT:
        score -= 10
    if res.get('capital_event_risk'):
        score -= 10
    if cap_bucket == "MICRO":
        score -= 18
    elif cap_bucket == "SMALL":
        score -= 8
    elif cap_bucket == "MEGA" and turnover >= 1.2:
        score += 3

    a_minus_trial = _a_minus_trial_qualified(res, blockers, sector_strength, stock_sector_fit)
    if a_minus_trial:
        res['a_minus_trial'] = True
        res['a_minus_trial_policy_version'] = A_MINUS_TRIAL_POLICY_VERSION
    a_eod_trial = False
    if not a_minus_trial:
        a_eod_trial, a_eod_cautions, a_eod_hard_blockers = _a_eod_controlled_qualified(
            res,
            blockers,
            price_triggered=price_triggered,
            volume_confirmed=volume_confirmed,
            close_confirmed=close_confirmed,
            near_limit=near_limit,
        )
        if a_eod_trial:
            blockers = a_eod_hard_blockers
            res['a_eod_controlled_trial'] = True
            res['a_eod_policy_version'] = A_EOD_CONTROLLED_POLICY_VERSION
            res['a_eod_trade_cautions'] = a_eod_cautions
            from core.risk_constants import A_EOD_CONTROLLED_ENABLED
            if not A_EOD_CONTROLLED_ENABLED:
                # SHADOW：保留打标用于点内对照统计，但不贡献交易资格
                res['a_eod_shadow'] = True
                res.setdefault('trade_cautions', []).append(
                    "A-EOD受控通道SHADOW中（E3前推验证未通过），仅观察不签发"
                )
    fatal_markers = ("回避", "结构不进入交易池", "结构失效", "异常价格跳变", "板块下跌", "禁止实盘")
    has_fatal_blocker = any(any(marker in b for marker in fatal_markers) for b in blockers)
    # 字母等级不再参与执行。严格模式直接使用策略、板块、价格行为和阻断条件。
    if STRICT_REAL_SIGNAL_GATE:
        if TRADE_GATE_V2_ENABLED:
            # v2：核心策略 + 无硬阻断 + PA计划READY + 站上确认价 + 收盘稳定。
            # 共振与板块强度/联动/适配转为机会分加分与扣分，不再作为硬合取项。
            formal_trade = (
                not blockers
                and not list(res.get('sop_vetoes') or [])
                and strategy_type in CORE_TRADE_STRATEGIES
                and pa_execution['tier'] == "NORMAL"
                and action == "READY"
                and price_triggered
                and close_confirmed
            )
        else:
            formal_trade = (
                not blockers
                and not list(res.get('sop_vetoes') or [])
                and strategy_type in CORE_TRADE_STRATEGIES
                and res.get('共振') == "🔥 核心热点"
                and _trade_quality_confirmed(res, sector_strength, stock_sector_fit)
            )
        # SHADOW 模式下 a_eod_trial 不再贡献交易资格（res 打标保留供对照统计）
        from core.risk_constants import A_EOD_CONTROLLED_ENABLED
        trade_eligible = formal_trade or a_minus_trial or (a_eod_trial and A_EOD_CONTROLLED_ENABLED)
    else:
        trade_eligible = not blockers and strategy_type in CORE_TRADE_STRATEGIES
    if trade_eligible:
        bucket = "TRADE"
        execution_policy = (
            "A_MINUS_CONTROLLED_TRIAL" if a_minus_trial
            else "A_EOD_CONTROLLED_TRIAL" if a_eod_trial
            else "BARK_CONFIRMED_TRADE"
        )
    elif early_entry and not has_fatal_blocker:
        bucket = "EARLY"
        execution_policy = "EARLY_REVIEW_ONLY"
    elif pa_execution['tier'] == "T1_CONFIRM" and not has_fatal_blocker:
        bucket = "OBSERVE"
        execution_policy = "PA_T1_CONFIRMATION"
    elif pa_execution['tier'] == "PULLBACK_WATCH" and not has_fatal_blocker:
        bucket = "OBSERVE"
        execution_policy = "PA_PULLBACK_WATCH"
    elif observe_promotion and not has_fatal_blocker:
        bucket = "OBSERVE"
        execution_policy = "WAIT_CONFIRMATION"
    elif has_fatal_blocker:
        bucket = "BLOCK"
        execution_policy = "NO_TRADE"
    else:
        bucket = "OBSERVE"
        execution_policy = "WAIT_CONFIRMATION"

    res['trade_eligible'] = trade_eligible
    res['trade_bucket'] = bucket
    res['trade_execution_policy'] = execution_policy
    res['requires_bark_confirmation'] = not trade_eligible
    res['trade_blockers'] = blockers
    _apply_price_action_execution_stage(res, executable=trade_eligible)
    res['final_trade_score'] = round(score, 2)
    res['display_trade_score'] = round(max(0, min(100, score)), 1)
    if strategy_type == "pine":
        res['trade_timeframe'] = "SHORT_1_2D"
        res['exit_hint'] = "Pine信号按1-2个交易日短线管理，次日不强则降级观察"


def _inject_failure_pattern(results, engine):
    """改动 #17：预查近 FAILURE_LOOKBACK_DAYS 天内同代码+策略的失败次数，注入到 res['recent_failure_count']。

    查询 failure_samples（手动亏损 + 风控自动止损平仓均会写入），按 (code, strategy_type)
    聚合近期失败次数。
    改动 A4：原实现只按 code 聚合，不区分 strategy_type——一只票用 tv_dual 失败 2 次，
    改用 pine 策略（完全不同信号逻辑）也被误杀。现按 (code, strategy_type) 配对，
    只否决"同代码同策略"的反复失败。
    engine 为 None 或查询失败时静默跳过（不阻断扫描）。
    """
    if not results or engine is None:
        return
    try:
        from sqlalchemy import text
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=FAILURE_LOOKBACK_DAYS)).isoformat()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT code, strategy_type, COUNT(*) AS cnt
                FROM failure_samples
                WHERE sample_date >= :cutoff AND pnl_pct < 0
                GROUP BY code, strategy_type
            """), {"cutoff": cutoff}).fetchall()
        # 改动 A4：键改为 (code, strategy_type) 元组
        fail_map = {(str(r[0]), str(r[1] or "")): int(r[2]) for r in rows} if rows else {}
    except Exception:
        fail_map = {}
    for res in results:
        _code = str(res.get('代码', ''))
        _strat = str(res.get('strategy_type') or "")
        # 优先按 (code, strategy_type) 精确匹配；无策略维度时回退到纯 code 匹配
        cnt = fail_map.get((_code, _strat), 0)
        if cnt == 0 and _strat:
            # 回退：failure_samples 中 strategy_type 为空的历史记录仍按 code 计入
            cnt = fail_map.get((_code, ""), 0)
        res['recent_failure_count'] = cnt


def _inject_capital_event_risk(results, engine, lookback_days: int = 60, as_of: Optional[str] = None):
    """Mark recent financing/unlock/reduction events that can turn into 'good news sold' risk.

    as_of（回放数据日）给定时，事件窗口改为 [as_of-lookback, as_of]，且
    publish_time 不得晚于 as_of——否则回放会命中"未来"新闻（点时违约）。
    """
    if not results or engine is None:
        return
    codes = [str(res.get('代码') or '').zfill(6) for res in results if res.get('代码')]
    if not codes:
        return
    as_of_date = date.fromisoformat(str(as_of)[:10]) if as_of else date.today()
    cutoff = (as_of_date - timedelta(days=lookback_days)).isoformat()
    upper_bound = as_of_date.isoformat()
    keyword_expr = " OR ".join([f"n.title LIKE :kw{i} OR COALESCE(n.content, '') LIKE :kw{i}" for i, _ in enumerate(CAPITAL_EVENT_KEYWORDS)])
    params = {f"kw{i}": f"%{kw}%" for i, kw in enumerate(CAPITAL_EVENT_KEYWORDS)}
    params.update({"codes": codes, "cutoff": cutoff, "as_of_end": f"{upper_bound} 23:59:59"})
    try:
        stmt = text(f"""
            SELECT DISTINCT s.stock_code, n.title
            FROM news_stocks s
            JOIN news_raw n ON n.id = s.news_id
            WHERE s.stock_code IN :codes
              AND COALESCE(n.publish_time, n.created_at) >= :cutoff
              AND COALESCE(n.publish_time, n.created_at) <= :as_of_end
              AND ({keyword_expr})
        """).bindparams(bindparam("codes", expanding=True))
        with engine.connect() as conn:
            rows = conn.execute(stmt, params).fetchall()
    except Exception:
        rows = []

    event_map: Dict[str, List[str]] = {}
    for code, title in rows:
        event_map.setdefault(str(code).zfill(6), []).append(str(title or "")[:80])
    for res in results:
        code = str(res.get('代码') or '').zfill(6)
        titles = event_map.get(code, [])
        if titles:
            res['capital_event_risk'] = True
            res['capital_event_titles'] = titles[:3]


def _inject_breakdown_retracement(results, hist_map):
    """改动(上班族Bark v2)：破位反抽陷阱多维评分。

    取 hist_map[code] 近期数据，若检测窗口内有单日大跌 <= BREAKDOWN_DROP_PCT，
    通过四维评分区分"真陷阱"（诱多反抽）与"黄金坑"（强势洗盘）：
      1. 大跌日量能不足（+30）：量比 < TRAP_VOLUME_RATIO_THRESHOLD（非恐慌抛售）
      2. 缩量弱反抽（+25）：大跌后量比递减（恢复期无真实买盘）
      3. MA20 长时间破位（+25）：大跌后在 MA20 下方停留 >= TRAP_MA20_BREAK_DAYS 天
      4. V型未确认突破（+20）：突破日才从 MA20 下方跳到上方，无企稳确认
    输出 res['breakdown_trap_score']（0-100）。≥TRAP_VETO_SCORE 否决；≥TRAP_RISK_SCORE 加风险。
    """
    if not results or not hist_map:
        return
    lookback = BREAKDOWN_LOOKBACK_DAYS
    drop_pct = BREAKDOWN_DROP_PCT
    for res in results:
        code = str(res.get('代码', ''))
        df = hist_map.get(code)
        res['breakdown_trap_score'] = 0
        if df is None or df.empty or '收盘' not in df.columns:
            continue
        try:
            tail = df.tail(lookback + 1).reset_index(drop=True)
            if len(tail) < 2:
                continue
            closes = tail['收盘'].astype(float).tolist()
            # 找检测窗口内的大跌日（单日跌幅 <= drop_pct）
            # 修复 BUG-D：排除最后一条（突破日本身），只找"突破前的大跌"。
            # 否则突破日自身的大跌会被误判为"破位反抽"（假阳性+50）。
            drop_idx = None
            for i in range(1, len(closes) - 1):
                if closes[i - 1] > 0:
                    day_pct = (closes[i] / closes[i - 1] - 1) * 100
                    if day_pct <= drop_pct:
                        drop_idx = i
                        break
            if drop_idx is None:
                continue  # 无前置大跌 → score=0（不标记）

            score = 0
            has_vol_ma = 'Vol_MA20' in tail.columns
            has_ma20 = 'MA20' in tail.columns

            # 维度1：大跌日量能不足（非恐慌抛售 → 缺乏清洗力度 → 更像诱多）
            if has_vol_ma:
                vol_ma_drop = float(tail['Vol_MA20'].iloc[drop_idx]) if pd.notna(tail['Vol_MA20'].iloc[drop_idx]) else 0
                if vol_ma_drop > 0:
                    drop_vol_ratio = float(tail['成交量'].iloc[drop_idx]) / vol_ma_drop
                    if drop_vol_ratio < TRAP_VOLUME_RATIO_THRESHOLD:
                        score += 30  # 温和放量（非恐慌抛售）
            else:
                score += 30  # 无量能数据，保守按量能不足计

            # 维度2：缩量弱反抽（大跌后至最后一日前，量比递减）
            if has_vol_ma and drop_idx < len(tail) - 1:
                post_vrs = []
                for i in range(drop_idx + 1, len(tail)):
                    vma = float(tail['Vol_MA20'].iloc[i]) if pd.notna(tail['Vol_MA20'].iloc[i]) else 0
                    if vma > 0:
                        post_vrs.append(float(tail['成交量'].iloc[i]) / vma)
                # 量比序列递减（后半均值 < 前半均值）→ 缩量弱反抽
                if len(post_vrs) >= 2:
                    mid = len(post_vrs) // 2
                    first_half = sum(post_vrs[:max(1, mid)]) / max(1, mid)
                    second_half = sum(post_vrs[mid:]) / max(1, len(post_vrs) - mid)
                    if second_half < first_half:
                        score += 25

            # 维度3：MA20 长时间破位（大跌后至最后一日前，收盘持续在 MA20 下方）
            if has_ma20:
                below_days = 0
                for i in range(drop_idx, len(tail) - 1):  # 不含最后一根（突破日）
                    ma20v = tail['MA20'].iloc[i]
                    if pd.notna(ma20v) and closes[i] < float(ma20v):
                        below_days += 1
                if below_days >= TRAP_MA20_BREAK_DAYS:
                    score += 25

            # 维度4：V型未确认突破（倒数第二根仍在 MA20 下方 → 突破日才跳上来，无企稳）
            if has_ma20 and len(tail) >= 2:
                prev_ma20 = tail['MA20'].iloc[-2]
                prev_close = closes[-2]
                if pd.notna(prev_ma20) and prev_close < float(prev_ma20):
                    score += 20  # 前一日仍在 MA20 下方 → V型未确认

            res['breakdown_trap_score'] = min(score, 100)
        except Exception:
            res['breakdown_trap_score'] = 0


def _apply_sop_filter(results, market_regime, sector_trends):
    """Apply continuous quality scoring, risk vetoes and execution evidence."""
    regime_status = market_regime.get('status', 'UNKNOWN')

    for res in results:
        vetoes = []
        soft_vetoes = []
        checks = []
        bonuses = []
        risks = []

        # ── 一票否决 ──
        if res.get('影线比', 0) > 0.5:
            # v2：上影线属质量瑕疵而非结构失效，降级扣分（v1 仍为一票否决）。
            if TRADE_GATE_V2_ENABLED:
                soft_vetoes.append("上影线过长")
            else:
                vetoes.append("上影线过长")
        if res.get('warnings') and len(res['warnings']) > 0:
            vetoes.append("地雷预警")
        if 0 < res.get('mkt_cap_yi', 0) < 30:
            vetoes.append("市值<30亿")
        if _is_abnormal_price_move(res.get('代码'), res.get('涨幅%')):
            vetoes.append("异常价格跳变")
        if _pa_plan_action(res) == "AVOID":
            vetoes.append("价格行为回避")
        if str(res.get('pa_trade_setup') or "") in BLOCKED_PA_SETUPS:
            vetoes.append("低质量价格结构")

        # 板块下跌否决
        sector = res.get('行业', '')
        sector_info = sector_trends.get(sector, {})
        if sector_info.get('trend') == 'DOWN':
            vetoes.append("板块下跌")
        if res.get('sector_phase') == 'SECTOR_FADE' and res.get('sector_alignment_score', 0) < 60:
            vetoes.append("板块扩散转弱")
        # 改动(上班族Bark v2)：破位反抽陷阱评分。>=TRAP_VETO_SCORE 一票否决；
        # >=TRAP_RISK_SCORE 加风险标注（不否决，排序扣分）。
        trap_score = res.get('breakdown_trap_score', 0)
        if trap_score >= TRAP_VETO_SCORE:
            vetoes.append(f"破位反抽陷阱(评分{trap_score})")
        elif trap_score >= TRAP_RISK_SCORE:
            risks.append(f"破位反抽疑似(评分{trap_score})，排序扣分")
        # 改动 #17：近期失败模式否决（同一代码+策略近 N 天内多次失败 → 降级，避免反复踩雷）
        if res.get('recent_failure_count', 0) >= FAILURE_VETO_MIN_COUNT:
            # v2：历史连败是经验性风险而非当前结构失效，降级扣分。
            if TRADE_GATE_V2_ENABLED:
                soft_vetoes.append(f"近期失败模式命中({res['recent_failure_count']}次)")
            else:
                vetoes.append(f"近期失败模式命中({res['recent_failure_count']}次)")

        # ── 加权连续评分（替代原计数法）──
        # 胜率改进：优先用 adjusted_win_rate（Wilson 下界），fallback 原始胜率
        _bt_stats = res.get('回测统计', {}) or {}
        _adj_wr = _bt_stats.get('adjusted_win_rate')
        win_rate_str = res.get('历史胜率', '0%')
        try:
            _win_rate_raw = float(str(win_rate_str).replace('%', ''))
        except (ValueError, TypeError):
            _win_rate_raw = 0
        # 使用折扣后胜率（如有），否则用原始胜率
        # 改动 A5：无 Wilson 下界时对原始胜率打 7 折（小样本惩罚）。
        # 原逻辑 fallback 用原始胜率，导致样本=3、胜率=100% 的票（Wilson下界≈42%）
        # 被严重高估。打 7 折后 70%，更接近真实置信度。
        if _adj_wr is not None:
            _win_rate = float(_adj_wr)
        else:
            _win_rate = _win_rate_raw * SMALL_SAMPLE_WIN_RATE_DISCOUNT

        pf_raw = _bt_stats.get('profit_factor', 0)
        try:
            _pf = float(pf_raw)
        except (ValueError, TypeError):
            _pf = 0
        # 期望收益惩罚：expectancy < 0 说明策略长期亏钱，不论胜率多高都应扣分
        _expectancy = float(_bt_stats.get('expectancy', 0) or 0)
        _raw = float(res.get('raw_score') or res.get('Score') or 0)
        _roe = float(res.get('ROE') or 0)
        _yoy = float(res.get('净利YOY') or 0)
        _pa_struct = float(res.get('pa_structure_score') or res.get('price_action_score') or 50)
        _sector_align = float(res.get('sector_alignment_score') or 50)
        _sector_strength = _sector_strength_score(res)
        _stock_sector_fit = _stock_sector_fit_score(res)
        _strong_sector_core = _strong_sector_core_candidate(res, _sector_strength, _stock_sector_fit)
        _strong_sector_rear = _strong_sector_rear_candidate(res, _sector_strength)
        _mkt_cap_yi = float(res.get('mkt_cap_yi') or 0)
        _cap_bucket = _market_cap_bucket(_mkt_cap_yi)
        res['mkt_cap_bucket'] = _cap_bucket

        # 7维加权评分（各维度映射到0-100，再按权重加权求和）
        # 改动 A5：胜率权重 0.25→0.18（胜率受样本量影响大，且在 score_calibration
        # 层还有 0.05 权重双重计入），释放给盈亏比(0.20→0.22)和信号强度(0.15→0.18)，
        # 这两个指标更稳健。权重和仍=1.0。
        _d_win = max(0, min(100, _win_rate))
        _d_pf = max(0, min(100, min(_pf, 3) / 3 * 100))
        _d_sig = max(0, min(100, _raw / 120 * 100))
        _d_fund = (max(0, min(100, _roe / 15 * 100)) + max(0, min(100, _yoy / 30 * 100))) / 2
        effective_regime = str(res.get('effective_market_regime') or regime_status).upper()
        _d_regime = {"OFFENSIVE": 100, "DEFENSIVE": 50, "UNKNOWN": 50}.get(effective_regime, 0)
        _d_brooks = max(0, min(100, _pa_struct))
        _d_sector = max(0, min(100, _sector_align))
        _d_cap = {"UNKNOWN": 80, "MICRO": 10, "SMALL": 45, "MID": 85, "LARGE": 80, "MEGA": 65}.get(_cap_bucket, 80)

        quality_score = round(
            _d_win * 0.17 + _d_pf * 0.21 + _d_sig * 0.17 + _d_fund * 0.14
            + _d_regime * 0.09 + _d_brooks * 0.10 + _d_sector * 0.08 + _d_cap * 0.04, 1
        )
        # 期望收益惩罚：expectancy < 0 说明策略长期亏钱，扣10分（不论胜率多高）
        if _expectancy < 0:
            quality_score -= 10
            risks.append(f"期望收益为负({_expectancy:+.1f}%)，策略长期可能亏钱")

        # 展示信息（前端用，不再作为分级依据）
        if _win_rate >= 50:
            checks.append(f"胜率{_win_rate:.0f}%")
        if _pf >= 1.5:
            checks.append(f"盈亏比{_pf:.1f}")
        if regime_status == "OFFENSIVE":
            checks.append("大盘进攻")
        elif res.get('market_segment_stage') == "STRUCTURAL_REPAIR":
            checks.append(f"{res.get('market_segment')}结构性强修复")
            bonuses.append("成长板块独立强势")
        if res.get('共振') == "🔥 核心热点":
            bonuses.append("板块核心共振")
        if (res.get('sector_momentum_score') or 0) >= 75:
            bonuses.append("板块强动量")
        elif sector_info.get('trend') == 'LEAD':
            bonuses.append("板块领涨")
        if _roe >= 8:
            bonuses.append(f"ROE{_roe:.0f}%")
        if _yoy >= 15:
            bonuses.append(f"业绩{_yoy:.0f}%")
        if _sector_align >= 75:
            bonuses.append("个股强于板块")
        elif 0 < _sector_align < WEAK_SECTOR_ALIGNMENT:
            quality_score -= 8
            risks.append("弱板块联动胜率偏低")
        elif 0 < _sector_align < MIN_EXECUTION_SECTOR_ALIGNMENT:
            quality_score -= 4
            risks.append("板块联动不足，降低仓位优先级")
        if _strong_sector_core:
            quality_score += 5
            bonuses.append("强板块核心适配")
            res['sector_core_role_candidate'] = True
            res['sector_core_role_label'] = "强板块核心股"
        elif _strong_sector_rear:
            quality_score -= 6
            risks.append("强板块后排角色，等待转强为核心股")
            res['sector_rear_role_watch'] = True
            res['sector_core_role_label'] = "强板块后排观察"
        elif 0 < _sector_strength < WEAK_SECTOR_ALIGNMENT:
            quality_score -= 8
            risks.append("板块强度弱，个股强势不直接交易")
        elif 0 < _sector_strength < MIN_EXECUTION_SECTOR_ALIGNMENT:
            quality_score -= 4
            risks.append("板块强度不足，等待扩散确认")
        if _sector_strength >= MIN_EXECUTION_SECTOR_ALIGNMENT and 0 < _stock_sector_fit < 45:
            risks.append("板块强但个股适配不足，偏补涨观察")
        weekly_context = str(res.get('pa_weekly_context') or "")
        trend_phase = str(res.get('pa_trend_phase') or "")
        if weekly_context in {"周线中性", "周线交易区间"}:
            quality_score -= 5
            risks.append(f"{weekly_context}，等待右侧确认")
        if trend_phase == "震荡观察":
            quality_score -= 4
            risks.append("震荡观察，避免提前重仓")
        setup = str(res.get('pa_trade_setup') or "")
        if _has_pullback_reversal_volume(res):
            quality_score += 5
            bonuses.append("缩量回调后放量反包")
        if _is_h2_second_entry(setup, res) and _h2_execution_confirmed(res, _sector_strength):
            quality_score += 4
            bonuses.append("H2二次入场")
        if _is_sweet_spot_trade_model(res, _sector_align):
            quality_score += 5
            bonuses.append("70-79机会分强联动买点")
            res['sweet_spot_trade_candidate'] = True
            res['sweet_spot_reason'] = "机会分70-79 + 强联动 + TV均线或ZP，按专门买点模型复核"
        if str(res.get('sector_phase') or "") in BARK_PROFILE_SECTOR_PHASE_BONUS:
            quality_score += 4
            bonuses.append("Bark高胜率板块阶段")
            res['bark_success_profile_match'] = True
        if _bark_profile_setup_confirmed(res, setup, _sector_align):
            quality_score += 3
            bonuses.append("Bark高胜率价格结构")
            res['bark_success_profile_match'] = True
        if regime_status == "OFFENSIVE":
            quality_score += 3
            bonuses.append("Bark画像进攻市加分")
        if _cap_bucket in {"MID", "LARGE"}:
            bonuses.append("市值流动性适中")
        elif _cap_bucket == "MEGA":
            risks.append("超大市值需换手确认")
        elif _cap_bucket == "SMALL":
            risks.append("小市值需主线强联动")
        if _is_trend_continuation_candidate(res):
            bonuses.append("主线趋势中继二买")
            res['trend_continuation_candidate'] = True

        res['sop_quality_dimensions'] = {
            "win_rate": _d_win, "profit_factor": _d_pf, "signal_strength": _d_sig,
            "fundamental": _d_fund, "regime": _d_regime, "brooks": _d_brooks, "sector": _d_sector,
            "sector_strength": _sector_strength, "stock_sector_fit": _stock_sector_fit, "market_cap": _d_cap,
        }

        # 5日涨幅渐进惩罚：过度延伸只降低连续质量分，不再映射字母等级。
        grade_pct_5d = float(res.get('pct_5d') or 0)
        _5d_penalty = 0.0
        if grade_pct_5d > SOP_A_GRADE_MAX_5D_GAIN_PCT:
            _over_ext = grade_pct_5d - SOP_A_GRADE_MAX_5D_GAIN_PCT
            _5d_penalty = round(
                min(_over_ext, SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT - SOP_A_GRADE_MAX_5D_GAIN_PCT)
                * SOP_A_GRADE_5D_PENALTY_PER_PCT, 1
            )
            quality_score = round(quality_score - _5d_penalty, 1)
            risks.append(
                f"5日涨幅{grade_pct_5d:.1f}%超{SOP_A_GRADE_MAX_5D_GAIN_PCT:g}%起扣线，质量分扣{_5d_penalty:.1f}"
            )
        if vetoes:
            quality_score -= 15
            risks.append(f"软否决: {', '.join(vetoes[:2])}")
        if soft_vetoes:
            quality_score -= min(8, 4 * len(soft_vetoes))
            risks.append(f"软否决降级(v2): {', '.join(soft_vetoes[:2])}")
        res['sop_soft_vetoes'] = soft_vetoes

        res['sop_quality_score'] = quality_score
        res['sop_vetoes'] = vetoes
        res['sop_checks'] = checks
        res['sop_bonuses'] = bonuses
        res['sop_risks'] = risks
        brooks_adjustment = _brooks_rank_adjustment(res)
        res['brooks_rank_adjustment'] = brooks_adjustment
        res['final_rank_score'] = round(float(res.get('Score') or 0) + brooks_adjustment, 2)
        res['final_rank_score'] = round(res['final_rank_score'] + min(12, max(0, float(res.get('sector_alignment_score') or 0) - 50) * 0.24), 2)
        _rps_120 = float(res.get('rps_120') or 0)
        _rps_sector_120 = float(res.get('rps_sector_120') or 0)
        _rps_acceleration = float(res.get('rps_acceleration') or 0)
        if _rps_120 >= 90:
            res['final_rank_score'] = round(res['final_rank_score'] + 4, 2)
            bonuses.append("全市场RPS120前10%")
        if _rps_sector_120 >= 80:
            res['final_rank_score'] = round(res['final_rank_score'] + 2, 2)
            bonuses.append("板块内RPS120前20%")
        if _rps_acceleration >= 5:
            res['final_rank_score'] = round(res['final_rank_score'] + 1, 2)
            bonuses.append("RPS短周期加速")
        if res.get('trend_continuation_candidate'):
            res['final_rank_score'] = round(res['final_rank_score'] + 6, 2)
        if _has_pullback_reversal_volume(res):
            res['final_rank_score'] = round(res['final_rank_score'] + 5, 2)
        if res.get('h2_second_entry_boost'):
            res['final_rank_score'] = round(res['final_rank_score'] + 4, 2)
        if res.get('sweet_spot_trade_candidate'):
            res['final_rank_score'] = round(res['final_rank_score'] + 5, 2)
        if res.get('bark_success_profile_match'):
            res['final_rank_score'] = round(res['final_rank_score'] + 4, 2)
        if _strong_sector_core:
            res['final_rank_score'] = round(res['final_rank_score'] + 5, 2)
        elif _strong_sector_rear:
            res['final_rank_score'] = round(res['final_rank_score'] - 6, 2)
        elif 0 < _sector_strength < MIN_EXECUTION_SECTOR_ALIGNMENT:
            res['final_rank_score'] = round(res['final_rank_score'] - 5, 2)
        if 0 < _sector_align < WEAK_SECTOR_ALIGNMENT:
            res['final_rank_score'] = round(res['final_rank_score'] - 8, 2)
        elif 0 < _sector_align < MIN_EXECUTION_SECTOR_ALIGNMENT:
            res['final_rank_score'] = round(res['final_rank_score'] - 4, 2)
        if _cap_bucket == "SMALL":
            res['final_rank_score'] = round(res['final_rank_score'] - 5, 2)
        elif _cap_bucket == "MICRO":
            res['final_rank_score'] = round(res['final_rank_score'] - 12, 2)
        if brooks_adjustment >= 6:
            bonuses.append("Brooks结构加分")
        if brooks_adjustment <= -6:
            vetoes.append("Brooks风险偏高")
        if res.get('sequoia_research_shadow_only'):
            res['sop_checks'].append("外部研究形态SHADOW")
            res['sop_bonuses'].append("独立形态研究样本")
        elif res.get('bottom_discovery_watch_only'):
            stage = str(res.get('bottom_discovery_stage') or "B0_BASE")
            res['sop_checks'].append("B1止跌转强" if stage == "B1_REVERSAL" else "B0底部候选")
            res['sop_bonuses'].append("低位起涨研究样本")
        elif res.get('early_watch_only'):
            res['sop_checks'].append("等待TV-ZP确认")
            res['sop_bonuses'].append("早期异动观察")
        elif res.get('revival_watch_only'):
            res['sop_bonuses'].append("历史信号复活")
            level = res.get('revival_level')
            if level == "MOMENTUM_ACCELERATION":
                res['sop_checks'].append("复活动量加速")
                res['sop_bonuses'].append("连续强势加速")
            elif level == "FOLLOW_SMALL":
                res['sop_checks'].append("复活信号可复核")
            elif level == "NEXT_DAY_CONFIRM":
                res['sop_checks'].append("复活信号等次日确认")
            else:
                res['sop_checks'].append("复活信号禁止追涨")
        elif res.get('tv_reversal_watch_only'):
            res['sop_checks'].append("TV-ZP原始long + 日线B确认")
            res['sop_bonuses'].append("暴跌后强修复观察")
        elif res.get('momentum_acceleration_watch_only'):
            res['sop_checks'].append("强趋势加速")
            res['sop_bonuses'].append("涨停/大阳加速观察")
            res['momentum_watch_only'] = True
            res['momentum_watch_reason'] = res.get(
                'momentum_acceleration_reason',
                "强趋势加速，不追买；次日确认后小仓复核",
            )
        elif res.get('sector_watch_only'):
            res['sop_checks'].append("等待TV买点")
            res['sop_bonuses'].append("板块趋势确认观察")
        elif _is_momentum_watch_candidate(res, vetoes):
            res['sop_checks'].append("动量观察")
            res['sop_bonuses'].append("强势动量观察")
            res['momentum_watch_only'] = True
            res['momentum_watch_reason'] = "涨幅/短线涨幅偏高，不追买；保留观察回踩或次日确认"
        res['quality_reason'] = f"连续质量分{quality_score:.1f}；按风险否决、交易桶与价格行为执行"
        _apply_trade_execution_profile(res)


def _early_watch_quality(res: Dict[str, Any]) -> tuple[bool, List[str]]:
    """Quality gate for MA-led early watch candidates under TV strict mode."""
    reasons = []
    if res.get('tv_ma_signal') != 'B共振' or res.get('tv_zp_signal') != '无':
        reasons.append("不是均线领先信号")

    has_brooks = (
        res.get('pa_h2_quality') == '强'
        or bool(res.get('pa_volume_confirmed'))
        or res.get('pa_volume_pattern') == '放量突破'
        or res.get('pa_breakout_quality') == '强突破'
    )
    if not has_brooks:
        reasons.append("Brooks/H2/放量质量不足")

    has_fundamental = (res.get('ROE') or 0) >= 8 or (res.get('净利YOY') or 0) >= 15
    if not has_fundamental:
        reasons.append("缺少基本面加分")

    if (res.get('pa_trade_plan') or {}).get('action') == 'AVOID':
        reasons.append("价格行为建议暂不参与")

    return len(reasons) == 0, reasons


def _build_sector_watch_candidates(
    candidates: pd.DataFrame,
    existing_codes: set,
    hist_map: Dict[str, pd.DataFrame],
    sector_map: Dict[str, str],
    sector_strength: Dict[str, Dict[str, Any]],
    max_per_sector: int = 5,
) -> List[Dict[str, Any]]:
    """Add strong-sector leaders to observation only, even when no TV buy signal fired."""
    if candidates is None or candidates.empty or not sector_strength:
        return []

    df = candidates.copy()
    df['code'] = df['code'].astype(str).str.zfill(6)
    df['industry'] = df['code'].map(sector_map).fillna('未知')
    df['pct_chg'] = pd.to_numeric(df['pct_chg'], errors='coerce').fillna(0)
    df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0)
    df = df[(df['pct_chg'] > 0) & (df['price'] > 0) & (df['industry'] != '未知')]

    watch: List[Dict[str, Any]] = []
    for industry, group in df.groupby('industry'):
        strength = sector_strength.get(industry, {})
        if strength.get('sector_phase') != 'SECTOR_CONFIRM':
            continue
        sector_avg = float(strength.get('sector_avg_pct') or group['pct_chg'].mean() or 0)
        leaders = group[
            group['pct_chg'].le(A_MINUS_TRIAL_MAX_DAILY_RISE_PCT)
        ].sort_values('pct_chg', ascending=False).head(max_per_sector)
        for rank, (_, row) in enumerate(leaders.iterrows(), start=1):
            code = str(row.get('code', '')).zfill(6)
            if code in existing_codes:
                continue
            stock_pct = float(row.get('pct_chg') or 0)
            role = classify_sector_role(stock_pct, sector_avg, rank_in_sector=rank)
            if role not in {'LEADER', 'CORE'}:
                continue

            df_hist = hist_map.get(code)
            if df_hist is None or df_hist.empty:
                continue
            df_hist = df_hist.copy().reset_index(drop=True)
            pa = analyze_price_action(df_hist)
            plan = pa.get('pa_trade_plan') or {}
            if plan.get('action') == 'AVOID':
                continue

            latest = df_hist.iloc[-1]
            current_price = float(row.get('price') or latest.get('收盘') or 0)
            entry_price = float(pa.get('pa_entry_price') or latest.get('最高') or current_price)
            risk = compute_paper_risk_levels(entry_price, entry_price, current_price, pa)
            pct_5d = 0.0
            if len(df_hist) >= 6:
                close_now = float(df_hist['收盘'].iloc[-1])
                close_5d_ago = float(df_hist['收盘'].iloc[-6])
                if close_5d_ago > 0:
                    pct_5d = round((close_now - close_5d_ago) / close_5d_ago * 100, 2)
            if pct_5d > SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT:
                continue

            item: Dict[str, Any] = {
                '代码': code,
                '名称': str(row.get('name') or code),
                '行业': industry,
                '现价': round(current_price, 2),
                '涨幅%': round(stock_pct, 2),
                'Score': 58.0,
                'RSI': round(float(latest.get('RSI') or 0), 1),
                'DIF': round(float(latest.get('DIF') or 0), 3),
                'BB': round(float(latest.get('BB') or 0), 4),
                '粘合度': round(float(latest.get('粘合度') or 0), 4),
                '历史胜率': '0%',
                '信号次数': 0,
                '北向': '---',
                '共振': '板块强势观察',
                '影线比': 0,
                'strategy_type': 'sector_watch',
                'signal': '板块强势观察',
                'reason': f'{industry}趋势确认，{role}进入观察；等待TV买点或回踩确认',
                'sector_watch_only': True,
                'sector_watch_reason': f'{industry}趋势确认，个股为{role}，但TV双策略买点未触发',
                'tv_ma_signal': '未触发',
                'tv_zp_signal': '未触发',
                'tv_match': '板块观察',
                'pct_5d': pct_5d,
                'entry_price': round(entry_price, 2),
                'stop_price': risk['active_stop_price'],
                'plan_stop_price': risk['active_stop_price'],
                'initial_stop_price': risk['initial_stop_price'],
                'structure_stop_price': risk['structure_stop_price'],
                'target_price': risk['take_profit_price'],
                'risk_reward': risk['risk_reward'],
                'risk_notes': risk['risk_notes'],
            }
            item.update(pa)
            watch.append(item)
            existing_codes.add(code)

    return watch


def single_stock_task(code, name, price, vol, open_price, threshold, vol_multiplier, rsi_min, use_macd_filter, use_bb_sqz, sqz_lookback, use_weekly, use_rs_filter=True, local_only=False, engine=None, preloaded_df=None, target_date=None, bench_df=None, strategy_type="squeeze", pine_min_signals=3, min_data_days=None, weekly_ma_period=20, fund_data=None, tv_weekly_gate=False):
    # Use provided target_date or default to now
    if target_date is None or target_date == "":
        target_date = datetime.now()
    elif isinstance(target_date, str):
        target_date = datetime.strptime(target_date, "%Y-%m-%d")

    # 优先使用预加载的数据
    if preloaded_df is not None and not preloaded_df.empty:
        df = preloaded_df.copy().reset_index(drop=True)
    else:
        df = load_from_db(code, (target_date - timedelta(days=360)).strftime("%Y-%m-%d"), engine)

    if df.empty:
        return {"reason": "数据库无此股票历史数据"}

    # 统一日期格式为字符串，确保计算和合并的一致性
    if pd.api.types.is_datetime64_any_dtype(df['日期']):
        df['日期'] = df['日期'].dt.strftime('%Y-%m-%d')
    else:
        df['日期'] = df['日期'].astype(str).str[:10]

    # 根据策略类型设置最小数据要求
    if min_data_days is None:
        if strategy_type in {"pine", "tv_zp", "tv_dual", "tv_dual_strict"}:
            min_days = 120
        elif strategy_type == "uptrend_limit_down":
            min_days = 80
        elif strategy_type == "rps_breakout":
            min_days = 125
        elif strategy_type == "trader_vic_2b":
            min_days = 220
        elif strategy_type in {
            "early_value", "bottom_discovery", "high_tight_flag",
            "turtle_breakout", "limit_up_shakeout",
        }:
            min_days = 80
        elif strategy_type == "consensus":
            min_days = 130 # 需要 60 周或足够长的日线来模拟
        else: # squeeze or both
            min_days = 120
    else:
        min_days = min_data_days

    if len(df) < min_days:
        return {"reason": f"样本不足({len(df)})"}

    if len(df) >= 2:
        try:
            latest_close = float(df['收盘'].iloc[-1])
            prev_close = float(df['收盘'].iloc[-2])
            if prev_close > 0:
                daily_pct = (latest_close - prev_close) / prev_close * 100
                if _is_abnormal_price_move(code, daily_pct):
                    return {"reason": f"异常价格跳变({daily_pct:.2f}%)"}
        except (TypeError, ValueError, KeyError):
            pass

    try:
        # 技术指标计算 - 预加载数据可能已有通用指标，但仍缺少 Pine 专属指标。
        enable_pine = (strategy_type in ["pine", "both", "tv_zp", "tv_dual", "tv_dual_strict"])
        if 'RSI' not in df.columns:
            df = calculate_indicators(df, current_price=price, current_vol=vol, current_open=open_price, bench_df=bench_df, enable_pine_indicators=enable_pine)

        pine_cols = {'RF_Upward', 'ST_Signal', 'RQK_Up', 'HalfTrend_Up', 'QQE_Long'}
        if enable_pine and not pine_cols.issubset(df.columns):
            df = calculate_pine_indicators(df)

        # 根据策略类型选择不同的筛选逻辑
        if strategy_type == "pine":
            # Pine Script 多指标共振策略
            match, stats = check_pine_strategy(df, min_signals=pine_min_signals, fund_data=fund_data)
            if match:
                stats['代码'] = code
                stats['名称'] = name
                stats['strategy_type'] = "pine"
                return stats
            else:
                return stats
        elif strategy_type == "tv_zp":
            match, stats = check_tv_zp_strategy(df, fund_data=fund_data)
            if match:
                stats['代码'] = code
                stats['名称'] = name
                stats['strategy_type'] = "tv_zp"
                return stats
            else:
                return stats
        elif strategy_type in {"tv_dual", "tv_dual_strict"}:
            # 可选周线对齐门槛（默认关闭）。开启后，周线波段未走强（MA 未向上 + EMA10w≤EMA30w）
            # 的候选直接过滤，避免逆周线下跌结构买入。复用既有 get_weekly_indicators（日→周重采样）。
            if tv_weekly_gate:
                if not get_weekly_indicators(code, df=df, local_only=local_only, weekly_ma_period=weekly_ma_period):
                    return {"reason": "周线波段未走强（tv_dual 门槛）"}
            match, stats = check_tv_dual_strategy(
                df,
                threshold=threshold,
                vol_multiplier=vol_multiplier,
                rsi_min=rsi_min,
                use_macd_filter=use_macd_filter,
                sqz_lookback=sqz_lookback,
                require_both=(strategy_type == "tv_dual_strict"),
                fund_data=fund_data,
            )
            if match:
                stats['代码'] = code
                stats['名称'] = name
                stats['strategy_type'] = strategy_type
                return stats
            if strategy_type == "tv_dual_strict" and stats.get("tv_zp_raw_current"):
                watch_match, watch_stats = check_tv_reversal_watch(
                    df,
                    threshold=threshold,
                    vol_multiplier=vol_multiplier,
                    rsi_min=rsi_min,
                    use_macd_filter=use_macd_filter,
                    sqz_lookback=sqz_lookback,
                )
                if watch_match:
                    watch_stats['代码'] = code
                    watch_stats['名称'] = name
                    watch_stats['strategy_type'] = "tv_reversal_watch"
                    return watch_stats
            return stats
        elif strategy_type == "both":
            # 同时满足：均线粘合 + Pine Script 共振
            match_sqz, stats_sqz = check_strategy(
                df, threshold=threshold, vol_multiplier=vol_multiplier, rsi_min=rsi_min,
                use_macd_filter=use_macd_filter, use_bb_sqz=use_bb_sqz,
                sqz_lookback=sqz_lookback, use_rs_filter=use_rs_filter, fund_data=fund_data
            )
            if not match_sqz:
                return stats_sqz

            match_pine, stats_pine = check_pine_strategy(df, min_signals=pine_min_signals, fund_data=fund_data)
            if not match_pine:
                return stats_pine

            # 两者都满足，合并结果
            # 周线趋势过滤
            is_w_ok = True
            if use_weekly:
                is_w_ok = get_weekly_indicators(code, df=df, local_only=local_only, weekly_ma_period=weekly_ma_period)
                if not is_w_ok:
                    return {"reason": "周线波段未走强"}

            combined_stats = stats_pine.copy()
            combined_stats.update(stats_sqz)
            # 分数取平均
            combined_stats['Score'] = (stats_pine['Score'] + stats_sqz['Score']) / 2
            combined_stats['代码'] = code
            combined_stats['名称'] = name
            combined_stats['strategy_type'] = "both"
            combined_stats['reason'] = "双重策略共振"
            return combined_stats
        elif strategy_type == "consensus":
            # Azul "共识" 策略
            is_w_ok = True
            if use_weekly:
                is_w_ok = get_weekly_indicators(code, df=df, local_only=local_only, weekly_ma_period=weekly_ma_period)

            match, stats = check_consensus_strategy(df, is_weekly_ok=is_w_ok, vol_multiplier=vol_multiplier, fund_data=fund_data)
            if match:
                stats['代码'] = code
                stats['名称'] = name
                stats['strategy_type'] = "consensus"
                return stats
            else:
                return stats
        elif strategy_type == "early_value":
            match, stats = _check_early_value_strategy(df, code, name, price)
            return stats
        elif strategy_type == "bottom_discovery":
            match, stats = _check_bottom_discovery_strategy(
                df, code, name, price, current_vol=vol, current_open=open_price,
            )
            return stats
        elif strategy_type == "weekly_four_patterns":
            context = build_completed_timeframe_context(df)
            signals = context["pa_weekly_pattern_signals"]
            if not signals:
                return {"reason": "已完成周线未命中四形态"}
            return {
                **context,
                "代码": code,
                "名称": name,
                "现价": float(df["收盘"].iloc[-1]),
                "Score": 0,
                "strategy_type": "weekly_four_patterns",
                "weekly_pattern_watch_only": True,
                "reason": "、".join(signals) + "（仅观察）",
            }
        elif strategy_type == "h2":
            pa = analyze_price_action(df)
            h2_state = pa.get("pa_h2_state")
            legacy_h2 = pa.get("price_action_pattern") == "H2二次入场" or "H2" in (pa.get("pa_tags") or [])
            if (h2_state and h2_state != "H2_TRIGGERED") or (not h2_state and not legacy_h2):
                return {"reason": "未形成H2二次入场"}
            current = float(df["收盘"].iloc[-1])
            previous = float(df["收盘"].iloc[-2])
            plan_action = str((pa.get("pa_trade_plan") or {}).get("action") or "WATCH")
            return {
                **pa,
                "代码": code,
                "名称": name,
                "现价": round(current, 2),
                "涨幅%": round((current / previous - 1) * 100, 2) if previous > 0 else 0.0,
                "Score": float(pa.get("price_action_score") or 0),
                "raw_score": float(pa.get("price_action_score") or 0),
                "strategy_type": "h2",
                "signal": "H2二次入场",
                "reason": pa.get("price_action_summary") or "多头趋势双腿回调后向上突破",
                "结构": "H2二次入场",
                "h2_watch_only": plan_action != "READY",
            }
        elif strategy_type in {
            "high_tight_flag", "turtle_breakout", "limit_up_shakeout",
            "ma_volume", "uptrend_limit_down", "rps_breakout", "trader_vic_2b",
        }:
            match, stats = _check_sequoia_research_strategy(df, code, name, strategy_type)
            return stats
        else:
            # 默认均线粘合策略
            match, stats = check_strategy(
                df,
                threshold=threshold,
                vol_multiplier=vol_multiplier,
                rsi_min=rsi_min,
                use_macd_filter=use_macd_filter,
                use_bb_sqz=use_bb_sqz,
                sqz_lookback=sqz_lookback,
                use_rs_filter=use_rs_filter,
                fund_data=fund_data
            )

            if match:
                # 周线趋势过滤
                if use_weekly:
                    if not get_weekly_indicators(code, df=df, local_only=local_only, weekly_ma_period=weekly_ma_period):
                        return {"reason": "周线波段未走强"}

                stats['代码'] = code
                stats['名称'] = name
                stats['strategy_type'] = "squeeze"
                return stats
            else:
                return stats
    except Exception as e:
        logger.error(f"[{code}] 分析异常: {str(e)}")
        return {"reason": "策略计算异常"}


@dataclass
class _MarketScanContext:
    """perform_market_scan 机械拆分后的跨阶段显式上下文。

    原实现通过函数内闭包捕获共享状态；拆成 `_scan_*` 阶段函数后，所有跨阶段
    变量统一收敛到该 dataclass，由每个阶段函数显式接收，并按原执行位置写回，
    保持行为与计时打点完全等价。
    """
    # ---- 扫描入参（只读；除 adaptive 八参外原样传递） ----
    strategy_type: str
    market_range: str
    use_macd_filter: bool
    use_weekly: bool
    use_rs_filter: bool
    local_only: bool
    data_date: Optional[str]
    min_data_days: Optional[int]
    weekly_ma_period: int
    tv_weekly_gate: bool
    require_live_snapshot: bool
    mkt_cap_min: float
    scan_context: Optional[Dict[str, Any]]
    publish_to_sentinel: bool
    # ---- 大盘自适应改参目标（_scan_prepare_environment 内可能被写回） ----
    threshold: float
    vol_multiplier: float
    rsi_min: int
    use_bb_sqz: bool
    sqz_lookback: int
    pine_min_signals: int
    stop_loss_pct: float
    turnover_min: float
    # ---- 审计与计时 ----
    audit_payload: Dict[str, Any]
    phase_timings: Dict[str, float]
    scan_started_at: datetime
    # ---- 阶段产物（默认值仅为拆分占位，正常流程由各阶段按原位置写回） ----
    max_date: Any = None
    engine: Any = None
    snapshot_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    data_mode: str = "LIVE_SNAPSHOT"
    snapshot_as_of: Any = None
    resolved_data_date: str = ""
    discovery_pool: str = ""
    candidates: pd.DataFrame = field(default_factory=pd.DataFrame)
    start_time: Optional[float] = None
    bench_slice: Optional[pd.DataFrame] = None
    hist_map: Dict[str, pd.DataFrame] = field(default_factory=dict)
    fund_map: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    money_flow_map: Dict[str, Any] = field(default_factory=dict)
    results: List[Dict[str, Any]] = field(default_factory=list)
    rps_map: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    sector_map: Dict[str, str] = field(default_factory=dict)
    sector_trends: Dict[str, Any] = field(default_factory=dict)
    sector_strength: Dict[str, Any] = field(default_factory=dict)
    market_regime: Dict[str, Any] = field(default_factory=dict)
    monthly_sector_context: Dict[str, Any] = field(default_factory=dict)
    active_plan_map: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _scan_prepare_environment(ctx: _MarketScanContext, mark_phase: Callable[[str], None]) -> bool:
    """阶段 1/6：大盘环境取参与自适应改参、数据预检熔断、快照获取与本地装载。

    对应原 perform_market_scan 中 mark_phase("market_snapshot_load") 之前的段落。
    返回 True 表示数据预检熔断且非实时模式，调用方应立即返回空结果。
    """
    data_date = ctx.data_date
    strategy_type = ctx.strategy_type
    local_only = ctx.local_only
    require_live_snapshot = ctx.require_live_snapshot
    scan_context = ctx.scan_context
    audit_payload = ctx.audit_payload
    max_date = ctx.max_date
    threshold = ctx.threshold
    vol_multiplier = ctx.vol_multiplier
    rsi_min = ctx.rsi_min
    use_bb_sqz = ctx.use_bb_sqz
    sqz_lookback = ctx.sqz_lookback
    pine_min_signals = ctx.pine_min_signals
    stop_loss_pct = ctx.stop_loss_pct
    turnover_min = ctx.turnover_min

    # 获取大盘环境以动态调整参数。
    # 回放扫描（data_date=历史日）时拿到的 get_market_regime() 是"今天"的
    # 实时状态——用它改写历史扫描的阈值会把未来信息泄进回放，校准失真。
    # 因此回放模式下跳过自适应改参（冻结传入参数），只保留实时扫描行为。
    regime = get_market_regime()
    reg_status = regime.get("status", "UNKNOWN")

    # 启用 REGIME_PARAMS 自适应阈值（改动 #7）：根据大盘状态自动收紧/放宽
    # threshold、vol_multiplier、rsi_min、stop_loss_pct 等。bear 时最严，bull 时最松。
    # 用 SCAN_REGIME_ADAPTIVE 开关控制，便于回退到旧的固定参数。
    if SCAN_REGIME_ADAPTIVE and not data_date:
        from core.market_regime import get_adaptive_params
        adaptive = get_adaptive_params(reg_status, strategy_type)
        threshold = float(adaptive.get("threshold", threshold))
        vol_multiplier = float(adaptive.get("vol_multiplier", vol_multiplier))
        if "rsi_min" in adaptive:
            rsi_min = int(adaptive["rsi_min"])
        if "stop_loss_pct" in adaptive:
            stop_loss_pct = float(adaptive["stop_loss_pct"])
        if "sqz_lookback" in adaptive:
            sqz_lookback = int(adaptive["sqz_lookback"])
        if adaptive.get("use_bb_sqz") is not None:
            use_bb_sqz = bool(adaptive["use_bb_sqz"])
        if "pine_min_signals" in adaptive:
            pine_min_signals = int(adaptive["pine_min_signals"])
        logger.info(
            f"[SCAN] Market regime={reg_status}. Adaptive params: threshold={threshold}, "
            f"vol_mult={vol_multiplier}, rsi_min={rsi_min}, stop_loss={stop_loss_pct}."
        )
    # 换手率调整保留：进攻市适度放宽换手要求（同样只在实时扫描生效，回放冻结）
    if reg_status == "OFFENSIVE" and not data_date:
        turnover_min = max(2.5, turnover_min - 0.5)
        logger.info(f"[SCAN] Market is OFFENSIVE. Adjusting turnover requirement to {turnover_min}.")
    ctx.threshold = threshold
    ctx.vol_multiplier = vol_multiplier
    ctx.rsi_min = rsi_min
    ctx.stop_loss_pct = stop_loss_pct
    ctx.sqz_lookback = sqz_lookback
    ctx.use_bb_sqz = use_bb_sqz
    ctx.pine_min_signals = pine_min_signals
    ctx.turnover_min = turnover_min

    ctx.snapshot_df = snapshot_df = pd.DataFrame()
    ctx.engine = engine = get_db_engine()

    # 数据预检熔断：若数据质量 blocking，中止扫描，避免坏/陈旧数据静默产生假信号。
    # （此前 preflight 仅作为 GET 接口暴露，扫描器从不检查。）
    if SCAN_PREFLIGHT_ENFORCE and engine is not None:
        try:
            preflight = build_scan_preflight(engine, data_date=data_date)
            if preflight.get("blocking"):
                block_msgs = [
                    c.get("message", c.get("name", ""))
                    for c in preflight.get("checks", [])
                    if c.get("status") == "error"
                ]
                logger.warning(
                    f"[RUN_MARKET_SCAN] 扫描被数据预检熔断中止："
                    f"{'; '.join(block_msgs) or '存在 error 级检查项'}"
                )
                if require_live_snapshot:
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "实时扫描已中止：数据预检未通过。"
                            f"{'; '.join(block_msgs) or '存在数据完整性问题'}"
                        ),
                    )
                return True
        except HTTPException:
            raise
        except Exception as exc:
            # 预检本身失败不应阻断扫描（降级为告警，保持可用性）
            logger.warning(f"[RUN_MARKET_SCAN] 数据预检执行异常，跳过熔断：{exc}")

    # 1. 如果需要实时行情，或不是强制本地，尝试联网获取快照。
    # 午间/Bark 扫描会传 local_only=True + require_live_snapshot=True；
    # 这种组合必须主动拉实时快照，否则会直接因 snapshot_df 为空而熔断。
    if (require_live_snapshot or not local_only) and data_date is None:
        try:
            ctx.snapshot_df = snapshot_df = _load_market_snapshot(force_refresh=require_live_snapshot)
        except Exception:
            logger.debug("Network snapshot failed.")

    # 2. 如果数据为空（联网失败 或 强制本地），启用本地数据库兜底
    if require_live_snapshot and is_snapshot_stale(snapshot_df):
        raise HTTPException(
            status_code=503,
            detail="实时行情快照已过期，已中止扫描，避免 Bark 使用过时行情数据。",
        )
    if snapshot_df.empty:
        if require_live_snapshot:
            raise HTTPException(
                status_code=503,
                detail="实时行情快照不可用，已中止扫描，避免 Bark 使用历史 daily_k 数据。",
            )
        logger.info(f"Switching to LOCAL DB mode (Local Only: {local_only}, Data Date: {data_date or 'Auto'})...")
        try:
            with engine.connect() as conn:
                # 如果指定了日期，使用指定日期；否则查找有足够数据的最近日期
                if data_date:
                    # 验证日期格式和存在性
                    date_check = conn.execute(
                        text("SELECT date, COUNT(DISTINCT code) as stock_count FROM daily_k WHERE date = :date GROUP BY date"),
                        {"date": data_date}
                    ).fetchone()
                    if not date_check:
                        raise HTTPException(
                            status_code=400,
                            detail=f"指定日期 {data_date} 没有数据或格式不正确。请使用 YYYY-MM-DD 格式。"
                        )
                    ctx.max_date = max_date = data_date
                    stock_count = date_check[1]
                    logger.info(f"Using specified date: {max_date} ({stock_count} stocks)")
                else:
                    # 查找有足够数据的最近日期（至少 1000 只股票）
                    logger.debug("Querying DB for best available date...")
                    best_date_query = text("""
                        SELECT date, COUNT(DISTINCT code) as stock_count
                        FROM daily_k
                        GROUP BY date
                        HAVING COUNT(DISTINCT code) >= 1000
                        ORDER BY date DESC
                        LIMIT 1
                    """)
                    best_date_res = conn.execute(best_date_query).fetchone()
                    if best_date_res and best_date_res[0]:
                        ctx.max_date = max_date = best_date_res[0]
                        stock_count = best_date_res[1]
                        logger.info(f"Found best date in DB: {max_date} ({stock_count} stocks)")
                    else:
                        raise HTTPException(status_code=503, detail="数据库中没有足够的数据进行扫描")

                # 使用参数化查询防止 SQL 注入
                # pct_chg 使用与前一日收盘价对比 (日涨幅)，而非日内 open→close
                # 使用 LAG 窗口函数高效获取前日收盘价
                # 注意: 避免 :: 类型转换语法，SQLAlchemy 会将 :: 误解析为命名参数
                query = text("""
                    WITH ranked AS (
                        SELECT code, date, close, open, high, low, vol,
                               LAG(close) OVER (PARTITION BY code ORDER BY date) as prev_close
                        FROM daily_k
                        WHERE date <= CAST(:max_date AS date)
                          AND date >= CAST(CAST(:max_date AS date) - interval '7 days' AS date)
                    )
                    SELECT r.code, b.name, b.industry, r.close as price, r.open, r.high, r.low, r.vol,
                           CASE WHEN r.prev_close > 0
                               THEN ROUND(CAST((r.close - r.prev_close) / r.prev_close * 100 AS numeric), 2)
                               ELSE 0
                           END as pct_chg,
                           NULL as turnover,
                           NULL as mkt_cap
                    FROM ranked r
                    LEFT JOIN stock_basic b ON r.code = b.code
                    WHERE r.date = CAST(:max_date AS date)
                """)
                ctx.snapshot_df = snapshot_df = pd.read_sql(query, engine, params={"max_date": max_date})
                logger.info(f"Loaded {len(snapshot_df)} rows from DB fallback.")
                # 保存数据日期信息用于返回
                if hasattr(snapshot_df, 'attrs'):
                    snapshot_df.attrs['data_date'] = max_date
                # Fallback for name if join failed
                if not snapshot_df.empty:
                    snapshot_df['name'] = snapshot_df['name'].fillna(snapshot_df['code'])
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Local fallback error: {e}")
            raise HTTPException(status_code=500, detail=f"加载数据失败: {str(e)}")

    if snapshot_df.empty:
        detail_msg = "无法获取市场数据。"
        if local_only:
            detail_msg += "【离线模式】已开启，但本地数据库尚未同步今日数据。请先执行【数据管理 -> 同步当日数据】。"
        else:
            detail_msg += "联网请求超时且本地无缓存数据，请检查网络或刷新后再试。"
        raise HTTPException(status_code=503, detail=detail_msg)
    ctx.data_mode = data_mode = "LOCAL_DB" if max_date else "LIVE_SNAPSHOT"
    snapshot_attrs = getattr(snapshot_df, "attrs", {}) or {}
    ctx.snapshot_as_of = snapshot_as_of = (
        max_date or snapshot_attrs.get("data_date")
        if data_mode == "LOCAL_DB"
        else snapshot_attrs.get("fetched_at") or datetime.now()
    )
    ctx.resolved_data_date = resolved_data_date = str(
        max_date or snapshot_attrs.get("data_date") or datetime.now().strftime("%Y-%m-%d")
    )[:10]
    if scan_context is not None:
        scan_context.update({
            "data_date": resolved_data_date,
            "data_mode": data_mode,
            "as_of": snapshot_as_of,
        })
    audit_payload.update(_build_snapshot_audit(snapshot_df, data_mode, snapshot_as_of))
    dataset_version = f"{data_mode}:{str(snapshot_as_of)[:19]}"
    audit_payload["version_snapshot"]["dataset_version"] = dataset_version
    audit_payload["point_in_time_snapshot_count"] = save_point_in_time_snapshot(
        snapshot_df, dataset_version, snapshot_as_of, data_mode, engine,
    )
    audit_payload["params_snapshot"]["point_in_time_snapshot_count"] = audit_payload["point_in_time_snapshot_count"]
    audit_payload["params_snapshot"]["evidence_pipeline"] = {
        "stage": "MARKET_DATA_READY", "mode": EVIDENCE_GATE_MODE,
    }
    if audit_payload["point_in_time_snapshot_count"] <= 0:
        audit_payload["research_only"] = True
        audit_payload.setdefault("degradation_reasons", []).append("点时快照持久化失败")
    mark_phase("market_snapshot_load")

    return False


def _scan_filter_candidates(ctx: _MarketScanContext, mark_phase: Callable[[str], None]) -> None:
    """阶段 2/6：SOP 初始过滤（主板/创业板/科创板、剔除 ST/退市）、discovery pool
    判定、疑似调整缺口隔离与市场范围（科创板/指数成分）过滤，并广播 scan_start。"""
    snapshot_df = ctx.snapshot_df
    audit_payload = ctx.audit_payload
    engine = ctx.engine
    strategy_type = ctx.strategy_type
    turnover_min = ctx.turnover_min
    mkt_cap_min = ctx.mkt_cap_min
    data_date = ctx.data_date
    market_range = ctx.market_range

    # 初始过滤 (核心优化：只分析当日上涨且满足换手率/市值要求的股票)
    total_snapshot = len(snapshot_df)
    audit_payload["total_snapshot"] = total_snapshot

    # SOP: 仅保留 沪深主板(60, 00)、创业板(30)、科创板(688)；剔除 ST、退市整理
    snapshot_df['code_str'] = snapshot_df['code'].astype(str)
    snapshot_df['name_str'] = snapshot_df['name'].astype(str)

    is_target_market = snapshot_df['code_str'].str.startswith(('60', '688', '00', '30'))
    is_not_st = ~snapshot_df['name_str'].str.contains('ST|退', case=False)

    # fallback 模式下 turnover/mkt_cap 可能为 NULL（本地DB无此数据），需特殊处理
    has_turnover = snapshot_df['turnover'].notna()
    has_mkt_cap = snapshot_df['mkt_cap'].notna()
    has_core_quote = snapshot_df['price'].notna() & snapshot_df['pct_chg'].notna()

    discovery_mask, discovery_pool = _discovery_pool_mask(snapshot_df, strategy_type)
    ctx.discovery_pool = discovery_pool
    audit_payload["effective_filters"] = [
        item for item in audit_payload.get("effective_filters", [])
        if item != "positive_pct_change"
    ] + [f"discovery_pool:{discovery_pool}"]
    turnover_filter = (
        has_turnover & (snapshot_df['turnover'] >= turnover_min)
        if "turnover_min" in audit_payload.get("effective_filters", [])
        else pd.Series(True, index=snapshot_df.index)
    )
    mkt_cap_filter = (
        has_mkt_cap & (snapshot_df['mkt_cap'] >= mkt_cap_min * 100000000)
        if "market_cap_min" in audit_payload.get("effective_filters", [])
        else pd.Series(True, index=snapshot_df.index)
    )
    candidates = snapshot_df[
        discovery_mask &
        is_target_market & is_not_st & has_core_quote &
        mkt_cap_filter & turnover_filter
    ].copy()
    candidates["discovery_pool"] = discovery_pool
    audit_payload["params_snapshot"]["discovery_pool"] = discovery_pool
    adjustment_gap_codes = get_suspected_adjustment_gap_codes(engine, target_date=data_date)
    if adjustment_gap_codes:
        candidates = candidates[~candidates["code"].astype(str).isin(adjustment_gap_codes)]
        audit_payload.setdefault("fail_reasons", {})["suspected_adjustment_gap"] = len(adjustment_gap_codes)
        logger.warning(f"Quarantined {len(adjustment_gap_codes)} suspected adjustment-gap candidates.")
    audit_payload["candidate_count"] = len(candidates)
    mark_phase("candidate_filter")

    logger.info(f"Snapshot: {total_snapshot} stocks")
    logger.info(f"After SOP Filter (No ST/BJ/Delist, +%, TO>{turnover_min}%, MC>{mkt_cap_min}亿): {len(candidates)} candidates")

    # 1. 处理科创板过滤
    if "包含科创板" not in market_range:
        candidates = candidates[~candidates['code'].astype(str).str.startswith('688')]

    # 2. 处理成分股精确过滤
    index_map = {
        "沪深300": "000300",
        "上证50": "000016",
        "中证500": "000905",
        "中证1000": "000852"
    }

    target_index = None
    for key, val in index_map.items():
        if key in market_range:
            target_index = val
            break

    if target_index:
        try:
            import akshare as ak
            cons_df = ak.index_stock_cons(symbol=target_index)
            if not cons_df.empty:
                cons_codes = cons_df['品种代码'].tolist()
                candidates = candidates[candidates['code'].isin(cons_codes)]
        except Exception as e:
            logger.warning(f"{market_range} filter failed: {e}")

    # 无数量上限，用户可按需调整筛选条件
    logger.info(f"准备扫描 {len(candidates)} 只股票...")
        
    ws_manager.broadcast_threadsafe({
        "type": "scan_start",
        "message": f"准备扫描 {len(candidates)} 只股票..."
    })
    ctx.candidates = candidates


def _scan_load_data(ctx: _MarketScanContext, mark_phase: Callable[[str], None]) -> bool:
    """阶段 3/6：预拉取基准指数、批量装载候选历史 K 线、注入实时快照行、
    流动性/次新股过滤与向量化指标计算，产出 hist_map。

    返回 True 表示过滤后无候选（审计已落库），调用方应立即返回空结果。
    """
    data_date = ctx.data_date
    strategy_type = ctx.strategy_type
    snapshot_df = ctx.snapshot_df
    audit_payload = ctx.audit_payload
    resolved_data_date = ctx.resolved_data_date
    scan_started_at = ctx.scan_started_at

    results = []
    ctx.engine = engine = get_db_engine()

    # 核心优化：预拉取指数历史并过滤，避免在线程内重复查询和过滤
    bench_df = get_index_hist("000001")
    ctx.bench_slice = bench_slice = None
    if not bench_df.empty:
        # 预先过滤出需要的日期范围
        hist_end = datetime.now() if not data_date else datetime.strptime(data_date, "%Y-%m-%d")
        hist_start = hist_end - timedelta(days=365)
        bench_df = bench_df.copy()
        bench_df['日期'] = pd.to_datetime(bench_df['日期'], errors='coerce')
        mask = (bench_df['日期'] >= hist_start) & (bench_df['日期'] <= hist_end)
        ctx.bench_slice = bench_slice = bench_df.loc[mask, ['日期', '收盘']].copy()
        logger.info(f"Pre-filtered benchmark data: {len(bench_slice)} points.")

    # 核心优化：批量拉取所有候选标的的历史数据，并进行向量化指标计算
    logger.info(f"Pre-loading historical data for {len(candidates)} candidates in batch...")
    ctx.start_time = start_time = time.time()
    end_date_hist = datetime.now().strftime("%Y-%m-%d") if not data_date else data_date
    # 图表和扫描共用同一 TV 预热窗口；Alternate Signal 对历史起点敏感。
    start_date_hist = (
        datetime.strptime(end_date_hist, "%Y-%m-%d")
        - timedelta(days=TV_SIGNAL_WARMUP_DAYS)
    ).strftime("%Y-%m-%d")
    candidate_codes = candidates['code'].tolist()

    dfs = []
    try:
        chunk_size = 1000
        for i in range(0, len(candidate_codes), chunk_size):
            chunk = candidate_codes[i:i + chunk_size]
            placeholders = ", ".join([f":code_{j}" for j in range(len(chunk))])
            query_params = {f"code_{j}": c for j, c in enumerate(chunk)}
            query_params["start_date"] = start_date_hist
            query_params["end_date"] = end_date_hist

            query = text(f"""
                SELECT d.code, d.date as "日期", d.open as "开盘", d.high as "最高",
                       d.low as "最低", d.close as "收盘", d.vol as "成交量",
                       b.name
                FROM daily_k d
                LEFT JOIN stock_basic b ON d.code = b.code
                WHERE d.code IN ({placeholders}) AND d.date >= :start_date AND d.date <= :end_date
                ORDER BY d.code, d.date ASC
            """)
            with engine.connect() as conn:
                chunk_df = pd.read_sql(query, conn, params=query_params)
                if not chunk_df.empty:
                    dfs.append(chunk_df)

        if not dfs:
            logger.error("No historical data found for candidates.")
            raise HTTPException(status_code=404, detail="本地历史数据缺失，请先同步数据。")

        master_df = pd.concat(dfs).reset_index(drop=True)

        # --- 注入实盘快照数据 ---
        # snapshot_df 包含了我们要筛选的标的的实时数据
        # 如果是本地历史回测 (local_only 且 snapshot 从 db fallback 加载)，master_df 已经包含该日数据，不可重复添加
        # 我们通过判断 snapshot 的日期是否大于 master_df 中的最大日期来决定是否追加
        snapshot_date = getattr(snapshot_df, 'attrs', {}).get('data_date', datetime.now().strftime("%Y-%m-%d"))
        # 确保 snapshot_date 是字符串格式
        if isinstance(snapshot_date, date):
            snapshot_date = snapshot_date.strftime("%Y-%m-%d")
        else:
            snapshot_date = str(snapshot_date)[:10]

        db_max_date = master_df['日期'].max()
        if isinstance(db_max_date, pd.Timestamp):
            db_max_date = db_max_date.strftime("%Y-%m-%d")
        else:
            db_max_date = str(db_max_date)[:10]

        if snapshot_date > db_max_date:
            logger.info(f"Appending snapshot live data ({snapshot_date}) to historical database series ({db_max_date})...")
            snap_to_append = snapshot_df[snapshot_df['code'].isin(master_df['code'].unique())].copy()
            snap_to_append = snap_to_append.rename(columns={
                'price': '收盘',
                'open': '开盘',
                'high': '最高',
                'low': '最低',
                'vol': '成交量'
            })
            snap_to_append['日期'] = snapshot_date
            master_df = pd.concat([master_df, snap_to_append], ignore_index=True)
            # 重新排序并重置索引，确保 batch calculation 的索引对齐逻辑正常工作
            master_df = master_df.sort_values(['code', '日期']).reset_index(drop=True)

        candidates, universe_filter_stats = _apply_liquidity_and_new_stock_filters(
            candidates,
            master_df,
        )
        ctx.candidates = candidates
        allowed_codes = set(candidates["code"].astype(str))
        master_df = master_df[master_df["code"].astype(str).isin(allowed_codes)].copy()
        audit_payload["candidate_count"] = len(candidates)
        audit_payload["params_snapshot"]["universe_filters"] = {
            "avg_amount_lookback_days": UNIVERSE_LIQUIDITY_LOOKBACK_DAYS,
            "min_avg_amount_yuan": UNIVERSE_MIN_AVG_AMOUNT_YUAN,
            "amount_estimation": "close_x_volume_hands_x_100_when_exact_missing",
            **universe_filter_stats,
        }
        audit_payload["effective_filters"] = list(dict.fromkeys([
            *audit_payload.get("effective_filters", []),
            "avg_amount_5d",
            "exclude_new_one_price_stock",
        ]))
        if candidates.empty:
            logger.info("No candidates remain after five-day liquidity and new-stock filters.")
            audit_payload.update({
                "scan_date": resolved_data_date,
                "finished_at": datetime.now(),
                "duration_sec": round((datetime.now() - scan_started_at).total_seconds(), 2),
                "result_count": 0,
            })
            save_scan_audit_log(audit_payload, engine)
            return True

        logger.info(f"Master dataframe loaded: {len(master_df)} rows. Calculating indicators...")

        # --- 向量化指标计算 ---
        master_df = batch_calculate_indicators(master_df, bench_df=bench_slice)

        # Pine Script 策略或 同时启用 策略需要额外的指标计算
        if strategy_type in ["pine", "both", "tv_zp", "tv_dual", "tv_dual_strict"]:
            logger.info("Calculating Pine Script indicators in parallel...")
            # 对每只股票单独计算 Pine 指标 (使用并行加速)
            groups = [group.copy() for _, group in master_df.groupby('code')]

            with ThreadPoolExecutor(max_workers=8) as executor:
                pine_results = list(executor.map(calculate_pine_indicators, groups))

            if pine_results:
                master_df = pd.concat(pine_results, ignore_index=True)
            logger.info(f"Parallel Pine Script indicators calculation completed.")

        logger.info(f"Batch indicator calculation completed in {time.time() - start_time:.2f}s.")
        mark_phase("indicator_batch")

        # 按代码切分，供并发扫描使用
        ctx.hist_map = hist_map = {code: group for code, group in master_df.groupby('code')}

    except Exception as e:
        logger.error(f"Batch processing failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"数据预处理失败: {str(e)}")

    return False


def _scan_evaluate_candidates(ctx: _MarketScanContext, mark_phase: Callable[[str], None]) -> None:
    """阶段 4/6：装载基本面/资金流映射、线程池并发执行单股策略扫描、RPS 截面闸门、
    Top 100 截断、板块上下文构建与板块观察/历史复活/动量加速候选注入。"""
    engine = ctx.engine
    candidates = ctx.candidates
    hist_map = ctx.hist_map
    bench_slice = ctx.bench_slice
    audit_payload = ctx.audit_payload
    snapshot_df = ctx.snapshot_df
    max_date = ctx.max_date
    data_date = ctx.data_date
    resolved_data_date = ctx.resolved_data_date
    strategy_type = ctx.strategy_type
    threshold = ctx.threshold
    vol_multiplier = ctx.vol_multiplier
    rsi_min = ctx.rsi_min
    use_macd_filter = ctx.use_macd_filter
    use_bb_sqz = ctx.use_bb_sqz
    sqz_lookback = ctx.sqz_lookback
    use_weekly = ctx.use_weekly
    use_rs_filter = ctx.use_rs_filter
    local_only = ctx.local_only
    pine_min_signals = ctx.pine_min_signals
    min_data_days = ctx.min_data_days
    weekly_ma_period = ctx.weekly_ma_period
    tv_weekly_gate = ctx.tv_weekly_gate

    # 加载基本面数据
    ctx.fund_map = fund_map = {}
    try:
        with engine.connect() as conn:
            fund_res = conn.execute(text("SELECT code, roe, net_profit_yoy, revenue_yoy, label FROM stock_fundamentals")).fetchall()
            for r in fund_res:
                # 强制使用字符串作为 Key，防止 pandas 类型推断导致 int/str 匹配失败
                code_key = str(r[0]).zfill(6)
                fund_map[code_key] = {
                    "roe": float(r[1]) if r[1] is not None else 0.0,
                    "net_profit_yoy": float(r[2]) if r[2] is not None else 0.0,
                    "revenue_yoy": float(r[3]) if r[3] is not None else 0.0,
                    "label": str(r[4]) if r[4] is not None else ""
                }
            logger.info(f"Loaded fundamentals for {len(fund_map)} stocks from database.")
    except Exception as e:
        logger.error(f"Failed to load fundamentals: {e}")

    ctx.money_flow_map = money_flow_map = _build_scan_money_flow_map(limit=6000)

    # 并发扫描逻辑 - 执行策略筛选和周线确认
    workers = 24  # 向量化后主压力在周线重采样，可提高并发
    logger.info(f"Starting strategy scan for {len(candidates)} stocks (workers={workers})...")

    ctx.results = results = []
    fail_reasons = {}
    none_count = 0
    processed_count = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_stock = {
            executor.submit(
                single_stock_task,
                row['code'], row['name'], row['price'], row['vol'], row['open'],
                threshold, vol_multiplier, rsi_min, use_macd_filter, use_bb_sqz, sqz_lookback, use_weekly, use_rs_filter,
                local_only=local_only, engine=engine, preloaded_df=hist_map.get(row['code']), target_date=data_date,
                bench_df=bench_slice, strategy_type=strategy_type, pine_min_signals=pine_min_signals, min_data_days=min_data_days,
                weekly_ma_period=weekly_ma_period, fund_data=fund_map.get(str(row['code'])),
                tv_weekly_gate=tv_weekly_gate
            ): row for _, row in candidates.iterrows()
        }

        for future in as_completed(future_to_stock):
            processed_count += 1
            if processed_count % 100 == 0 or processed_count == len(future_to_stock):
                logger.info(f"Scan Progress: {processed_count}/{len(future_to_stock)} stocks processed...")
                ws_manager.broadcast_threadsafe({
                    "type": "scan_progress",
                    "current": processed_count,
                    "total": len(future_to_stock),
                    "message": f"扫描中... ({processed_count}/{len(future_to_stock)})"
                })

            try:
                res = future.result(timeout=60)
                if isinstance(res, dict) and 'Score' in res:
                    results.append(res)
                elif isinstance(res, dict):
                    reason = res.get('reason', '未知')
                    fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
                elif res is None:
                    none_count += 1
            except Exception as e:
                fail_reasons[f"异常: {str(e)[:30]}"] = fail_reasons.get(f"异常: {str(e)[:30]}", 0) + 1

        normalized_fail_reasons, fail_reason_details = _summarize_rejection_reasons(fail_reasons)
        logger.info(f"Scan Stats: Matches={len(results)}, Rejections={sum(fail_reasons.values())}")
        audit_payload["fail_reasons"] = normalized_fail_reasons
        audit_payload["params_snapshot"]["fail_reason_details"] = fail_reason_details
        if normalized_fail_reasons:
            logger.info(f"Rejection Summary: {normalized_fail_reasons}")

        # Pine 策略或 同时启用 策略额外统计
        if strategy_type in ["pine", "both", "tv_zp", "tv_dual", "tv_dual_strict"]:
            pine_stats = {}
            for reason, count in fail_reasons.items():
                if "信号不足" in reason:
                    # 提取信号数，如 "信号不足 (2/3)"
                    match = re.search(r'\((\d+)/(\d+)\)', reason)
                    if match:
                        signals = int(match.group(1))
                        pine_stats[signals] = pine_stats.get(signals, 0) + count
            if pine_stats:
                logger.info(f"Pine Strategy Signal Distribution: {pine_stats}")

    logger.info(f"Scan completed in {time.time() - start_time:.2f}s. Found {len(results)} matches.")
    mark_phase("strategy_evaluation")

    # 排序并取 Top 100；先记录截断压力，供影子排序验证，不改变正式结果。
    rps_map: Dict[str, Dict[str, Any]] = {}
    ctx.rps_map = rps_map
    if strategy_type in {"rps_breakout", "trader_vic_2b"}:
        from core.sequoia_research import load_cross_sectional_rps

        rps_as_of = str(max_date)[:10] if max_date else datetime.now().strftime("%Y-%m-%d")
        rps_map = load_cross_sectional_rps(engine, rps_as_of)
        ctx.rps_map = rps_map
        rps_threshold = 90 if strategy_type == "rps_breakout" else 60
        ctx.results = results = [
            row for row in results
            if (rps_map.get(str(row.get("代码") or "").zfill(6), {}).get("rps_120") or 0) >= rps_threshold
        ]
        for row in results:
            row.update(rps_map.get(str(row.get("代码") or "").zfill(6), {}))
        audit_payload[f"{strategy_type}_rps_gate"] = {
            "as_of": rps_as_of,
            "threshold": rps_threshold,
            "candidate_count": len(results),
            "universe_count": len(rps_map),
            "trade_permission": False,
        }

    ctx.results = results = sorted(results, key=lambda x: x['Score'], reverse=True)
    prelimit_count = len(results)
    audit_payload["params_snapshot"]["prelimit_ranking"] = {
        "candidate_count": prelimit_count,
        "limit": 100,
        "truncated_count": max(0, prelimit_count - 100),
        "top100_cutline_score": round(float(results[99]['Score']), 2) if prelimit_count >= 100 else None,
        "top200_cutline_score": round(float(results[199]['Score']), 2) if prelimit_count >= 200 else None,
    }
    ctx.results = results = results[:100]
    if strategy_type == "limit_up_shakeout":
        from core.limit_up_leadership import load_limit_up_event_map
        from core.sequoia_research import confirm_limit_up_shakeout_candidates

        signal_date = str(max_date)[:10] if max_date else datetime.now().strftime("%Y-%m-%d")
        prior_event_date = shift_a_share_trading_date(signal_date, -1)
        before_event_gate = len(results)
        prior_event_map = load_limit_up_event_map(prior_event_date, engine)
        ctx.results = results = confirm_limit_up_shakeout_candidates(
            results,
            prior_event_map,
            prior_event_date,
        )
        audit_payload["limit_up_shakeout_event_gate"] = {
            "event_date": prior_event_date,
            "input_count": before_event_gate,
            "confirmed_count": len(results),
            "event_universe_count": len(prior_event_map),
            "fail_closed": True,
        }

    ctx.sector_map = sector_map = get_sector_map()
    ctx.sector_trends = sector_trends = get_sector_trends()
    ctx.market_regime = market_regime = get_market_regime()
    # 把实时快照聚合写入 breadth_history（修复 6/22 节后首日 bug：让后续
    # build_sector_history_context / load_market_cycle_history 读到今日实时宽度，
    # 而非滞后的 daily_k）。失败只 log 不阻断扫描。
    # bar_date 必须显式传 data_date：回放扫描（data_date=历史日）的快照是
    # 历史数据，缺省会被写成"今天"，此后实时扫描的板块强度全部读到被历史
    # 数据覆盖的宽度。实时扫描 data_date=None → record 函数缺省用今天，行为不变。
    if not snapshot_df.empty and 'pct_chg' in snapshot_df.columns:
        from core.db import record_breadth_snapshot
        record_breadth_snapshot(snapshot_df, sector_map, engine, bar_date=data_date)
    sector_history = build_sector_history_context(engine, sector_map, as_of=data_date)
    ctx.monthly_sector_context = monthly_sector_context = build_previous_month_sector_context(
        engine,
        sector_map,
        as_of_date=resolved_data_date,
    )
    ctx.sector_strength = sector_strength = build_sector_strength(
        snapshot_df,
        sector_map,
        sector_trends,
        sector_history,
        monthly_sector_context,
    )

    if strategy_type == "early_value":
        results, dropped, kept_pending = _apply_early_value_sector_filter(results, sector_map, sector_strength)
        ctx.results = results
        if dropped:
            logger.info(f"Early value sector-start filter dropped {dropped} candidates.")
        if kept_pending:
            logger.info("Early value sector-start filter found no confirmed sectors; keeping pending watch candidates.")

    if _should_include_sector_watch(strategy_type):
        ctx.results = results = []
        sector_watch = _build_sector_watch_candidates(
            candidates,
            set(),
            hist_map,
            sector_map,
            sector_strength,
        )
        if sector_watch:
            logger.info(f"Added {len(sector_watch)} sector-watch candidates.")
            results.extend(sector_watch)
    elif strategy_type in {"tv_dual", "tv_dual_strict"}:
        existing_codes = {str(res.get('代码', '')).zfill(6) for res in results}
        sector_watch = _build_sector_watch_candidates(
            candidates,
            existing_codes,
            hist_map,
            sector_map,
            sector_strength,
            max_per_sector=2,
        )
        if sector_watch:
            logger.info(
                f"Added {len(sector_watch)} early strong-sector observation candidates."
            )
            results.extend(sector_watch)

    active_plan_map: Dict[str, Dict[str, Any]] = {}
    ctx.active_plan_map = active_plan_map
    if strategy_type in {"tv_dual", "tv_dual_strict"}:
        existing_codes = {str(res.get('代码', '')).zfill(6) for res in results}
        candidate_codes = [str(code).zfill(6) for code in candidates['code'].tolist()]
        signal_map = _fetch_recent_signal_map(
            engine,
            candidate_codes,
            str(max_date or datetime.now().strftime("%Y-%m-%d")),
        )
        ctx.active_plan_map = active_plan_map = _fetch_active_execution_plan_map(
            engine,
            candidate_codes,
            str(max_date or datetime.now().strftime("%Y-%m-%d")),
        )
        revival_candidates = _build_historical_revival_candidates(
            candidates,
            existing_codes,
            hist_map,
            signal_map,
            strategy_type,
        )
        if revival_candidates:
            logger.info(f"Added {len(revival_candidates)} historical revival candidates.")
            results.extend(revival_candidates)
            existing_codes.update(str(res.get('代码', '')).zfill(6) for res in revival_candidates)
        momentum_candidates = _build_momentum_acceleration_candidates(
            candidates,
            existing_codes,
            hist_map,
            sector_map,
            sector_strength,
            strategy_type,
        )
        if momentum_candidates:
            logger.info(f"Added {len(momentum_candidates)} momentum acceleration candidates.")
            results.extend(momentum_candidates)

    mark_phase("strategy_post_filter")


def _scan_enrich_candidates(ctx: _MarketScanContext, mark_phase: Callable[[str], None]) -> None:
    """阶段 5/6：并发回测补充（胜率/行业/风险位）、近期推送/冻结计划/早期观察质量
    过滤，及板块共振/地雷/换手/PE/板块角色/资金流/RPS/景气度/月度板块等增强注入。"""
    engine = ctx.engine
    results = ctx.results
    hist_map = ctx.hist_map
    fund_map = ctx.fund_map
    money_flow_map = ctx.money_flow_map
    active_plan_map = ctx.active_plan_map
    audit_payload = ctx.audit_payload
    snapshot_df = ctx.snapshot_df
    candidates = ctx.candidates
    max_date = ctx.max_date
    data_date = ctx.data_date
    snapshot_as_of = ctx.snapshot_as_of
    data_mode = ctx.data_mode
    strategy_type = ctx.strategy_type
    stop_loss_pct = ctx.stop_loss_pct
    pine_min_signals = ctx.pine_min_signals
    threshold = ctx.threshold
    vol_multiplier = ctx.vol_multiplier
    rsi_min = ctx.rsi_min
    use_macd_filter = ctx.use_macd_filter
    use_bb_sqz = ctx.use_bb_sqz
    sqz_lookback = ctx.sqz_lookback
    use_rs_filter = ctx.use_rs_filter
    sector_map = ctx.sector_map
    sector_trends = ctx.sector_trends
    sector_strength = ctx.sector_strength
    market_regime = ctx.market_regime
    monthly_sector_context = ctx.monthly_sector_context
    rps_map = ctx.rps_map

    # 补充增强 data (行业, 胜率) - 并发处理 Top 100 + 板块观察
    logger.info(f"Parallel supplementing {len(results)} results (WinRate + Industry)...")

    def process_supplement(res):
        try:
            if res.get('sector_watch_only'):
                return res
            _inject_missing_fundamentals(res, fund_map)
            code = res['代码']
            # 1. 计算回测统计
            # 直接使用 hist_map 中已计算好指标的数据，避免重复计算
            df_hist = hist_map.get(code)
            if df_hist is not None:
                df_hist = df_hist.copy().reset_index(drop=True)
            df_labeled = df_hist
                
            # 获取止损参数 (前端可配置)
            try:
                sl_pct = float(stop_loss_pct)
            except (TypeError, ValueError):
                sl_pct = BACKTEST_STOP_LOSS_PCT
                
            if strategy_type == "pine":
                bt = calculate_pine_win_rate(df_labeled, min_signals=pine_min_signals, stop_loss_pct=sl_pct, code=code)
            elif strategy_type == "tv_zp":
                bt = calculate_tv_zp_win_rate(df_labeled, stop_loss_pct=sl_pct, code=code)
            elif strategy_type in {"tv_dual", "tv_dual_strict"}:
                bt = calculate_tv_dual_win_rate(
                    df_labeled,
                    stop_loss_pct=sl_pct,
                    threshold=threshold,
                    vol_multiplier=vol_multiplier,
                    rsi_min=rsi_min,
                    use_macd_filter=use_macd_filter,
                    sqz_lookback=sqz_lookback,
                    require_both=(strategy_type == "tv_dual_strict"),
                    code=code,
                )
            elif strategy_type == "both":
                bt = calculate_pine_win_rate(df_labeled, min_signals=pine_min_signals, stop_loss_pct=sl_pct, code=code)
            elif strategy_type == "consensus":
                bt = calculate_consensus_win_rate(df_labeled, stop_loss_pct=sl_pct, code=code)
            elif strategy_type == "h2":
                bt = {
                    "win_rate": 0,
                    "signal_count": 0,
                    "avg_return": 0,
                    "max_drawdown": 0,
                    "profit_factor": 0,
                    "avg_hold_days": 0,
                    "stop_loss_hits": 0,
                    "adjusted_win_rate": 0,
                    "confidence": 0,
                    "expectancy": 0,
                    "sample_warning": "H2独立策略暂无单股专项回测样本",
                }
            elif strategy_type in {"high_tight_flag", "turtle_breakout", "limit_up_shakeout"}:
                bt = calculate_research_pattern_win_rate(
                    df_labeled,
                    strategy_type,
                    stop_loss_pct=sl_pct,
                    code=code,
                )
            else:
                bt = calculate_historical_win_rate(
                    df_labeled,
                    stop_loss_pct=sl_pct,
                    threshold=threshold,
                    vol_multiplier=vol_multiplier,
                    rsi_min=rsi_min,
                    use_macd_filter=use_macd_filter,
                    use_bb_sqz=use_bb_sqz,
                    sqz_lookback=sqz_lookback,
                    use_rs_filter=use_rs_filter,
                    code=code,
                )
                
            res['历史胜率'] = f"{bt['win_rate']}%"
            res['信号次数'] = bt['signal_count']
            res['回测统计'] = {
                "avg_return": bt['avg_return'],
                "max_drawdown": bt['max_drawdown'],
                "profit_factor": bt['profit_factor'],
                "avg_hold_days": bt['avg_hold_days'],
                "stop_loss_hits": bt['stop_loss_hits'],
                "adjusted_win_rate": bt.get('adjusted_win_rate', bt['win_rate']),
                "adjusted_win_rate_method": bt.get('adjusted_win_rate_method', 'wilson_lower_99'),
                "confidence": bt.get('confidence', 1.0),
                "expectancy": bt.get('expectancy', 0),
                "sample_warning": bt.get('sample_warning', ''),
                "backtest_engine_version": BACKTEST_ENGINE_VERSION,
                "exit_rule_version": EXIT_RULE_VERSION,
            }
            res['strategy_logic_version'] = STRATEGY_LOGIC_VERSION
            res['backtest_engine_version'] = BACKTEST_ENGINE_VERSION
            res['exit_rule_version'] = EXIT_RULE_VERSION

            # 2. 获取行业
            industry = sector_map.get(code, "未知")
            if industry == "未知":
                try:
                    import akshare as ak
                    info_df = ak.stock_individual_info_em(symbol=code)
                    if not info_df.empty:
                        industry_val = info_df[info_df['item'] == '行业分类']['value'].values
                        if len(industry_val) > 0:
                            industry = industry_val[0]
                except Exception: pass
            res['行业'] = industry

            # 3. SOP 新增字段
            if df_hist is not None and len(df_hist) >= 6:
                close_now = float(df_hist['收盘'].iloc[-1])
                close_5d_ago = float(df_hist['收盘'].iloc[-6])
                res['pct_5d'] = round((close_now - close_5d_ago) / close_5d_ago * 100, 2)
            else:
                res['pct_5d'] = 0.0

            # 候选计划价：突破确认位 + 结构失效/初始风控，而不是简单 -8%
            if df_hist is not None and not df_hist.empty:
                pa = analyze_price_action(df_hist)
                res.update(pa)
                chip = build_chip_distribution(df_hist)
                if chip.get('available'):
                    res['chip_distribution'] = chip
                    res['chip_buy_impact'] = chip.get('buy_impact')
                    res['chip_holding_impact'] = chip.get('holding_impact')
                    res['chip_score_delta'] = chip.get('score_delta', 0)
                entry_price = float(pa.get('pa_entry_price') or df_hist['最高'].iloc[-1])
                current_price = float(df_hist['收盘'].iloc[-1])
                risk = compute_paper_risk_levels(entry_price, entry_price, current_price, pa)
                res['entry_price'] = round(entry_price, 2)
                res['stop_price'] = risk['active_stop_price']
                res['plan_stop_price'] = risk['active_stop_price']
                res['initial_stop_price'] = risk['initial_stop_price']
                res['structure_stop_price'] = risk['structure_stop_price']
                res['target_price'] = risk['take_profit_price']
                res['risk_reward'] = risk['risk_reward']
                res['risk_notes'] = risk['risk_notes']
                if not res.get('结构') and pa.get('price_action_pattern') not in (None, "无明确形态"):
                    res['结构'] = pa.get('price_action_pattern')
            else:
                res['entry_price'] = res.get('现价', 0)
                risk = compute_paper_risk_levels(float(res.get('现价', 0)), float(res.get('现价', 0)), float(res.get('现价', 0)))
                res['stop_price'] = risk['active_stop_price']
                res['plan_stop_price'] = risk['active_stop_price']
                res['target_price'] = risk['take_profit_price']

        except Exception as e:
            logger.error(f"Supplement error for {res.get('代码')}: {e}")
        return res

    # 使用线程池并发补充 100 只股票
    with ThreadPoolExecutor(max_workers=15) as executor:
        list(executor.map(process_supplement, results))
    mark_phase("result_supplement")

    recent_push_counts = load_recent_push_counts(
        engine,
        (res.get("代码") for res in results),
        strategy_type,
        str(max_date or datetime.now().strftime("%Y-%m-%d")),
    )
    for res in results:
        res["recent_push_days"] = recent_push_counts.get(str(res.get("代码") or "").zfill(6), 1)

    for res in results:
        _apply_frozen_execution_plan(
            res,
            active_plan_map.get(str(res.get('代码', '')).zfill(6), {}),
        )

    early_drop_count = 0
    for res in results:
        if res.get('early_watch_only'):
            ok, reasons = _early_watch_quality(res)
            res['early_watch_quality_ok'] = ok
            res['early_watch_quality_reasons'] = reasons
            if not ok:
                early_drop_count += 1
                res['_drop_early_watch'] = True
    if early_drop_count:
        logger.info(f"Early watch quality filter dropped {early_drop_count} candidates.")
        ctx.results = results = [r for r in results if not r.get('_drop_early_watch')]

    # --- SOP: 板块共振 (Sector Resonance) 计算 ---
    industry_counts = {}
    for res in results:
        ind = res.get('行业', '未知')
        industry_counts[ind] = industry_counts.get(ind, 0) + 1

    for res in results:
        ind = res.get('行业', '未知')
        if industry_counts.get(ind, 0) > 1 and ind != '未知':
            res['共振'] = "🔥 核心热点"
        else:
            res['共振'] = "独苗"

    # --- SOP: 地雷监测 (Mine Sweeper) ---
    mine_data = fetch_mine_sweeper_data()
    for res in results:
        code = res['代码']
        warnings = []
        if code in mine_data["earnings"]: warnings.append("📅 财报")
        if code in mine_data["unlocks"]: warnings.append("🔒 解禁")
        # reductions 分支已停用：原数据源 ak.stock_dzjy_mrtj() 是大宗交易（≠减持），
        # 且返回陈旧数据，曾导致 601138 等股票被误判 D 级。mine_data["reductions"] 恒为空。
        # 若未来接入正确的减持数据源（如高管减持公告），此行可直接复用。
        if code in mine_data["reductions"]: warnings.append("⚠️ 减持")
        res['warnings'] = warnings

    # --- SOP: 注入市值/换手/PE (从快照数据) ---
    snap_mkt_map = {}
    snap_turnover_map = {}
    snap_pe_map = {}
    liquidity_map = {
        str(row["code"]).zfill(6): {
            "avg_amount_5d": float(row.get("avg_amount_5d") or 0),
            "avg_amount_5d_estimated": bool(row.get("avg_amount_5d_estimated", True)),
        }
        for _, row in candidates.iterrows()
    }
    if not snapshot_df.empty and 'mkt_cap' in snapshot_df.columns:
        for _, row in snapshot_df.iterrows():
            code = str(row['code'])
            snap_mkt_map[code] = row.get('mkt_cap', 0)
            snap_turnover_map[code] = row.get('turnover', None)
            # 改动 P2：注入 PE（快照含 pe 列，原代码漏注入导致 scan_history.pe 全 None）
            snap_pe_map[code] = row.get('pe', None)
    for res in results:
        code = res['代码']
        res.update(liquidity_map.get(str(code).zfill(6), {}))
        mkt_raw = snap_mkt_map.get(code, 0)
        res['mkt_cap_yi'] = round(float(mkt_raw) / 1e8, 1) if mkt_raw else 0
        turnover_raw = snap_turnover_map.get(code)
        if turnover_raw is not None:
            res['turnover'] = float(turnover_raw or 0)
        elif res['mkt_cap_yi'] > 0:
            # 改动 P1：快照无换手率（盘后/快照过期）时，用"成交额/市值"复算近似换手率。
            # turnover ≈ (vol × close) / mkt_cap × 100。这是标准近似（流通市值≈总市值的大盘股误差小），
            # 让"大市值低换手"过滤(306-309行)在盘后也能生效，不再因 turnover 缺失而静默跳过。
            _vol = float(res.get('成交量', 0) or res.get('vol', 0) or 0)
            _close = float(res.get('price', 0) or res.get('最新价', 0) or 0)
            _mkt = float(mkt_raw)  # 元
            if _vol > 0 and _close > 0 and _mkt > 0:
                res['turnover'] = round(_vol * _close / _mkt * 100, 2)
        # 改动 P2：注入 PE（原代码漏注入，导致 scan_history.pe 全 None）
        pe_raw = snap_pe_map.get(code)
        if pe_raw is not None:
            try:
                res['pe'] = round(float(pe_raw), 1)
            except (TypeError, ValueError):
                pass

    # 改动 P0：预计算每只票在板块内的涨幅排名（用于 classify_sector_role 的 LEADER 判定）。
    # 原 res.update(strength) 会把"板块排名"(sector_rank) 覆盖到 res，但那是板块在全市场的排名，
    # 不是个股在板块内的排名。这里从快照按板块分组、涨幅降序算出个股板块内 rank。
    stock_sector_rank_map: Dict[str, int] = {}
    if not snapshot_df.empty and 'pct_chg' in snapshot_df.columns and 'industry' in snapshot_df.columns:
        snap_rank = snapshot_df.copy()
        snap_rank['code'] = snap_rank['code'].astype(str).str.zfill(6)
        snap_rank['pct_chg'] = pd.to_numeric(snap_rank['pct_chg'], errors='coerce').fillna(0)
        for _ind, _grp in snap_rank.groupby('industry'):
            _ranked = _grp.sort_values('pct_chg', ascending=False)
            for _r, (_, _row) in enumerate(_ranked.iterrows(), start=1):
                stock_sector_rank_map[str(_row['code']).zfill(6)] = _r

    # 注入板块走势到每个结果
    sector_fund_flow_map = _load_sector_fund_flow_map()
    for res in results:
        sector = res.get('行业', '')
        s_info = sector_trends.get(sector, {})
        res['sector_trend'] = s_info.get('trend', 'UNKNOWN')
        res['sector_pct'] = s_info.get('pct', 0)
        strength = sector_strength.get(sector, {})
        res.update(strength)
        if sector in sector_fund_flow_map:
            res['sector_main_net_inflow_5d_yi'] = sector_fund_flow_map[sector]
        stock_pct = float(res.get('涨幅%', 0) or 0)
        sector_avg = float(strength.get('sector_avg_pct', res.get('sector_pct', 0)) or 0)
        relative_pct = round(stock_pct - sector_avg, 2)
        res['sector_relative_pct'] = relative_pct
        # 个股在板块内的涨幅排名（P0：传入 rank 约束 LEADER 判定）
        _stock_rank_in_sector = stock_sector_rank_map.get(str(res.get('代码', '')).zfill(6), 0)
        res['stock_rank_in_sector'] = _stock_rank_in_sector
        res['sector_strength_score'] = _sector_strength_score(res)
        res['stock_sector_fit_score'] = _stock_sector_fit_score(res)
        res['sector_alignment_score'] = _combined_sector_alignment(
            res['sector_strength_score'],
            res['stock_sector_fit_score'],
        )
        res['sector_role'] = classify_sector_role(
            stock_pct,
            sector_avg,
            rank_in_sector=_stock_rank_in_sector,
            alignment_score=res['sector_alignment_score'],
        )
        # sector_role 依赖 alignment，算出角色后重新计算个股适配，让龙头/核心定位参与解释。
        res['stock_sector_fit_score'] = _stock_sector_fit_score({**res, 'stock_sector_fit_score': None})
        res['sector_alignment_score'] = _combined_sector_alignment(
            res['sector_strength_score'],
            res['stock_sector_fit_score'],
        )

    # 行业景气度聚合（ROE/净利同比中位数）：供逻辑链展示与AI复核参考，不参与风控判定。
    prosperity_map = build_industry_prosperity(results)
    if prosperity_map:
        for res in results:
            _prosperity = prosperity_map.get(str(res.get('行业') or '').strip())
            if _prosperity:
                res['industry_prosperity'] = _prosperity

    if monthly_sector_context:
        # 月度板块强弱只做标注与排序参考，不做硬性剔除。
        # 上月前5板块 × 每板块2只月度龙头的硬闸门曾把全市场日信号压到个位数。
        monthly_leaders = build_sector_leaders(
            engine,
            snapshot_df,
            sector_map,
            sector_strength,
            top_n=MONTHLY_SECTOR_LEADERS_PER_SECTOR,
        )
        leader_code_set = {
            str(item.get("code") or "").zfill(6)
            for items in monthly_leaders.values()
            for item in items
        }
        for res in results:
            res["monthly_sector_leader"] = str(res.get("代码") or "").zfill(6) in leader_code_set
        monthly_sample = next(iter(monthly_sector_context.values()))
        audit_payload["params_snapshot"]["monthly_sector_gate"] = {
            "period": monthly_sample.get("sector_prev_month_period"),
            "mode": "annotate_only",
            "top_sectors": sorted(
                sector for sector, context in monthly_sector_context.items()
                if context.get("sector_prev_month_top5")
            ),
            "leaders_per_sector": MONTHLY_SECTOR_LEADERS_PER_SECTOR,
            "results_in_top5_sectors": sum(
                1 for res in results if res.get("sector_prev_month_top5")
            ),
            "results_monthly_leaders": sum(
                1 for res in results if res.get("monthly_sector_leader")
            ),
        }

    # 横截面 RPS 使用最新完整日线截面计算，只参与排序和解释，不授予交易权限。
    from core.sequoia_research import load_cross_sectional_rps
    rps_as_of = str(max_date)[:10] if max_date else datetime.now().strftime("%Y-%m-%d")
    if not rps_map:
        rps_map = load_cross_sectional_rps(engine, rps_as_of) if results else {}
        ctx.rps_map = rps_map
    for res in results:
        res.update(rps_map.get(str(res.get("代码") or "").zfill(6), {}))
    audit_payload["rps_factor"] = {
        "as_of": rps_as_of,
        "universe_count": len(rps_map),
        "point_in_time": True,
        "trade_permission": False,
    }

    # 改动 #17：预查近期失败模式，注入 recent_failure_count 供 _apply_sop_filter 否决
    _inject_failure_pattern(results, engine)
    _inject_breakdown_retracement(results, hist_map)
    _inject_capital_event_risk(results, engine, as_of=data_date)
    for res in results:
        if res.get('capital_event_risk'):
            warnings = list(res.get('warnings') or [])
            warnings.append("🏦 定增/资本事件")
            res['warnings'] = warnings

    _apply_money_flow_to_results(results, money_flow_map)
    for res in results:
        _apply_close_confirmation_timing(res, snapshot_as_of, data_mode)
        history = res.get('revival_history')
        if history:
            res.update(_classify_historical_revival(res, history))

    # 受控试仓先读取历史健康度；数据库异常时健康门禁故障安全关闭。
    from core.a_minus_trial import build_a_minus_trial_health
    a_minus_trial_health = build_a_minus_trial_health(engine)
    for res in results:
        res['a_minus_trial_health'] = a_minus_trial_health

    # 成长板块可能先于宽基指数修复；先注入板块级市场状态，再进行 SOP 评分。
    growth_segment_context = apply_growth_segment_context(results, snapshot_df, market_regime)
    market_regime['growth_segments'] = growth_segment_context
    mark_phase("result_enrichment")


def _scan_decide_and_persist(ctx: _MarketScanContext, mark_phase: Callable[[str], None]) -> None:
    """阶段 6/6：决策管线（SOP 过滤/决策层/评分校准/策略健康/事件驱动）、候选证据与
    执行可达性、结果排序与哨兵推送、扫描结果与审计日志持久化及 scan_end 广播。"""
    engine = ctx.engine
    results = ctx.results
    audit_payload = ctx.audit_payload
    snapshot_df = ctx.snapshot_df
    max_date = ctx.max_date
    data_date = ctx.data_date
    snapshot_as_of = ctx.snapshot_as_of
    data_mode = ctx.data_mode
    resolved_data_date = ctx.resolved_data_date
    discovery_pool = ctx.discovery_pool
    phase_timings = ctx.phase_timings
    start_time = ctx.start_time
    strategy_type = ctx.strategy_type
    publish_to_sentinel = ctx.publish_to_sentinel
    market_regime = ctx.market_regime
    sector_trends = ctx.sector_trends

    # 应用质量与风险评估；旧字母等级只在函数内部保留以兼容历史测试，
    # 不再参与后续准入、排序、推送或持久化。
    _apply_sop_filter(results, market_regime, sector_trends)
    for res in results:
        for legacy_grade_field in (
            "sop_grade", "sop_subgrade", "sop_base_grade", "sop_a_grade_eligible",
            "sop_a_grade_gate_reasons", "sop_grade_policy_version",
            "sop_quality_gap_to_a", "sop_grade_transition_reasons", "sop_grade_reason",
            "grade_execution_mode", "grade_execution_shadow_eligible",
        ):
            res.pop(legacy_grade_field, None)
    for res in results:
        res['market_regime'] = market_regime.get('status', 'UNKNOWN')
    from core.limit_up_leadership import apply_limit_up_features, load_limit_up_event_map
    scan_event_date = str(max_date) if max_date else datetime.now().strftime("%Y-%m-%d")
    apply_limit_up_features(results, load_limit_up_event_map(scan_event_date, engine))
    from core.event_driven import apply_event_catalysts
    apply_event_catalysts(
        results,
        load_active_event_catalysts(engine, as_of=scan_event_date),
    )
    from core.decision_layer import load_market_cycle_history
    decision_context = apply_decision_layer(
        results,
        snapshot_df,
        market_regime,
        load_market_cycle_history(engine, as_of=data_date),
        data_date=str(max_date) if max_date else None,  # 改动 A3：传数据日，修复周末误判
    )
    from core.score_calibration import calibrate_scan_scores
    calibrate_scan_scores(results)
    from core.strategy_health import apply_strategy_health_controls, build_strategy_health
    apply_strategy_health_controls(results, build_strategy_health(engine))
    from core.event_driven import finalize_event_trade_state
    finalize_event_trade_state(results)
    from core.decision_semantics import apply_decision_semantics
    apply_decision_semantics(results)
    for row in results:
        row.setdefault("discovery_pool", discovery_pool)
        row["trade_blockers"] = _dedupe_trade_blockers(list(row.get("trade_blockers") or []))
        row["trade_blocker_groups"] = classify_trade_blockers(row["trade_blockers"])
        row["execution_rr"] = build_execution_rr(row)
        row["execution_plan_state"] = build_frozen_plan_state(row)
        row["distance_to_trade"] = build_distance_to_trade(row)
    _apply_research_only_gate(results, audit_payload)
    mark_phase("decision_pipeline")
    from core.candidate_evidence import apply_candidate_evidence
    from core.stock_research import get_cached_stock_research_signals

    # 行业资金流共振证据（借鉴 easy-stock 题材雷达）：把行业排名注入候选，
    # 供证据门 sector_context / decision_memo 参考。映射为空（首日未采集/
    # 接口失败）时整段跳过，fail-open 不影响扫描主流程。
    try:
        from core.db import get_stock_basic_map, load_sector_fund_flow_map

        sector_flow_map = load_sector_fund_flow_map(engine, scan_event_date)
        if sector_flow_map:
            industry_map = get_stock_basic_map(engine)
            for row in results:
                code = str(row.get("代码") or row.get("code") or "").zfill(6)
                industry = str(industry_map.get(code) or "")
                if industry:
                    row.setdefault("stock_industry", industry)
                flow = sector_flow_map.get(industry)
                if flow:
                    row["sector_fund_flow_rank"] = flow.get("rank")
                    row["sector_main_force_net"] = flow.get("main_force_net")
                    row["sector_flow_bar_date"] = flow.get("bar_date")
    except Exception as exc:
        logger.debug(f"Sector fund flow evidence attach skipped: {exc}")

    def _cached_research(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        code = str(row.get("代码") or row.get("code") or "").zfill(6)
        return (
            get_cached_stock_research_signals(code, scan_event_date)
            or get_cached_stock_research_signals(code)
        )

    evidence_summary = apply_candidate_evidence(
        results,
        as_of=snapshot_as_of,
        mode=EVIDENCE_GATE_MODE,
        research_lookup=_cached_research,
    )
    mark_phase("candidate_evidence")
    from core.execution_reachability import apply_execution_reachability
    reachability_summary = apply_execution_reachability(results)
    # Research-only and evidence gates run after the first presentation pass;
    # refresh derived labels so Bark and persisted snapshots reflect final state.
    apply_decision_semantics(results)
    for row in results:
        row["trade_blockers"] = _dedupe_trade_blockers(list(row.get("trade_blockers") or []))
        row["trade_blocker_groups"] = classify_trade_blockers(row["trade_blockers"])
        row["distance_to_trade"] = build_distance_to_trade(row)
        shadow = assess_persistent_b_shadow(row)
        row["persistent_b_shadow"] = shadow
        row["persistent_b_shadow_eligible"] = shadow["eligible"]
    from core.score_calibration import apply_score_display_contract
    apply_score_display_contract(results)
    audit_payload["evidence_pipeline"] = evidence_summary
    audit_payload["params_snapshot"]["evidence_pipeline"] = {
        **evidence_summary, "stage": "DECISION_READY",
    }
    audit_payload["version_snapshot"]["candidate_evidence"] = "candidate-evidence-v1"
    audit_payload["version_snapshot"]["execution_reachability"] = "execution-reachability-v1"
    audit_payload["params_snapshot"]["execution_reachability"] = reachability_summary
    mark_phase("execution_reachability")
    audit_payload["version_snapshot"]["score_calibration"] = "cross-strategy-percentile-v1"
    audit_payload["version_snapshot"]["strategy_health_control"] = "execution-cohort-circuit-breaker-v2"
    audit_payload["version_snapshot"]["decision_layer"] = (
        decision_context.get("market_sentiment_model_version") or "cycle-unknown"
    )
    audit_payload["params_snapshot"]["market_sentiment_stage"] = decision_context.get("market_sentiment_stage")
    audit_payload["params_snapshot"]["portfolio_position_cap_pct"] = decision_context.get("portfolio_position_cap_pct")
    bucket_counts = {
        bucket: sum(1 for row in results if row.get("trade_bucket") == bucket)
        for bucket in ("TRADE", "EARLY", "OBSERVE", "BLOCK")
    }
    logger.info("Execution buckets: %s", bucket_counts)

    bucket_order = {'TRADE': 0, 'EARLY': 1, 'OBSERVE': 2, 'BLOCK': 3}
    ctx.results = results = sorted(
        results,
        key=lambda x: (
            bucket_order.get(str(x.get('trade_bucket') or 'OBSERVE'), 2),
            -float(x.get('trade_opportunity_score') or 0),
            -float(x.get('calibrated_score', x.get('Score', 0)) or 0),
        ),
    )

    # Only the designated execution strategy may update Bark/Sentinel memory.
    if publish_to_sentinel:
        from core.sentinel import sentinel, _select_intraday_push_stocks
        sentinel.last_top_5 = _select_intraday_push_stocks(results) if results else []
    mark_phase("result_ranking")

    # --- 持久化保存 ---
    persist_started_at = time.perf_counter()
    scan_data_date = resolved_data_date
    for res in results:
        res['data_date'] = scan_data_date
        res['data_mode'] = data_mode
        res['as_of'] = str(snapshot_as_of)
        if res.get('revival_watch_only'):
            res['result_group'] = 'HISTORICAL_REVIVAL'
        elif res.get('momentum_acceleration_watch_only'):
            res['result_group'] = 'MOMENTUM_WATCH'
        elif res.get('sequoia_research_shadow_only'):
            res['result_group'] = 'SHADOW_RESEARCH'
        else:
            res['result_group'] = 'FORMAL'
    save_scan_results(
        results,
        engine,
        data_date=scan_data_date,
        replace_strategy_types=[strategy_type],
    )
    phase_timings["result_persistence"] = round(time.perf_counter() - persist_started_at, 3)
    audit_payload["params_snapshot"]["performance_phases_sec"] = phase_timings
    audit_payload.update({
        "scan_date": str(max_date) if max_date else datetime.now().strftime("%Y-%m-%d"),
        "finished_at": datetime.now(),
        "duration_sec": round(time.time() - start_time, 2),
        "result_count": len(results),
    })
    save_scan_audit_log(audit_payload, engine)

    ws_manager.broadcast_threadsafe({
        "type": "scan_end",
        "matches": len(results),
        "message": (
            f"扫描完成！可交易{sum(1 for r in results if r.get('trade_bucket') == 'TRADE')}只 "
            f"观察{sum(1 for r in results if r.get('trade_bucket') in {'EARLY', 'OBSERVE'})}只"
        )
    })


def perform_market_scan(
    threshold: float = 0.12,
    vol_multiplier: float = 1.5,
    rsi_min: int = 55,
    use_macd_filter: bool = True,
    use_bb_sqz: bool = False,
    sqz_lookback: int = 10,
    use_weekly: bool = False,
    market_range: str = "全市场(除科创)",
    turnover_min: float = 3.0,
    mkt_cap_min: float = 0.0,
    use_rs_filter: bool = False,
    local_only: bool = True,
    data_date: Optional[str] = None,
    strategy_type: str = PRIMARY_TV_STRATEGY,
    pine_min_signals: int = 3,
    min_data_days: Optional[int] = None,
    weekly_ma_period: int = 20,
    stop_loss_pct: float = BACKTEST_STOP_LOSS_PCT,
    tv_weekly_gate: bool = False,
    require_live_snapshot: bool = False,
    scan_context: Optional[Dict[str, Any]] = None,
    publish_to_sentinel: bool = True,
) -> List[Dict[str, Any]]:
    """
    Executes the main market scan logic.
    """
    logger.info(f"[RUN_MARKET_SCAN] strategy_type={strategy_type}, min_data_days={min_data_days}, weekly_ma={weekly_ma_period}")
    scan_started_at = datetime.now()
    phase_started_at = time.perf_counter()
    phase_timings: Dict[str, float] = {}

    def mark_phase(name: str) -> None:
        nonlocal phase_started_at
        now = time.perf_counter()
        phase_timings[name] = round(now - phase_started_at, 3)
        phase_started_at = now

    audit_payload: Dict[str, Any] = {
        "started_at": scan_started_at,
        "status": "SUCCESS",
        "strategy_type": strategy_type,
        "params_snapshot": {
            "threshold": threshold,
            "vol_multiplier": vol_multiplier,
            "rsi_min": rsi_min,
            "use_macd_filter": use_macd_filter,
            "use_bb_sqz": use_bb_sqz,
            "sqz_lookback": sqz_lookback,
            "use_weekly": use_weekly,
            "market_range": market_range,
            "turnover_min": turnover_min,
            "mkt_cap_min": mkt_cap_min,
            "use_rs_filter": use_rs_filter,
            "local_only": local_only,
            "data_date": data_date,
            "pine_min_signals": pine_min_signals,
            "min_data_days": min_data_days,
            "weekly_ma_period": weekly_ma_period,
            "stop_loss_pct": stop_loss_pct,
            "require_live_snapshot": require_live_snapshot,
            "evidence_pipeline": {"stage": "PENDING", "mode": EVIDENCE_GATE_MODE},
        },
        "version_snapshot": {
            "strategy_logic_version": STRATEGY_LOGIC_VERSION,
            "backtest_engine_version": BACKTEST_ENGINE_VERSION,
            "exit_rule_version": EXIT_RULE_VERSION,
        },
    }

    ctx = _MarketScanContext(
        strategy_type=strategy_type,
        market_range=market_range,
        use_macd_filter=use_macd_filter,
        use_weekly=use_weekly,
        use_rs_filter=use_rs_filter,
        local_only=local_only,
        data_date=data_date,
        min_data_days=min_data_days,
        weekly_ma_period=weekly_ma_period,
        tv_weekly_gate=tv_weekly_gate,
        require_live_snapshot=require_live_snapshot,
        mkt_cap_min=mkt_cap_min,
        scan_context=scan_context,
        publish_to_sentinel=publish_to_sentinel,
        threshold=threshold,
        vol_multiplier=vol_multiplier,
        rsi_min=rsi_min,
        use_bb_sqz=use_bb_sqz,
        sqz_lookback=sqz_lookback,
        pine_min_signals=pine_min_signals,
        stop_loss_pct=stop_loss_pct,
        turnover_min=turnover_min,
        audit_payload=audit_payload,
        phase_timings=phase_timings,
        scan_started_at=scan_started_at,
    )

    try:
        # 阶段 1-6 与原实现逐一对应；返回 True 的阶段表示按原语义提前返回空结果。
        if _scan_prepare_environment(ctx, mark_phase):
            return []
        _scan_filter_candidates(ctx, mark_phase)
        if _scan_load_data(ctx, mark_phase):
            return []
        _scan_evaluate_candidates(ctx, mark_phase)
        _scan_enrich_candidates(ctx, mark_phase)
        _scan_decide_and_persist(ctx, mark_phase)
        return ctx.results
    except HTTPException as he:
        engine = get_db_engine()
        audit_payload.update({
            "scan_date": str(ctx.max_date) if ctx.max_date else datetime.now().strftime("%Y-%m-%d"),
            "finished_at": datetime.now(),
            "duration_sec": round((datetime.now() - scan_started_at).total_seconds(), 2),
            "status": "FAILED",
            "error_message": str(he.detail),
        })
        save_scan_audit_log(audit_payload, engine)
        raise he
    except Exception as e:
        logger.error(f"Scanner fatal error: {e}")
        import traceback
        traceback.print_exc()
        engine = get_db_engine()
        audit_payload.update({
            "scan_date": str(ctx.max_date) if ctx.max_date else datetime.now().strftime("%Y-%m-%d"),
            "finished_at": datetime.now(),
            "duration_sec": round((datetime.now() - scan_started_at).total_seconds(), 2),
            "status": "FAILED",
            "error_message": str(e)[:500],
        })
        save_scan_audit_log(audit_payload, engine)
        raise HTTPException(status_code=500, detail=f"扫描执行失败: {str(e)}")
