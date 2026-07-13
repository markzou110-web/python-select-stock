"""
Alpha Vision - Core Market Scanning Engine.
Decoupled business logic from FastAPI Routers.
"""
import time
import os
import re
import pandas as pd
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict, Any
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
    check_strategy, check_pine_strategy, check_tv_zp_strategy, check_tv_dual_strategy, check_consensus_strategy,
    calculate_historical_win_rate, calculate_pine_win_rate, calculate_tv_zp_win_rate, calculate_tv_dual_win_rate, calculate_consensus_win_rate,
    STRATEGY_LOGIC_VERSION, BACKTEST_ENGINE_VERSION, EXIT_RULE_VERSION
)
from core.price_action import analyze_price_action
from core.risk_engine import compute_paper_risk_levels
from core.risk_constants import BACKTEST_STOP_LOSS_PCT  # 与实盘硬止损同源，保证回测胜率反映真实规则
from core.scan_preflight import build_scan_preflight

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

# 改动(上班族Bark v2)：破位反抽陷阱多维评分。检测信号前 N 天内单日大跌后，
# 通过"量能/反抽强度/MA20破位时长/V型未确认"四维评分区分真陷阱与黄金坑洗盘，
# 避免单一-5%规则误杀强势股洗盘。
BREAKDOWN_LOOKBACK_DAYS = 5          # 检测窗口（天）
BREAKDOWN_DROP_PCT = -5.0            # 触发评分的单日跌幅阈值
TRAP_VETO_SCORE = 70                 # 评分>=此值 → 一票否决降D级
TRAP_RISK_SCORE = 40                 # 评分>=此值 → 加风险标注（不否决，排序扣分）
TRAP_VOLUME_RATIO_THRESHOLD = 2.0    # 恐慌抛售量比阈值（>=此值视为真洗盘，量能维度0分）
TRAP_MA20_BREAK_DAYS = 3             # MA20下方停留天数阈值（>=此值加分）

# 改动(上班族Bark)：实盘信号门槛收紧。True 时仅 A 级 + 多重共振(🔥核心热点)判为可交易，
# B 级降为观察（上班族无暇盯盘纠错，宁缺毋滥）。False 回退到原 A/B 均可交易逻辑。
STRICT_REAL_SIGNAL_GATE = True
REVIVAL_LOOKBACK_DAYS = 10
REVIVAL_SOURCE_STRATEGIES = ("tv_dual", "tv_dual_strict", "squeeze", "tv_zp")
MOMENTUM_ACCEL_LOOKBACK_DAYS = 5
EXECUTION_PLAN_FREEZE_DAYS = 10
CONFIRMATION_PRICE_TOLERANCE_PCT = 0.05
MAX_FROZEN_ENTRY_EXTENSION_PCT = 3.0
EARLY_VALUE_MIN_RISE_FROM_20D_LOW = 10.0
EARLY_VALUE_MAX_RISE_FROM_20D_LOW = 20.0
EARLY_VALUE_MAX_5D_RISE = 12.0
EARLY_VALUE_MIN_VOLUME_RATIO = 1.05
EARLY_VALUE_MAX_VOLUME_RATIO = 2.20
from core.sector_strength import build_sector_strength, build_sector_history_context, classify_sector_role
from core.money_flow import get_money_flow_rank
from core.decision_layer import apply_decision_layer
from routers.market import fetch_mine_sweeper_data
from core.data_source_quality import get_suspected_adjustment_gap_codes
from core.execution_audit import classify_trade_blockers
from core.execution_insights import build_distance_to_trade, build_execution_rr, build_frozen_plan_state


EXECUTABLE_PA_ACTIONS = {"READY"}
BLOCKED_PA_SETUPS = {"外包K", "交易区间假突破"}
MIN_RAW_EXECUTION_SCORE = 60.0
MAX_EXECUTION_RISK_PCT = 16.0
# 强信号分级加权：原始策略分(raw_score)≥此值时，视为信号强度极高，
# 在 SOP 分级中等效为额外1个check+1个bonus，使强信号更容易达到A/B级
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
CORE_TRADE_STRATEGIES = {"tv_dual_strict"}
DISCOVERY_ONLY_STRATEGIES = {"tv_dual"}
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
        and float(res.get('pct_5d', 0) or 0) <= 15
    )


def _trade_quality_confirmed(res: Dict[str, Any], sector_strength: float, stock_sector_fit: float) -> bool:
    return (
        sector_strength >= MIN_EXECUTION_SECTOR_STRENGTH
        and stock_sector_fit >= MIN_EXECUTION_STOCK_SECTOR_FIT
        and _strong_sector_core_candidate(res, sector_strength, stock_sector_fit)
        and not _strong_sector_rear_candidate(res, sector_strength)
        and _as_float(res.get('sector_alignment_score')) >= MIN_EXECUTION_SECTOR_ALIGNMENT
        and _pa_plan_action(res) == "READY"
        and _has_stable_close_confirmation(res)
    )


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


def _build_snapshot_audit(snapshot_df: pd.DataFrame, data_mode: str, as_of: Any) -> Dict[str, Any]:
    """Describe which point-in-time filters can actually be enforced for this snapshot."""
    required = ("price", "pct_chg", "turnover", "mkt_cap")
    total = len(snapshot_df)
    coverage = {
        field: round(float(snapshot_df[field].notna().mean()), 4)
        if total and field in snapshot_df.columns else 0.0
        for field in required
    }
    effective_filters = ["target_market", "exclude_st_delist", "positive_pct_change"]
    degradation_reasons = []
    if coverage["turnover"] == 1.0:
        effective_filters.append("turnover_min")
    else:
        degradation_reasons.append("换手率字段不完整，未能对全部股票执行换手率过滤")
    if coverage["mkt_cap"] == 1.0:
        effective_filters.append("market_cap_min")
    else:
        degradation_reasons.append("市值字段不完整，未能对全部股票执行市值过滤")
    if coverage["price"] < 1.0 or coverage["pct_chg"] < 1.0:
        degradation_reasons.append("价格或涨跌幅字段不完整")
    return {
        "as_of": as_of,
        "data_mode": data_mode,
        "field_coverage": coverage,
        "effective_filters": effective_filters,
        "research_only": bool(degradation_reasons),
        "degradation_reasons": degradation_reasons,
    }


def _discovery_pool_mask(snapshot_df: pd.DataFrame, strategy_type: str) -> tuple[pd.Series, str]:
    """Keep discovery recall separate from later trade confirmation."""
    pct = pd.to_numeric(snapshot_df["pct_chg"], errors="coerce")
    if strategy_type == "early_value":
        return pct.between(-3.0, 5.0, inclusive="both"), "EARLY_DISCOVERY"
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
    return _as_float(
        res.get('pa_entry_price')
        or res.get('entry_price')
        or res.get('frozen_confirmation_price')
    )


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
    return bool(res.get('pa_volume_confirmed')) or res.get('pa_volume_pattern') in {'放量突破', '缩量回调后放量反包'}


def _has_stable_close_confirmation(res: Dict[str, Any]) -> bool:
    close_position = _as_float(res.get('pa_close_position'), -1.0)
    upper_shadow_pct = _as_float(res.get('pa_upper_shadow_pct'), -1.0)
    close_ok = close_position < 0 or close_position >= 0.6
    shadow_ok = upper_shadow_pct < 0 or upper_shadow_pct < 3
    return close_ok and shadow_ok


def _is_h2_second_entry(setup: str, res: Dict[str, Any]) -> bool:
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
    freeze_days: int = EXECUTION_PLAN_FREEZE_DAYS,
) -> Dict[str, Dict[str, Any]]:
    """Load the earliest still-valid READY plan so the trigger does not move daily."""
    if not codes:
        return {}
    code_params = {f"plan_code_{idx}": str(code).zfill(6) for idx, code in enumerate(codes)}
    placeholders = ", ".join(f":plan_code_{idx}" for idx in range(len(code_params)))
    params = {
        **code_params,
        "data_date": str(data_date)[:10],
        "freeze_days": max(1, int(freeze_days)),
    }
    query = text(f"""
        WITH active AS (
            SELECT s.code, COALESCE(s.data_date, s.date) AS plan_date,
                   s.pa_entry_price, s.pa_stop_price, s.pa_target_price,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.code
                       ORDER BY COALESCE(s.data_date, s.date) ASC, s.scanned_at ASC NULLS LAST
                   ) AS rn
            FROM scan_history s
            WHERE s.code IN ({placeholders})
              AND COALESCE(s.data_date, s.date) < CAST(:data_date AS date)
              AND COALESCE(s.data_date, s.date) >= CAST(:data_date AS date)
                  - (CAST(:freeze_days AS integer) * interval '1 day')
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
        SELECT code, plan_date, pa_entry_price, pa_stop_price, pa_target_price
        FROM active WHERE rn = 1
    """)
    try:
        with engine.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return {str(row._mapping["code"]).zfill(6): dict(row._mapping) for row in rows}
    except Exception as exc:
        logger.warning(f"Frozen execution plan lookup skipped: {exc}")
        return {}


def _apply_frozen_execution_plan(res: Dict[str, Any], plan: Dict[str, Any]) -> None:
    if not plan:
        return
    current = _candidate_price(res)
    entry = _as_float(plan.get("pa_entry_price"))
    stop = _as_float(plan.get("pa_stop_price"))
    if entry <= 0 or stop <= 0 or stop >= entry or (current > 0 and current <= stop):
        return
    res["generated_confirmation_price"] = res.get("pa_entry_price")
    res["generated_stop_price"] = res.get("pa_stop_price")
    res["frozen_plan_date"] = str(plan.get("plan_date") or "")[:10]
    res["frozen_confirmation_price"] = round(entry, 2)
    res["frozen_stop_price"] = round(stop, 2)
    target = _as_float(plan.get("pa_target_price"))
    res["frozen_target_price"] = round(target, 2) if target > 0 else None
    extension_pct = (current - entry) / entry * 100 if current > 0 else 0.0
    res["frozen_entry_extension_pct"] = round(extension_pct, 2)
    res["frozen_confirmation_triggered"] = _confirmation_price_reached(current, entry)
    res["execution_plan_frozen"] = True


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
        and (_as_float(res.get('pct_5d')) >= 15 or pct >= 9)
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
        strong_momentum = pct_5d >= 15 or strong_days >= 2 or limit_like_days >= 1
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
    """Keep strong movers visible without promoting them to executable A/B candidates."""
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
        res['early_trade_grade'] = "A-"
        res['early_trade_reason'] = f"距确认价<{EARLY_ENTRY_MAX_CONFIRM_GAP_PCT:.1f}%，主线强联动，允许小仓提前复核"
    if observe_promotion:
        res['observe_promotion_candidate'] = True
        res['observe_promotion_action'] = "观察转可买：回踩不破支撑后，放量站回确认价或尾盘站稳再小仓"

    if _is_abnormal_price_move(res.get('代码'), res.get('涨幅%')):
        blockers.append("异常价格跳变，排除交易")
    if action == "AVOID":
        blockers.append("价格行为建议回避")
    elif action and action not in EXECUTABLE_PA_ACTIONS:
        blockers.append("交易计划未确认")
    elif not action and strategy_type in {"tv_dual", "tv_dual_strict"}:
        blockers.append("缺少价格行为交易计划")
    if strategy_type in DISCOVERY_ONLY_STRATEGIES:
        blockers.append("普通tv_dual仅用于发现，需严格双策略确认")
    if setup in BLOCKED_PA_SETUPS:
        blockers.append(f"{setup}结构不进入交易池")
    if res.get('pa_pullback_status') == 'INVALIDATED':
        blockers.append("回踩结构失效")
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
    trend_phase = str(res.get('pa_trend_phase') or "")
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
            if not _has_stable_close_confirmation(res):
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
    if float(res.get('pct_5d', 0) or 0) > 15 and not right_side_quality:
        blockers.append("5日涨幅偏高且质量未确认")
    if _is_h1_first_entry(setup) and not _h1_execution_confirmed(res, sector_alignment):
        blockers.append("H1首次入场仅强主线放量确认可小仓复核")
    if setup_quality == "H2_RAW":
        blockers.append("H2二次入场未满足量能/质量/风险确认，仅观察")

    blockers = _dedupe_trade_blockers(blockers)

    score = float(res.get('final_rank_score', res.get('Score', 0)) or 0)
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
        res['sweet_spot_reason'] = "机会分70-79 + 强联动 + 严格双策略，按专门买点模型复核"
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

    grade = res.get('sop_grade')
    fatal_markers = ("回避", "结构不进入交易池", "异常价格跳变", "板块下跌", "禁止实盘")
    has_fatal_blocker = any(any(marker in b for marker in fatal_markers) for b in blockers)
    # 改动(上班族Bark)：实盘门槛收紧。STRICT_REAL_SIGNAL_GATE=True 时仅 A 级 + 多重共振
    # (🔥核心热点) 判为可交易；B 级降为观察（上班族无暇盯盘纠错，宁缺毋滥）。
    if STRICT_REAL_SIGNAL_GATE:
        trade_eligible = (grade == "A" and not blockers
                          and strategy_type in CORE_TRADE_STRATEGIES
                          and res.get('共振') == "🔥 核心热点"
                          and _trade_quality_confirmed(res, sector_strength, stock_sector_fit))
    else:
        trade_eligible = grade in {"A", "B"} and not blockers and strategy_type in CORE_TRADE_STRATEGIES
    if trade_eligible:
        bucket = "TRADE"
        execution_policy = "BARK_CONFIRMED_TRADE"
    elif early_entry and not has_fatal_blocker:
        bucket = "EARLY"
        execution_policy = "EARLY_REVIEW_ONLY"
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


def _inject_capital_event_risk(results, engine, lookback_days: int = 60):
    """Mark recent financing/unlock/reduction events that can turn into 'good news sold' risk."""
    if not results or engine is None:
        return
    codes = [str(res.get('代码') or '').zfill(6) for res in results if res.get('代码')]
    if not codes:
        return
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    keyword_expr = " OR ".join([f"n.title LIKE :kw{i} OR COALESCE(n.content, '') LIKE :kw{i}" for i, _ in enumerate(CAPITAL_EVENT_KEYWORDS)])
    params = {f"kw{i}": f"%{kw}%" for i, kw in enumerate(CAPITAL_EVENT_KEYWORDS)}
    params.update({"codes": codes, "cutoff": cutoff})
    try:
        stmt = text(f"""
            SELECT DISTINCT s.stock_code, n.title
            FROM news_stocks s
            JOIN news_raw n ON n.id = s.news_id
            WHERE s.stock_code IN :codes
              AND COALESCE(n.publish_time, n.created_at) >= :cutoff
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
    """SOP 过滤引擎：对扫描结果应用硬性条件、一票否决、加分项，生成 A/B/C/D 等级"""
    regime_status = market_regime.get('status', 'UNKNOWN')

    for res in results:
        vetoes = []
        checks = []
        bonuses = []
        risks = []

        # ── 一票否决 ──
        if res.get('影线比', 0) > 0.5:
            vetoes.append("上影线过长")
        if res.get('pct_5d', 0) > 15:
            risks.append("5日涨幅>15%，排序扣分")
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
        _d_regime = {"OFFENSIVE": 100, "DEFENSIVE": 50, "UNKNOWN": 50}.get(regime_status, 0)
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
            res['sweet_spot_reason'] = "机会分70-79 + 强联动 + 严格双策略，按专门买点模型复核"
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

        # ── 评分映射分级 ──
        _hard_vetoes = {"板块下跌", "地雷预警", "破位反抽陷阱", "价格行为建议回避", "价格行为回避",
                        "异常价格跳变", "低质量价格结构", "近期失败模式命中"}
        _has_hard_veto = any(any(hv in v for hv in _hard_vetoes) for v in vetoes)

        if _has_hard_veto:
            grade = "D"
        elif vetoes:
            quality_score -= 15
            risks.append(f"软否决: {', '.join(vetoes[:2])}")
            grade = "D" if quality_score < 30 else "C" if quality_score < 50 else "B" if quality_score < 70 else "A"
        else:
            grade = "A" if quality_score >= 70 else "B" if quality_score >= 50 else "C"
        if quality_score < 30 and grade == "C":
            res['sop_subgrade'] = 'C2'
        elif grade == "C":
            res['sop_subgrade'] = 'C1'

        res['sop_quality_score'] = quality_score
        res['sop_grade'] = grade
        res['sop_vetoes'] = vetoes
        res['sop_checks'] = checks
        res['sop_bonuses'] = bonuses
        res['sop_risks'] = risks
        brooks_adjustment = _brooks_rank_adjustment(res)
        res['brooks_rank_adjustment'] = brooks_adjustment
        res['final_rank_score'] = round(float(res.get('Score') or 0) + brooks_adjustment, 2)
        res['final_rank_score'] = round(res['final_rank_score'] + min(12, max(0, float(res.get('sector_alignment_score') or 0) - 50) * 0.24), 2)
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
        if res.get('pct_5d', 0) > 15:
            res['final_rank_score'] = round(res['final_rank_score'] - 6, 2)
        if brooks_adjustment >= 6:
            bonuses.append("Brooks结构加分")
        if brooks_adjustment <= -6:
            vetoes.append("Brooks风险偏高")
            res['sop_grade'] = "D" if grade in {"C", "D"} else "C"
        if res.get('early_watch_only'):
            res['sop_grade'] = "D" if vetoes else "C"
            res['sop_checks'].append("等待TV-ZP确认")
            res['sop_bonuses'].append("早期异动观察")
        elif res.get('revival_watch_only'):
            res['sop_bonuses'].append("历史信号复活")
            level = res.get('revival_level')
            if level == "MOMENTUM_ACCELERATION":
                res['sop_checks'].append("复活动量加速")
                res['sop_bonuses'].append("连续强势加速")
                if res['sop_grade'] == "C":
                    res['sop_grade'] = "B"
            elif level == "FOLLOW_SMALL":
                res['sop_checks'].append("复活信号可复核")
            elif level == "NEXT_DAY_CONFIRM":
                res['sop_checks'].append("复活信号等次日确认")
                if res['sop_grade'] == "A":
                    res['sop_grade'] = "B"
            else:
                res['sop_checks'].append("复活信号禁止追涨")
                res['sop_grade'] = "D" if vetoes else "C"
        elif res.get('momentum_acceleration_watch_only'):
            res['sop_grade'] = "M"
            res['sop_checks'].append("强趋势加速")
            res['sop_bonuses'].append("涨停/大阳加速观察")
            res['momentum_watch_only'] = True
            res['momentum_watch_reason'] = res.get(
                'momentum_acceleration_reason',
                "强趋势加速，不追买；次日确认后小仓复核",
            )
        elif res.get('sector_watch_only'):
            fatal_vetoes = {"地雷预警", "板块下跌", "板块扩散转弱", "Brooks风险偏高"}
            res['sop_grade'] = "D" if any(v in fatal_vetoes for v in vetoes) else "C"
            res['sop_checks'].append("等待TV买点")
            res['sop_bonuses'].append("板块趋势确认观察")
        elif _is_momentum_watch_candidate(res, vetoes):
            res['sop_grade'] = "M"
            res['sop_checks'].append("动量观察")
            res['sop_bonuses'].append("强势动量观察")
            res['momentum_watch_only'] = True
            res['momentum_watch_reason'] = "涨幅/短线涨幅偏高，不追买；保留观察回踩或次日确认"
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
        leaders = group.sort_values('pct_chg', ascending=False).head(max_per_sector)
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
        elif strategy_type == "early_value":
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
            else:
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
    strategy_type: str = "tv_dual_strict",
    pine_min_signals: int = 3,
    min_data_days: Optional[int] = None,
    weekly_ma_period: int = 20,
    stop_loss_pct: float = BACKTEST_STOP_LOSS_PCT,
    tv_weekly_gate: bool = False,
    require_live_snapshot: bool = False,
) -> List[Dict[str, Any]]:
    """
    Executes the main market scan logic.
    """
    logger.info(f"[RUN_MARKET_SCAN] strategy_type={strategy_type}, min_data_days={min_data_days}, weekly_ma={weekly_ma_period}")
    max_date = None
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
        },
        "version_snapshot": {
            "strategy_logic_version": STRATEGY_LOGIC_VERSION,
            "backtest_engine_version": BACKTEST_ENGINE_VERSION,
            "exit_rule_version": EXIT_RULE_VERSION,
        },
    }

    try:
        # 获取大盘环境以动态调整参数
        regime = get_market_regime()
        reg_status = regime.get("status", "UNKNOWN")
        
        # 启用 REGIME_PARAMS 自适应阈值（改动 #7）：根据大盘状态自动收紧/放宽
        # threshold、vol_multiplier、rsi_min、stop_loss_pct 等。bear 时最严，bull 时最松。
        # 用 SCAN_REGIME_ADAPTIVE 开关控制，便于回退到旧的固定参数。
        if SCAN_REGIME_ADAPTIVE:
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
        # 换手率调整保留：进攻市适度放宽换手要求
        if reg_status == "OFFENSIVE":
            turnover_min = max(2.5, turnover_min - 0.5)
            logger.info(f"[SCAN] Market is OFFENSIVE. Adjusting turnover requirement to {turnover_min}.")

        snapshot_df = pd.DataFrame()
        engine = get_db_engine()

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
                    return []
            except Exception as exc:
                # 预检本身失败不应阻断扫描（降级为告警，保持可用性）
                logger.warning(f"[RUN_MARKET_SCAN] 数据预检执行异常，跳过熔断：{exc}")

        # 1. 如果需要实时行情，或不是强制本地，尝试联网获取快照。
        # 午间/Bark 扫描会传 local_only=True + require_live_snapshot=True；
        # 这种组合必须主动拉实时快照，否则会直接因 snapshot_df 为空而熔断。
        if (require_live_snapshot or not local_only) and data_date is None:
            try:
                snapshot_df = _load_market_snapshot(force_refresh=require_live_snapshot)
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
                        max_date = data_date
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
                            max_date = best_date_res[0]
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
                    snapshot_df = pd.read_sql(query, engine, params={"max_date": max_date})
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
        data_mode = "LOCAL_DB" if max_date else "LIVE_SNAPSHOT"
        snapshot_as_of = (
            max_date or getattr(snapshot_df, "attrs", {}).get("data_date")
            if data_mode == "LOCAL_DB"
            else datetime.now()
        )
        audit_payload.update(_build_snapshot_audit(snapshot_df, data_mode, snapshot_as_of))
        dataset_version = f"{data_mode}:{str(snapshot_as_of)[:19]}"
        audit_payload["version_snapshot"]["dataset_version"] = dataset_version
        audit_payload["point_in_time_snapshot_count"] = save_point_in_time_snapshot(
            snapshot_df, dataset_version, snapshot_as_of, data_mode, engine,
        )
        audit_payload["params_snapshot"]["point_in_time_snapshot_count"] = audit_payload["point_in_time_snapshot_count"]
        if audit_payload["point_in_time_snapshot_count"] <= 0:
            audit_payload["research_only"] = True
            audit_payload.setdefault("degradation_reasons", []).append("点时快照持久化失败")
        mark_phase("market_snapshot_load")

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

        discovery_mask, discovery_pool = _discovery_pool_mask(snapshot_df, strategy_type)
        audit_payload["effective_filters"] = [
            item for item in audit_payload.get("effective_filters", [])
            if item != "positive_pct_change"
        ] + [f"discovery_pool:{discovery_pool}"]
        candidates = snapshot_df[
            discovery_mask &
            is_target_market & is_not_st &
            (~has_mkt_cap | (snapshot_df['mkt_cap'] >= mkt_cap_min * 100000000)) &
            (~has_turnover | (snapshot_df['turnover'] >= turnover_min))
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

        results = []
        engine = get_db_engine()

        # 核心优化：预拉取指数历史并过滤，避免在线程内重复查询和过滤
        bench_df = get_index_hist("000001")
        bench_slice = None
        if not bench_df.empty:
            # 预先过滤出需要的日期范围
            hist_end = datetime.now() if not data_date else datetime.strptime(data_date, "%Y-%m-%d")
            hist_start = hist_end - timedelta(days=365)
            bench_df = bench_df.copy()
            bench_df['日期'] = pd.to_datetime(bench_df['日期'], errors='coerce')
            mask = (bench_df['日期'] >= hist_start) & (bench_df['日期'] <= hist_end)
            bench_slice = bench_df.loc[mask, ['日期', '收盘']].copy()
            logger.info(f"Pre-filtered benchmark data: {len(bench_slice)} points.")

        # 核心优化：批量拉取所有候选标的的历史数据，并进行向量化指标计算
        logger.info(f"Pre-loading historical data for {len(candidates)} candidates in batch...")
        start_time = time.time()
        end_date_hist = datetime.now().strftime("%Y-%m-%d") if not data_date else data_date
        # 深度修复：延长历史数据预热期至 1000 天（约 4 年），以确保 100/200 周期的长效 EMA 完全收敛，精确对齐 TradingView
        start_date_hist = (datetime.strptime(end_date_hist, "%Y-%m-%d") - timedelta(days=1000)).strftime("%Y-%m-%d")
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
            hist_map = {code: group for code, group in master_df.groupby('code')}

        except Exception as e:
            logger.error(f"Batch processing failed: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"数据预处理失败: {str(e)}")

        # 加载基本面数据
        fund_map = {}
        try:
            with engine.connect() as conn:
                fund_res = conn.execute(text("SELECT code, roe, net_profit_yoy, revenue_yoy, label FROM stock_fundamentals")).fetchall()
                for r in fund_res:
                    # 强制使用字符串作为 Key，防止 pandas 类型推断导致 int/str 匹配失败
                    code_key = str(r[0])
                    fund_map[code_key] = {
                        "roe": float(r[1]) if r[1] is not None else 0.0,
                        "net_profit_yoy": float(r[2]) if r[2] is not None else 0.0,
                        "revenue_yoy": float(r[3]) if r[3] is not None else 0.0,
                        "label": str(r[4]) if r[4] is not None else ""
                    }
                logger.info(f"Loaded fundamentals for {len(fund_map)} stocks from database.")
        except Exception as e:
            logger.error(f"Failed to load fundamentals: {e}")

        money_flow_map = _build_scan_money_flow_map(limit=6000)

        # 并发扫描逻辑 - 执行策略筛选和周线确认
        workers = 24  # 向量化后主压力在周线重采样，可提高并发
        logger.info(f"Starting strategy scan for {len(candidates)} stocks (workers={workers})...")

        results = []
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

            logger.info(f"Scan Stats: Matches={len(results)}, Rejections={sum(fail_reasons.values())}")
            audit_payload["fail_reasons"] = fail_reasons
            if fail_reasons:
                logger.info(f"Rejection Summary: {fail_reasons}")

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

        # 排序并取 Top 100
        results = sorted(results, key=lambda x: x['Score'], reverse=True)[:100]

        sector_map = get_sector_map()
        sector_trends = get_sector_trends()
        market_regime = get_market_regime()
        # 把实时快照聚合写入 breadth_history（修复 6/22 节后首日 bug：让后续
        # build_sector_history_context / load_market_cycle_history 读到今日实时宽度，
        # 而非滞后的 daily_k）。失败只 log 不阻断扫描。
        if not snapshot_df.empty and 'pct_chg' in snapshot_df.columns:
            from core.db import record_breadth_snapshot
            record_breadth_snapshot(snapshot_df, sector_map, engine)
        sector_history = build_sector_history_context(engine, sector_map)
        sector_strength = build_sector_strength(snapshot_df, sector_map, sector_trends, sector_history)

        if strategy_type == "early_value":
            results, dropped, kept_pending = _apply_early_value_sector_filter(results, sector_map, sector_strength)
            if dropped:
                logger.info(f"Early value sector-start filter dropped {dropped} candidates.")
            if kept_pending:
                logger.info("Early value sector-start filter found no confirmed sectors; keeping pending watch candidates.")

        if _should_include_sector_watch(strategy_type):
            results = []
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

        active_plan_map: Dict[str, Dict[str, Any]] = {}
        if strategy_type in {"tv_dual", "tv_dual_strict"}:
            existing_codes = {str(res.get('代码', '')).zfill(6) for res in results}
            candidate_codes = [str(code).zfill(6) for code in candidates['code'].tolist()]
            signal_map = _fetch_recent_signal_map(
                engine,
                candidate_codes,
                str(max_date or datetime.now().strftime("%Y-%m-%d")),
            )
            active_plan_map = _fetch_active_execution_plan_map(
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

        # 补充增强 data (行业, 胜率) - 并发处理 Top 100 + 板块观察
        logger.info(f"Parallel supplementing {len(results)} results (WinRate + Industry)...")

        def process_supplement(res):
            try:
                if res.get('sector_watch_only'):
                    return res
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
                    bt = calculate_pine_win_rate(df_labeled, min_signals=pine_min_signals, stop_loss_pct=sl_pct)
                elif strategy_type == "tv_zp":
                    bt = calculate_tv_zp_win_rate(df_labeled, stop_loss_pct=sl_pct)
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
                    )
                elif strategy_type == "both":
                    bt = calculate_pine_win_rate(df_labeled, min_signals=pine_min_signals, stop_loss_pct=sl_pct)
                elif strategy_type == "consensus":
                    bt = calculate_consensus_win_rate(df_labeled, stop_loss_pct=sl_pct)
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
            results = [r for r in results if not r.get('_drop_early_watch')]

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
        if not snapshot_df.empty and 'mkt_cap' in snapshot_df.columns:
            for _, row in snapshot_df.iterrows():
                code = str(row['code'])
                snap_mkt_map[code] = row.get('mkt_cap', 0)
                snap_turnover_map[code] = row.get('turnover', None)
                # 改动 P2：注入 PE（快照含 pe 列，原代码漏注入导致 scan_history.pe 全 None）
                snap_pe_map[code] = row.get('pe', None)
        for res in results:
            code = res['代码']
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
        for res in results:
            sector = res.get('行业', '')
            s_info = sector_trends.get(sector, {})
            res['sector_trend'] = s_info.get('trend', 'UNKNOWN')
            res['sector_pct'] = s_info.get('pct', 0)
            strength = sector_strength.get(sector, {})
            res.update(strength)
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

        # 改动 #17：预查近期失败模式，注入 recent_failure_count 供 _apply_sop_filter 否决
        _inject_failure_pattern(results, engine)
        _inject_breakdown_retracement(results, hist_map)
        _inject_capital_event_risk(results, engine)
        for res in results:
            if res.get('capital_event_risk'):
                warnings = list(res.get('warnings') or [])
                warnings.append("🏦 定增/资本事件")
                res['warnings'] = warnings

        _apply_money_flow_to_results(results, money_flow_map)
        for res in results:
            history = res.get('revival_history')
            if history:
                res.update(_classify_historical_revival(res, history))

        # 应用 SOP 等级评定
        _apply_sop_filter(results, market_regime, sector_trends)
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
            load_market_cycle_history(engine),
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
        audit_payload["version_snapshot"]["score_calibration"] = "cross-strategy-percentile-v1"
        audit_payload["version_snapshot"]["strategy_health_control"] = "expected-return-circuit-breaker-v1"
        audit_payload["version_snapshot"]["decision_layer"] = "market-cycle-mainline-leadership-v2"
        audit_payload["params_snapshot"]["market_sentiment_stage"] = decision_context.get("market_sentiment_stage")
        audit_payload["params_snapshot"]["portfolio_position_cap_pct"] = decision_context.get("portfolio_position_cap_pct")
        logger.info(f"SOP Grades: A={sum(1 for r in results if r.get('sop_grade')=='A')}, "
                    f"B={sum(1 for r in results if r.get('sop_grade')=='B')}, "
                    f"M={sum(1 for r in results if r.get('sop_grade')=='M')}, "
                    f"C={sum(1 for r in results if r.get('sop_grade')=='C')}, "
                    f"D={sum(1 for r in results if r.get('sop_grade')=='D')}")

        # 按 SOP 等级排序: A > B > M > C > D, 同等级内按 Brooks/板块调整后的 Score 排序
        grade_order = {'A': 0, 'B': 1, 'M': 2, 'C': 3, 'D': 4}
        results = sorted(
            results,
            key=lambda x: (
                grade_order.get(x.get('sop_grade', 'D'), 4),
                -float(x.get('trade_opportunity_score') or 0),
                -float(x.get('calibrated_score', x.get('Score', 0)) or 0),
            ),
        )

        # Update Sentinel memory (仅 A/B 级)
        from core.sentinel import sentinel, _select_intraday_push_stocks
        ab_results = [r for r in results if r.get('sop_grade') in ('A', 'B')]
        sentinel.last_top_5 = _select_intraday_push_stocks(results) if results else ab_results[:5]
        mark_phase("scoring_and_decision")

        # --- 持久化保存 ---
        persist_started_at = time.perf_counter()
        scan_data_date = str(max_date) if max_date else datetime.now().strftime("%Y-%m-%d")
        for res in results:
            res['data_date'] = scan_data_date
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
            "message": f"扫描完成！A级{sum(1 for r in results if r.get('sop_grade')=='A')}只 B级{sum(1 for r in results if r.get('sop_grade')=='B')}只"
        })

        # 在结果中注入数据日期
        return results
    except HTTPException as he:
        engine = get_db_engine()
        audit_payload.update({
            "scan_date": str(max_date) if max_date else datetime.now().strftime("%Y-%m-%d"),
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
            "scan_date": str(max_date) if max_date else datetime.now().strftime("%Y-%m-%d"),
            "finished_at": datetime.now(),
            "duration_sec": round((datetime.now() - scan_started_at).total_seconds(), 2),
            "status": "FAILED",
            "error_message": str(e)[:500],
        })
        save_scan_audit_log(audit_payload, engine)
        raise HTTPException(status_code=500, detail=f"扫描执行失败: {str(e)}")
