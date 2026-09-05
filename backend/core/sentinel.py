"""
Alpha Vision - Intraday Sentinel Module.
Handles automated intraday schedule checks, multi-strategy scanning, and push notifications.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import time
import threading
import asyncio
import hashlib
import json

from core.config import config
from core.logging_config import logger
from core.db import get_setting, save_setting
from core.trading_calendar import (
    is_a_share_after_close_sync_window,
    is_a_share_intraday_session,
)
from core.operation_plan import position_health_score, position_size_advice, price_instruction
from core.pa_execution_policy import classify_price_action_execution
from core.risk_constants import (
    A_EOD_MAX_5D_GAIN_PCT,
    A_EOD_MAX_CONCURRENT_POSITIONS,
    A_EOD_T1_MAX_POSITIONS,
    A_EOD_T1_POLICY_VERSION,
    A_EOD_T1_PORTFOLIO_CAP_PCT,
    A_EOD_T1_POSITION_PCT,
    SOP_A_GRADE_STRATEGIES,
)


_last_new_strategy_shadow_fingerprint: Optional[str] = None
_SENTINEL_SCHEDULE_POLICY_VERSION = "half-hour-intraday-v1"
_LEGACY_SENTINEL_SCHEDULE = "14:20"


def _is_executable_candidate(stock: Dict[str, Any]) -> bool:
    stage = str(stock.get("pa_execution_stage") or "")
    stage_ok = not stage or stage == "NEXT_SESSION_EXECUTABLE"
    return stage_ok and stock.get("trade_bucket") == "TRADE" and stock.get("trade_eligible") is True


def _candidate_action_label(stock: Dict[str, Any]) -> str:
    trade_bucket = stock.get('trade_bucket')
    trade_eligible = stock.get('trade_eligible')
    blockers = stock.get('trade_blockers') or []
    if isinstance(blockers, str):
        blockers_text = blockers.strip("[]'\" ")
    else:
        blockers_text = "、".join(str(item) for item in blockers[:2])
    if trade_bucket == 'BLOCK' or trade_eligible is False:
        reason = f"；原因：{blockers_text}" if blockers_text else ""
        return f"尾盘动作：禁止追买，只复盘不交易{reason}"
    if trade_bucket == 'WATCH':
        return "尾盘动作：观察池，等回踩/放量站稳，不在高开或冲高回落时买"

    if stock.get('sector_watch_only'):
        pct = float(stock.get('涨幅%') or stock.get('pct_chg') or 0)
        if pct >= 7:
            return "尾盘动作：板块观察，不追大涨；等回踩或次日TV买点"
        return "尾盘动作：板块趋势观察，出现TV买点或回踩确认再考虑"

    if stock.get("a_eod_controlled_trial") and _is_executable_candidate(stock):
        return "尾盘动作：A-EOD受控小仓，单票不超过5%，合计不超过15%，最多3只"
    if stock.get("a_minus_trial") and _is_executable_candidate(stock):
        return "尾盘动作：A-受控试仓，单票不超过5%，A-合计不超过10%"

    grade = stock.get('sop_grade')
    score = float(stock.get('Score') or stock.get('score') or 0)
    pa_score = float(stock.get('price_action_score') or 0)
    entry_quality = stock.get('price_action_entry_quality') or ''
    sector_trend = stock.get('sector_trend') or ''

    if grade == 'A' and score >= 80 and pa_score >= 70 and sector_trend in ('LEAD', 'FOLLOW'):
        return "尾盘动作：可小仓试买候选，确认不追高"
    if grade == 'A':
        return "尾盘动作：重点观察，突破入场线且站稳再买"
    if grade == 'B' and ('高' in entry_quality or pa_score >= 65):
        return "尾盘动作：轻仓观察，回踩确认优先"
    if grade == 'M':
        return "尾盘动作：动量观察，不追涨停/大阳线，等回踩或次日确认"
    if grade == 'C':
        return "尾盘动作：观察池候选，只跟踪不买入"
    return "尾盘动作：只观察，不追价"


def _candidate_push_bucket(stock: Dict[str, Any]) -> str:
    if stock.get("event_alert_tier") == "STRONG_WATCH":
        return "强势异动"
    if stock.get("sequoia_research_shadow_only"):
        return "SHADOW研究观察"
    if stock.get("tv_reversal_watch_only"):
        return "强势异动"
    if stock.get('sector_watch_only'):
        return "观察"
    if stock.get('bottom_discovery_watch_only'):
        return "观察"
    if _is_executable_candidate(stock):
        return "可交易"
    if stock.get('trade_bucket') == 'BLOCK' or stock.get('trade_eligible') is False:
        return "禁止追买"
    return "观察"


def _load_recommendation_priority_adjustments(days: int = 120) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Load closed-loop adjustment advice for Bark display ranking only."""
    try:
        from routers.review import get_recommendation_outcome_loop

        payload = get_recommendation_outcome_loop(days=days)
        adjustments = payload.get("adjustments") or {}

        def useful(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
            return {
                str(item.get("value") or "UNKNOWN"): item
                for item in items
                if item.get("action") in {"BOOST", "DOWNWEIGHT"} and item.get("score_delta")
            }

        return {
            "source": useful(adjustments.get("by_source") or []),
            "strategy": useful(adjustments.get("by_strategy") or []),
        }
    except Exception as exc:
        logger.debug(f"Recommendation priority adjustment unavailable: {exc}")
        return {"source": {}, "strategy": {}}


def _push_priority_adjustment(
    stock: Dict[str, Any],
    push_source: str,
    adjustments: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    if not adjustments:
        return {"delta": 0, "notes": []}
    source_item = (adjustments.get("source") or {}).get(push_source)
    strategy = str(stock.get("strategy_type") or "UNKNOWN")
    strategy_item = (adjustments.get("strategy") or {}).get(strategy)
    items = [item for item in (source_item, strategy_item) if item]
    delta = sum(int(item.get("score_delta") or 0) for item in items)
    notes = []
    for item in items:
        score_delta = int(item.get("score_delta") or 0)
        if score_delta:
            notes.append(f"{item.get('dimension')} {item.get('value')} {score_delta:+d}")
    return {"delta": delta, "notes": notes}


def _annotate_push_priority(
    stock: Dict[str, Any],
    push_source: str,
    adjustments: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    adjustment = _push_priority_adjustment(stock, push_source, adjustments)
    if not adjustment["delta"]:
        return stock
    annotated = dict(stock)
    annotated["bark_priority_delta"] = adjustment["delta"]
    annotated["bark_priority_note"] = "；".join(adjustment["notes"])
    return annotated


def _candidate_brief_action(stock: Dict[str, Any]) -> str:
    if _is_executable_candidate(stock):
        if stock.get("a_eod_controlled_trial"):
            return "A-EOD受控小仓"
        if stock.get("a_minus_trial"):
            return "A-受控试仓"
        if stock.get("event_trial_trade"):
            return "事件回踩试仓"
        return "可小仓复核"
    if stock.get("bottom_discovery_watch_only"):
        return "起涨预警" if stock.get("bottom_discovery_stage") == "B1_REVERSAL" else "底部观察"
    if stock.get("sequoia_research_shadow_only"):
        return "SHADOW研究观察"
    if stock.get("tv_reversal_watch_only"):
        return "强修复观察"
    if stock.get("pa_execution_stage") == "INTRADAY_PREVIEW":
        return "盘中预观察"
    if stock.get("pa_execution_stage") == "EOD_CONFIRMED":
        return "尾盘确认待次日"
    if stock.get("pa_execution_stage") == "NEXT_SESSION_REVIEW":
        return "次日复核"
    if stock.get("pa_execution_tier") == "PULLBACK_WATCH":
        return "PA回踩观察"
    if stock.get('trade_bucket') == 'EARLY' or stock.get('early_trade_candidate'):
        return "提前复核"
    if stock.get('trade_bucket') == 'BLOCK' or stock.get('trade_eligible') is False:
        if stock.get("event_alert_tier") == "STRONG_WATCH":
            return "强势观察不追高"
        return "禁止买入"
    action = stock.get('pa_trade_action')
    if action == "READY":
        return "等确认"
    if action in {"WATCH", "WAIT"}:
        return "只观察"
    if action == "AVOID":
        return "回避"
    return "只观察"


def _candidate_grade_label(stock: Dict[str, Any]) -> str:
    explicit = str(stock.get("grade_label") or "").strip()
    if explicit:
        return explicit
    if stock.get("bottom_discovery_watch_only"):
        return "B1止跌转强" if stock.get("bottom_discovery_stage") == "B1_REVERSAL" else "B0底部候选"
    if stock.get("sequoia_research_shadow_only"):
        return "SHADOW研究形态"
    if stock.get("tv_reversal_watch_only"):
        return "M级强修复"
    if stock.get("pa_execution_tier") == "PULLBACK_WATCH":
        return "PA回踩观察"
    if stock.get("a_eod_controlled_trial") and _is_executable_candidate(stock):
        return "A-EOD级受控交易"
    if stock.get("a_minus_trial") and _is_executable_candidate(stock):
        return "A-级受控试仓"
    grade = str(stock.get("early_trade_grade") or stock.get("sop_grade") or "?").upper()
    if _is_executable_candidate(stock):
        return f"{grade}级可交易"
    if stock.get("trade_bucket") == "EARLY" or stock.get("early_trade_candidate"):
        return f"{grade}级提前复核"
    return f"{grade}级结构"


def _candidate_brief_reason(stock: Dict[str, Any]) -> str:
    catalyst = stock.get("event_catalyst") or {}
    if stock.get("event_alert_tier") == "STRONG_WATCH" and catalyst:
        low = catalyst.get("profit_growth_low")
        high = catalyst.get("profit_growth_high")
        return f"业绩催化同比+{low:.0f}%~+{high:.0f}%，等待首次可交易回踩"
    if stock.get("pa_close_confirmation_phase") == "INTRADAY_PROVISIONAL":
        return "盘中临时触价，14:30前不视为站稳"
    blockers = stock.get('trade_blockers') or []
    if isinstance(blockers, str):
        text = blockers.strip("[]'\" ")
        return text[:42]
    if blockers:
        return "；".join(str(item) for item in blockers[:2])
    instruction = stock.get("execution_instruction")
    if instruction:
        return str(instruction)[:42]
    if stock.get("pa_pullback_status_label"):
        return str(stock.get("pa_pullback_status_label"))
    return "等待触发价站稳"


def _pullback_status_text(status: Any, label: Any = "") -> str:
    status_text = str(status or "").upper()
    if status_text == "CONFIRMED":
        return "回踩已确认：可小仓复核，不高开追价"
    if status_text == "PENDING_CONFIRMATION":
        return "回踩待确认：支撑暂守住，等放量站上确认价"
    if status_text == "INVALIDATED":
        return "结构失效：跌破失效价，取消买入计划"
    if status_text == "WAITING_PULLBACK":
        return "等待回踩：尚未到健康回踩买点"
    if label:
        return str(label)
    return "等待回踩确认"


def _is_high_extension(stock: Dict[str, Any]) -> bool:
    blockers = stock.get("trade_blockers") or []
    blocker_text = blockers if isinstance(blockers, str) else "；".join(str(item) for item in blockers)
    try:
        pct = float(stock.get("涨幅%") or stock.get("pct_chg") or 0)
    except (TypeError, ValueError):
        pct = 0.0
    try:
        pct_5d = float(stock.get("pct_5d") or 0)
    except (TypeError, ValueError):
        pct_5d = 0.0
    return pct >= 7 or pct_5d >= 15 or "涨幅偏高" in blocker_text or "涨停/近涨停" in blocker_text


def _no_chase_line(stock: Dict[str, Any]) -> Optional[str]:
    if not _is_high_extension(stock):
        return None
    entry = stock.get("entry_price") or stock.get("pa_entry_price") or stock.get("pa_pullback_confirmation_price")
    stop = stock.get("plan_stop_price") or stock.get("stop_price") or stock.get("pa_stop_price")
    entry_text = entry if entry else "--"
    stop_text = stop if stop else "--"
    return f"  追高规则：不追高；等待回踩后重新站上{entry_text}，跌破{stop_text}取消"


def _candidate_brief_lines(stock: Dict[str, Any]) -> List[str]:
    grade_label = _candidate_grade_label(stock)
    name = stock.get('名称', stock.get('name', ''))
    code = stock.get('代码', stock.get('code', ''))
    action = _candidate_brief_action(stock)
    price = stock.get('现价') or stock.get('price')
    plan_state = stock.get("execution_plan_state") or {}
    entry = plan_state.get("active_confirmation_price") or stock.get('entry_price') or stock.get('pa_entry_price')
    stop = stock.get('plan_stop_price') or stock.get('stop_price') or stock.get('pa_stop_price')
    close_guard = stock.get('active_close_guard_price') or stock.get('pa_close_guard_price') or stop
    instruction = "可交易" if _is_executable_candidate(stock) else "不可交易"
    review_state = str(stock.get("execution_review_state") or "")
    review_labels = {
        "TRADE": "执行计划",
        "NEXT_DAY_REVIEW": "次日复核",
        "STRONG_WATCH": "强势观察",
        "OBSERVE": "只观察",
    }
    review_label = review_labels.get(review_state)
    state_text = f"｜{review_label}" if review_label else ""
    lines = [
        f"指令：{instruction}{state_text}｜{action}｜{grade_label} {name}({code})",
        (
            f"价格：现价{price if price else '--'}｜确认>{entry if entry else '--'}"
            f"｜失效<{close_guard if close_guard else '--'}"
        ),
    ]
    if stock.get("market_segment_stage") == "STRUCTURAL_REPAIR":
        lines.append(
            f"环境：{stock.get('market_segment', '成长板块')}结构性强修复"
            f"｜全市场{stock.get('market_regime', '--')}"
        )
    lines.append(f"原因：{_candidate_brief_reason(stock)}")
    return lines


def _intraday_state_fingerprint(stocks: List[Dict[str, Any]], regime: Dict[str, Any]) -> str:
    state = []
    for stock in sorted(stocks, key=lambda item: str(item.get("代码") or item.get("code") or "")):
        blockers = stock.get("trade_blockers") or []
        if isinstance(blockers, str):
            blockers = [blockers]
        state.append({
            "code": stock.get("代码") or stock.get("code"),
            "action": _candidate_brief_action(stock),
            "bucket": stock.get("trade_bucket"),
            "grade": stock.get("grade_stage") or stock.get("sop_grade"),
            "blockers": [str(item) for item in blockers[:2]],
            "health": (stock.get("strategy_health") or {}).get("status"),
            "review_state": stock.get("execution_review_state"),
            "reachability": stock.get("confirmation_reachability"),
        })
    payload = {"regime": regime.get("status"), "stocks": state}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _should_send_intraday_state(fingerprint: str, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    if (
        get_setting("sentinel_bark_state_date") == today
        and get_setting("sentinel_bark_state_fingerprint") == fingerprint
    ):
        return False
    return True


def _mark_intraday_state_sent(fingerprint: str, now: Optional[datetime] = None) -> None:
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    save_setting("sentinel_bark_state_date", today)
    save_setting("sentinel_bark_state_fingerprint", fingerprint)


def _brooks_alert_line(stock: Dict[str, Any]) -> Optional[str]:
    alerts = []
    if stock.get('pa_h2_quality') == '强':
        alerts.append("强H2")
    if stock.get('pa_volume_confirmed'):
        alerts.append(stock.get('pa_volume_pattern') or "量能确认")
    if (stock.get('pa_trap_risk') or 0) >= 75:
        alerts.append(f"陷阱风险{stock.get('pa_trap_risk')}%")
    if stock.get('pa_failed_second_entry') == '失败H2':
        alerts.append("失败H2")
    if stock.get('pa_trend_damage') in {'跌破EMA20', '跌破EMA60', '短线低点破坏'}:
        alerts.append(stock.get('pa_trend_damage'))
    if stock.get('pa_gap_risk', 0) >= 70:
        alerts.append(stock.get('pa_gap_type') or "缺口风险")
    if not alerts:
        summary = stock.get('pa_decision_summary')
        return f"  Brooks: {summary[:44]}..." if summary and len(summary) > 44 else (f"  Brooks: {summary}" if summary else None)
    return f"  Brooks: {' / '.join(alerts[:4])}"


def _pullback_alert_line(stock: Dict[str, Any]) -> Optional[str]:
    label = stock.get("pa_pullback_status_label")
    if not label:
        return None
    status = stock.get("pa_pullback_status")
    support = stock.get("pa_pullback_support_price")
    confirmation = stock.get("pa_pullback_confirmation_price")
    invalidation = stock.get("pa_pullback_invalidation_price")
    prices = []
    if support:
        prices.append(f"支撑{support}")
    if confirmation:
        prices.append(f"确认>{confirmation}")
    if invalidation:
        prices.append(f"失效<{invalidation}")
    translated = _pullback_status_text(status, label)
    return f"  回踩状态: {translated}{' | ' + ' / '.join(prices) if prices else ''}"


def _sector_alignment_line(stock: Dict[str, Any]) -> Optional[str]:
    score = stock.get('sector_momentum_score')
    if score is None:
        return None
    phase_labels = {
        'SECTOR_EARLY': '早期启动',
        'SECTOR_CONFIRM': '趋势确认',
        'SECTOR_CLIMAX': '高潮风险',
        'SECTOR_FADE': '扩散转弱',
        'SECTOR_NEUTRAL': '中性',
    }
    phase = phase_labels.get(stock.get('sector_phase'), stock.get('sector_phase') or '中性')
    breadth = stock.get('sector_breadth')
    alignment = stock.get('sector_alignment_score')
    rank = stock.get('sector_rank')
    pct_3d = stock.get('sector_3d_pct')
    pct_5d = stock.get('sector_5d_pct')
    role_labels = {'LEADER': '龙头', 'CORE': '中军', 'FOLLOWER': '后排', 'LAGGARD': '掉队'}
    role = role_labels.get(stock.get('sector_role'), stock.get('sector_role') or '')
    rank_text = f"#{rank}" if rank else "--"
    breadth_text = f"{breadth:.0f}%" if isinstance(breadth, (int, float)) else "--"
    alignment_text = f"{alignment:.0f}" if isinstance(alignment, (int, float)) else "--"
    role_text = f" | 角色{role}" if role else ""
    trend_text = ""
    if isinstance(pct_3d, (int, float)) and isinstance(pct_5d, (int, float)):
        trend_text = f" | 3日{pct_3d:+.1f}%/5日{pct_5d:+.1f}%"
    return f"  板块联动: {phase} | 强度{score} | 扩散{breadth_text} | 联动{alignment_text} | 排名{rank_text}{role_text}{trend_text}"


def _sector_watch_advice_lines(stock: Dict[str, Any]) -> List[str]:
    """Bark advice for sector-confirmed stocks without a TV buy signal yet."""
    reason = stock.get('sector_watch_reason') or stock.get('reason') or "板块趋势确认，但个股买点未触发"
    entry = stock.get('entry_price') or stock.get('pa_entry_price')
    stop = stock.get('plan_stop_price') or stock.get('stop_price')
    pct = float(stock.get('涨幅%') or 0)

    lines = [f"  观察原因: {reason}"]
    if pct >= 7:
        lines.append("  操作建议: 今日只跟踪，不追高；等待回踩不破结构位或次日TV买点")
    else:
        lines.append("  操作建议: 先放观察池；14:40后若放量站上触发价且未破失效位，再考虑轻仓")
    if entry or stop:
        entry_text = entry if entry else "--"
        stop_text = stop if stop else "--"
        lines.append(f"  条件触发: 站上/回踩确认 {entry_text} | 失效 {stop_text}")
    return lines


def _position_action_label(signal: Dict[str, str] | None, pl_pct: float, stop_buffer: float | None) -> str:
    if signal:
        level = signal.get('level')
        reason = signal.get('reason', '')
        if level == 'critical':
            return f"尾盘动作：建议平仓/人工确认卖出；原因：{reason}"
        if level == 'warning':
            return f"尾盘动作：建议减仓或收紧风控；原因：{reason}"
    if stop_buffer is not None and stop_buffer < 2:
        return f"尾盘动作：贴近风控线，仅持有不加仓；缓冲 {stop_buffer:.1f}%"
    if pl_pct > 5:
        return "尾盘动作：继续持有，盈利仓不追高加仓"
    if pl_pct > 0:
        return "尾盘动作：持有观察，等放量突破再考虑加仓"
    return "尾盘动作：弱势持有，不加仓，跌破执行风控需处理"


def _position_breakout_confirmation(
    curr: float,
    entry: float,
    active_stop: float,
    pa: Dict[str, Any],
    df_hist,
) -> Dict[str, Any]:
    """Build a concrete add-on checklist for profitable real positions."""
    if df_hist is None or df_hist.empty or len(df_hist) < 21:
        return {}

    prior_high = float(df_hist["最高"].iloc[-21:-1].max())
    avg_volume_20 = float(df_hist["成交量"].iloc[-21:-1].mean())
    last = df_hist.iloc[-1]
    open_price = float(last.get("开盘") or 0)
    high_price = float(last.get("最高") or 0)
    low_price = float(last.get("最低") or 0)
    close_price = float(last.get("收盘") or curr)
    last_volume = float(last.get("成交量") or 0)
    trigger = float(pa.get("pa_entry_price") or prior_high or 0)
    if trigger <= 0:
        return {}

    volume_ratio_threshold = float(pa.get("pa_breakout_volume_threshold") or 1.4)
    volume_threshold = avg_volume_20 * volume_ratio_threshold if avg_volume_20 > 0 else 0
    bar_range = max(high_price - low_price, 0.01)
    close_position = (close_price - low_price) / bar_range
    upper_shadow_pct = max(0.0, high_price - max(open_price, close_price)) / max(close_price, 0.01) * 100
    guard = max(active_stop or 0, entry or 0, trigger * 0.985)

    price_ok = curr >= trigger and close_price >= trigger
    volume_ok = volume_threshold > 0 and last_volume >= volume_threshold
    close_ok = close_price > open_price and close_position >= 0.6 and upper_shadow_pct < 3
    confirmed = price_ok and volume_ok and close_ok

    return {
        "trigger": round(trigger, 2),
        "guard": round(guard, 2),
        "volume_threshold": int(volume_threshold) if volume_threshold > 0 else 0,
        "volume_ratio_threshold": round(volume_ratio_threshold, 2),
        "volume_ratio": round(last_volume / volume_threshold, 2) if volume_threshold > 0 else 0,
        "price_ok": price_ok,
        "volume_ok": volume_ok,
        "close_ok": close_ok,
        "confirmed": confirmed,
    }


def _position_price_instruction(
    active_stop: float,
    structure_stop: float,
    breakout_plan: Dict[str, Any],
    pl_pct: float,
) -> str:
    return price_instruction(
        trigger=float(breakout_plan.get("trigger") or 0),
        guard=float(breakout_plan.get("guard") or 0),
        active_stop=active_stop,
        structure_stop=structure_stop,
        confirmed=bool(breakout_plan.get("confirmed")),
        profitable=pl_pct > 0,
    )


def _select_intraday_push_stocks(
    stock_list: List[Dict[str, Any]],
    executable_limit: int = 5,
    sector_watch_limit: int = 3,
    priority_adjustments: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
    push_source: str = "bark",
) -> List[Dict[str, Any]]:
    """Keep the interruptive Bark channel for executable candidates only."""
    grade_order = {'A': 0, 'A-EOD': 1, 'A-': 2, 'B': 3, 'M': 4, 'C': 5, 'D': 6, '?': 7}

    def rank_key(stock: Dict[str, Any]):
        adjustment = _push_priority_adjustment(stock, push_source, priority_adjustments)
        effective_score = float(stock.get('final_rank_score', stock.get('Score', 0)) or 0) + float(adjustment["delta"] or 0)
        return (
            grade_order.get(
                'A-EOD' if stock.get('a_eod_controlled_trial')
                else 'A-' if stock.get('a_minus_trial')
                else stock.get('sop_grade', '?'),
                7,
            ),
            -effective_score,
        )

    executable = [
        s for s in stock_list
        if (
            not s.get('sector_watch_only')
            and s.get('sop_grade') in ('A', 'B', 'M', 'C')
            and _is_executable_candidate(s)
        )
    ]
    selected: List[Dict[str, Any]] = []
    seen_codes = set()
    a_minus_picked = 0
    a_eod_picked = 0
    for stock in sorted(executable, key=rank_key):
        code = stock.get('代码') or stock.get('code')
        if code in seen_codes:
            continue
        if stock.get("a_minus_trial") and a_minus_picked >= 2:
            continue
        if stock.get("a_eod_controlled_trial") and a_eod_picked >= A_EOD_MAX_CONCURRENT_POSITIONS:
            continue
        selected.append(_annotate_push_priority(stock, push_source, priority_adjustments))
        seen_codes.add(code)
        if stock.get("a_minus_trial"):
            a_minus_picked += 1
        if stock.get("a_eod_controlled_trial"):
            a_eod_picked += 1
        if len(selected) >= executable_limit:
            break
    return selected


def _select_after_close_watchlist(
    stock_list: List[Dict[str, Any]],
    limit: int = 5,
    priority_adjustments: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
    push_source: str = "bark_next_day",
) -> List[Dict[str, Any]]:
    """Select next-day observation candidates without presenting blocked stocks as opportunities."""
    grade_order = {'A': 0, 'B': 1, 'M': 2, 'C': 3, 'D': 4}
    t1_plans = [
        _annotate_a_eod_t1_plan(stock)
        for stock in stock_list
        if _is_a_eod_t1_plan_candidate(stock)
    ]
    t1_plans = sorted(
        t1_plans,
        key=lambda stock: -float(
            stock.get('final_trade_score', stock.get('trade_opportunity_score', stock.get('Score', 0))) or 0
        ),
    )[:A_EOD_T1_MAX_POSITIONS]
    t1_codes = {str(stock.get('代码') or stock.get('code') or '') for stock in t1_plans}
    strict_codes = {
        str(stock.get('代码') or stock.get('code') or '')
        for stock in stock_list
        if str(stock.get('strategy_type') or '') == 'tv_dual_strict'
    }
    candidates = [
        stock for stock in stock_list
        if (
            (
                stock.get('sop_grade') in {'A', 'B', 'M', 'C'}
                or float(stock.get('trade_opportunity_score') or 0) >= 60
            )
            and stock.get('trade_bucket') != 'BLOCK'
            and not stock.get('sector_watch_only')
            and not stock.get('sequoia_research_shadow_only')
            and not (
                str(stock.get('strategy_type') or '') == 'tv_dual'
                and str(stock.get('代码') or stock.get('code') or '') in strict_codes
            )
            and str(stock.get('代码') or stock.get('code') or '') not in t1_codes
        )
    ]
    ordinary = sorted(
        candidates,
        key=lambda stock: (
            grade_order.get(stock.get('sop_grade'), 4),
            -(
                float(stock.get('final_trade_score', stock.get('Score', 0)) or 0)
                + float(_push_priority_adjustment(stock, push_source, priority_adjustments)["delta"] or 0)
            ),
        ),
    )[:max(0, limit - len(t1_plans))]
    selected = [*t1_plans, *ordinary][:limit]
    return [_annotate_push_priority(stock, push_source, priority_adjustments) for stock in selected]


def _is_a_eod_t1_plan_candidate(stock: Dict[str, Any]) -> bool:
    """Signal-day gate for a supported TV execution strategy."""
    if (
        stock.get('sector_watch_only')
        or stock.get('tv_reversal_watch_only')
        or stock.get('bottom_discovery_watch_only')
        or stock.get('sequoia_research_shadow_only')
        or str(stock.get('strategy_type') or '') not in SOP_A_GRADE_STRATEGIES
    ):
        return False
    pa_execution = classify_price_action_execution(stock)
    try:
        pct_5d = float(stock.get('pct_5d'))
        entry = float(stock.get('pa_entry_price') or stock.get('entry_price') or 0)
        stop = float(stock.get('pa_stop_price') or stock.get('plan_stop_price') or 0)
    except (TypeError, ValueError):
        return False
    vetoes = stock.get('sop_vetoes') or []
    if isinstance(vetoes, str):
        vetoes = [vetoes] if vetoes.strip("[]'\" ") else []
    market_stage = str(stock.get('market_sentiment_stage') or '').upper()
    sector_phase = str(stock.get('sector_phase') or '').upper()
    sector_mainline = str(stock.get('sector_mainline') or '').upper()
    return bool(
        pa_execution['tier'] in {'NORMAL', 'T1_CONFIRM'}
        and not pa_execution['hard_blocked']
        and pct_5d <= A_EOD_MAX_5D_GAIN_PCT
        and entry > stop > 0
        and not vetoes
        and market_stage not in {'RETREAT', 'ICE'}
        and sector_phase != 'SECTOR_FADE'
        and sector_mainline != 'FADING'
    )


def _annotate_a_eod_t1_plan(stock: Dict[str, Any]) -> Dict[str, Any]:
    planned = dict(stock)
    pa_execution = classify_price_action_execution(stock)
    planned.update({
        'a_eod_t1_plan': True,
        'a_eod_t1_policy_version': A_EOD_T1_POLICY_VERSION,
        'a_eod_t1_frozen_entry_price': stock.get('pa_entry_price') or stock.get('entry_price'),
        'a_eod_t1_frozen_stop_price': stock.get('pa_stop_price') or stock.get('plan_stop_price'),
        'a_eod_t1_frozen_target_price': stock.get('pa_target_price') or stock.get('target_price'),
        'a_eod_t1_position_pct': A_EOD_T1_POSITION_PCT,
        'a_eod_t1_portfolio_cap_pct': A_EOD_T1_PORTFOLIO_CAP_PCT,
        'a_eod_t1_max_positions': A_EOD_T1_MAX_POSITIONS,
        'pa_execution_policy_version': pa_execution['version'],
        'pa_execution_tier': pa_execution['tier'],
        'pa_execution_tier_label': pa_execution['label'],
        'execution_review_state': 'NEXT_DAY_REVIEW',
        'trade_eligible': False,
        'trade_bucket': 'OBSERVE',
    })
    return planned


def _after_close_price(stock: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = stock.get(key)
        if value not in (None, "", 0):
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                continue
    return None


def _sector_push_gap_lines(gap_items: Optional[List[Dict[str, Any]]], limit: int = 3) -> List[str]:
    if not gap_items:
        return []
    gaps = [item for item in gap_items if not item.get("has_push_candidate")]
    if not gaps:
        return []
    lines = ["热门板块未推原因："]
    for item in gaps[:limit]:
        industry = item.get("industry") or "--"
        reason = item.get("primary_reason_label") or "继续观察"
        score = item.get("sector_momentum_score", "--")
        candidate_count = item.get("scan_candidate_count", 0)
        lines.append(f"  {industry}：{reason} | 板块分 {score} | 候选 {candidate_count}只")
    lines.append("")
    return lines


def _build_after_close_watchlist_body(
    stock_list: List[Dict[str, Any]],
    scan_date: str,
    sector_gap_analysis: Optional[List[Dict[str, Any]]] = None,
) -> str:
    first = stock_list[0] if stock_list else {}
    lines = [
        f"数据日期：{scan_date}",
        f"市场：{first.get('market_sentiment_label', '--')} | 情绪分 {first.get('market_sentiment_score', '--')} | 总仓上限 {first.get('portfolio_position_cap_pct', '--')}%",
        "性质：次日观察清单，不是买入指令；高开或冲高时不追价。",
        "执行：次日仅在触发价上方站稳且量能确认后复核，跌破失效价立即取消计划。",
        "",
    ]
    lines.extend(_sector_push_gap_lines(sector_gap_analysis))
    for stock in stock_list:
        name = stock.get('名称') or stock.get('name') or ''
        code = stock.get('代码') or stock.get('code') or ''
        grade_label = _candidate_grade_label(stock)
        current = _after_close_price(stock, '现价', 'price')
        entry = _after_close_price(stock, 'a_eod_t1_frozen_entry_price', 'entry_price', 'pa_entry_price')
        stop = _after_close_price(stock, 'a_eod_t1_frozen_stop_price', 'plan_stop_price', 'stop_price', 'pa_stop_price', 'pa_pullback_invalidation_price')
        blockers = stock.get('trade_blockers') or []
        if isinstance(blockers, str):
            blocker_text = blockers.strip("[]'\" ")
        else:
            blocker_text = "、".join(str(item) for item in blockers[:2])

        price_label = stock.get("after_close_price_label") or "最近快照"
        if stock.get('a_eod_t1_plan'):
            heading = "A-EOD-T1｜次日计划"
        elif stock.get('pa_execution_tier') == 'PULLBACK_WATCH':
            heading = "PA回踩观察｜不可交易"
        else:
            heading = f"{grade_label}｜等待确认"
        lines.append(f"【{heading}】{name} ({code}) | {price_label} {current if current else '--'}")
        segment_text = (
            f" | {stock.get('market_segment')}结构性强修复"
            if stock.get("market_segment_stage") == "STRUCTURAL_REPAIR" else ""
        )
        lines.append(
            f"  定位：{stock.get('sector_mainline', '--')} / {stock.get('sector_role', '--')}"
            f" | 机会分(0-100) {stock.get('display_opportunity_score', stock.get('trade_opportunity_score', '--'))}"
            f" | {stock.get('trade_opportunity_label', '观望')}{segment_text}"
        )
        lines.append(
            f"  关键价：现价 {current if current else '--'} | 确认 >{entry if entry else '--'}"
            f" | 有效失效 <{stop if stop else '--'}"
        )
        if stock.get('a_eod_t1_plan'):
            lines.append(f"  确认：次日触发且现价守住 >{entry if entry else '--'}，再由 Bark 明确发出可交易")
        elif stock.get('pa_execution_tier') == 'PULLBACK_WATCH':
            lines.append("  确认：等待回踩结构重新转强且PA升至60分以上，再生成新的次日计划")
        else:
            lines.append(f"  确认：站稳 >{entry if entry else '--'} 且量能确认，再考虑小仓复核")
        lines.append(f"  失效：跌破 <{stop if stop else '--'}，取消观察/不得买入")
        if stock.get('a_eod_t1_plan'):
            lines.append(
                f"  限制：仅下一交易日有效；高开偏离确认价不超过3%；"
                f"确认后单票≤{stock.get('a_eod_t1_position_pct', A_EOD_T1_POSITION_PCT):g}%、"
                f"合计≤{stock.get('a_eod_t1_portfolio_cap_pct', A_EOD_T1_PORTFOLIO_CAP_PCT):g}%"
            )
        lines.append(f"  当前：等待确认，不追高{f'；原因：{blocker_text}' if blocker_text else ''}")
        if stock.get("bark_priority_note"):
            lines.append(f"  闭环调权：{stock['bark_priority_note']}，仅影响推送排序")
        chase_line = _no_chase_line(stock)
        if chase_line:
            lines.append(chase_line)
        if stock.get("execution_instruction"):
            lines.append(f"  指令：{stock['execution_instruction']}")
        brooks_line = _brooks_alert_line(stock)
        if brooks_line:
            lines.append(brooks_line)
        pullback_line = _pullback_alert_line(stock)
        if pullback_line:
            lines.append(pullback_line)
        lines.append("")
    return "\n".join(lines).rstrip()


def _attach_official_close_prices(
    stock_list: List[Dict[str, Any]], engine, scan_date: str,
) -> List[Dict[str, Any]]:
    """Use finalized daily close when available; otherwise label the value as a snapshot."""
    copied = [{**stock, "after_close_price_label": "最近快照"} for stock in stock_list]
    if engine is None or not copied:
        return copied
    codes = [str(stock.get("代码") or stock.get("code") or "") for stock in copied]
    try:
        from sqlalchemy import bindparam, text
        query = text("""
            SELECT code, close FROM daily_k
            WHERE date = :scan_date AND code IN :codes AND close > 0
        """).bindparams(bindparam("codes", expanding=True))
        with engine.connect() as conn:
            closes = {str(row["code"]): float(row["close"]) for row in conn.execute(
                query, {"scan_date": scan_date, "codes": codes},
            ).mappings()}
    except Exception as exc:
        logger.warning(f"Official close refresh skipped: {exc}")
        return copied
    for stock in copied:
        code = str(stock.get("代码") or stock.get("code") or "")
        if code in closes:
            stock["现价"] = closes[code]
            stock["price"] = closes[code]
            stock["after_close_price_label"] = "正式收盘"
    return copied


def send_after_close_watchlist(
    stock_list: List[Dict[str, Any]],
    scan_date: Optional[str] = None,
    now: Optional[datetime] = None,
    limit: int = 5,
) -> Optional[str]:
    """Push one actionable next-day observation list after a same-day close scan."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    scan_date = scan_date or (stock_list[0].get('data_date') if stock_list else None)
    if not stock_list or not is_a_share_after_close_sync_window(now) or scan_date != today:
        return None
    if get_setting("after_close_watchlist_last_date") == today:
        logger.info("Sentinel: after-close watchlist already pushed today.")
        return None

    priority_adjustments = _load_recommendation_priority_adjustments()
    selected = _select_after_close_watchlist(
        stock_list,
        limit=min(limit, 3),
        priority_adjustments=priority_adjustments,
        push_source="bark_next_day",
    )

    from core.db import get_db_engine
    engine = get_db_engine()
    selected = _attach_official_close_prices(selected, engine, today)
    group_counts = {
        "FORMAL": sum(str(item.get("result_group") or "FORMAL") == "FORMAL" for item in stock_list),
        "HISTORICAL_REVIVAL": sum(item.get("result_group") == "HISTORICAL_REVIVAL" for item in stock_list),
        "MOMENTUM_WATCH": sum(item.get("result_group") == "MOMENTUM_WATCH" for item in stock_list),
    }
    trade_count = sum(_is_executable_candidate(item) for item in stock_list)
    lines = [
        f"数据：{today}正式收盘",
        (
            f"结果：正式{group_counts['FORMAL']}｜复活{group_counts['HISTORICAL_REVIVAL']}｜"
            f"动量{group_counts['MOMENTUM_WATCH']}｜可交易{trade_count}"
        ),
        "纪律：只有后续Bark明确显示“可交易”才执行；其他均不买。",
    ]
    if selected:
        lines.append(f"次日复核（合并摘要，Top {len(selected)}）：")
        for stock in selected:
            name = stock.get("名称") or stock.get("name") or "--"
            code = stock.get("代码") or stock.get("code") or "--"
            current = _after_close_price(stock, "现价", "price")
            entry = _after_close_price(stock, "a_eod_t1_frozen_entry_price", "entry_price", "pa_entry_price")
            stop = _after_close_price(stock, "a_eod_t1_frozen_stop_price", "plan_stop_price", "stop_price", "pa_stop_price")
            tag = "T+1计划" if stock.get("a_eod_t1_plan") else "观察"
            lines.append(
                f"- {name}({code}) {tag}｜收{current or '--'}｜确认>{entry or '--'}｜失效<{stop or '--'}"
            )
    else:
        lines.append("次日复核：无。")
    body = "\n".join(lines)
    try:
        from core.db import save_recommendation_events
        save_recommendation_events(
            selected,
            engine=engine,
            source="bark_next_day",
            event_date=today,
        )
    except Exception as exc:
        logger.warning(f"After-close recommendation event persistence skipped: {exc}")

    # Measurement is independent from delivery: failed Bark attempts must not
    # disappear from the point-in-time performance sample.
    try:
        from core.signal_performance import save_intraday_signal_snapshots
        save_intraday_signal_snapshots(selected, engine, source="bark_next_day", signal_time=now)
    except Exception as exc:
        logger.warning(f"After-close signal snapshot persistence skipped: {exc}")

    title = f"Alpha Vision 收盘决策摘要 {today}"
    from core.notifier import split_message_body
    parts = split_message_body(body)
    deliveries = [
        _send_bark_message(
            f"{title} ({index}/{len(parts)})" if len(parts) > 1 else title,
            part,
        )
        for index, part in enumerate(parts, start=1)
    ]
    if not all(deliveries):
        return None
    save_setting("after_close_watchlist_last_date", today)
    return body


def _send_bark_message(title: str, body: str, *, enqueue_failed: bool = True) -> bool:
    logger.info(f"Notification: {body}")

    from core.notifier import notifier
    try:
        result = asyncio.run(notifier.send(
            title, body, channels=["bark"], enqueue_failed=enqueue_failed,
        ))
        return bool(result.get("bark"))
    except Exception as e:
        logger.error(f"Push notification failed: {e}")
        return False


def _real_position_action(
    curr: float,
    entry: float,
    high_since_entry: float,
    risk: Dict[str, Any],
    pa: Dict[str, Any],
    signals: List[Dict[str, Any]],
    df_hist,
) -> str:
    """Create actionable real-position advice for end-of-day Bark checks."""
    pl_pct = (curr - entry) / entry * 100 if entry > 0 else 0
    max_pl_pct = (high_since_entry - entry) / entry * 100 if entry > 0 else 0
    giveback_pct = max_pl_pct - pl_pct
    active_stop = risk.get("active_stop_price") or risk.get("stop_price") or 0
    structure_stop = risk.get("structure_stop_price") or risk.get("initial_stop_price") or active_stop
    latest_low = None
    upper_shadow_pct = 0.0

    if df_hist is not None and not df_hist.empty:
        last = df_hist.iloc[-1]
        open_price = float(last.get("开盘") or 0)
        high_price = float(last.get("最高") or 0)
        low_price = float(last.get("最低") or 0)
        close_price = float(last.get("收盘") or curr)
        latest_low = low_price if low_price > 0 else None
        if close_price > 0 and high_price > 0:
            upper_shadow_pct = max(0.0, high_price - max(open_price, close_price)) / close_price * 100

    stop_buffer = ((curr - active_stop) / curr * 100) if curr > 0 and active_stop > 0 else None
    trap_risk = float(pa.get("pa_trap_risk") or 0)
    volume_pattern = pa.get("pa_volume_pattern") or ""
    trend_damage = pa.get("pa_trend_damage") or ""
    plan = pa.get("pa_trade_plan") or {}
    pa_action = plan.get("action") or pa.get("pa_trade_action") or ""
    eight_rule = pa.get("pa_eight_rule_primary") or {}
    breakout_plan = _position_breakout_confirmation(curr, entry, active_stop, pa, df_hist)

    if signals:
        sig = signals[0]
        icon = "🚨" if sig.get('level') == 'critical' else "⚠️"
        action = _position_action_label(sig, pl_pct, stop_buffer)
    elif active_stop and curr <= active_stop:
        icon = "🚨"
        action = "跌破动态风控线，尾盘优先减仓保护本金"
    elif pa_action == "AVOID" and trap_risk >= 75 and pl_pct <= 0:
        icon = "⚠️"
        action = "价格行为转弱且高陷阱风险，尾盘不加仓，考虑降到观察仓"
    elif eight_rule.get("direction") == "RISK" and float(eight_rule.get("confidence") or 0) >= 80:
        icon = "⚠️"
        action = f"八诀风险触发：{eight_rule.get('label')}，停止加仓并复核减仓"
    elif giveback_pct >= 5 and upper_shadow_pct >= 3:
        icon = "⚠️"
        action = "冲高回落明显，先锁定部分仓位，剩余按风控线持有"
    elif breakout_plan.get("confirmed") and pl_pct > 0:
        icon = "🟢"
        action = "放量突破已确认，可小幅加仓；跌回突破价/动态线撤回加仓计划"
    elif pl_pct > 0 and stop_buffer is not None and stop_buffer >= 6:
        icon = "✅"
        action = "持有观察，盈利单用动态止盈线跟踪"
    else:
        icon = "✅"
        action = _position_action_label(None, pl_pct, stop_buffer)

    key_prices = []
    if entry > 0:
        key_prices.append(f"成本{entry:.2f}")
    if latest_low:
        key_prices.append(f"当日低点{latest_low:.2f}")
    if active_stop:
        key_prices.append(f"动态线{active_stop:.2f}")
    if structure_stop and structure_stop != active_stop:
        key_prices.append(f"结构线{structure_stop:.2f}")

    extras = []
    if breakout_plan:
        ratio_text = breakout_plan.get("volume_ratio_threshold") or 1.4
        volume_text = f"量≥均量{ratio_text}x({breakout_plan['volume_threshold']})" if breakout_plan.get("volume_threshold") else f"量≥20日均量{ratio_text}x"
        status_bits = []
        if breakout_plan.get("price_ok"):
            status_bits.append("价✓")
        if breakout_plan.get("volume_ok"):
            status_bits.append("量✓")
        if breakout_plan.get("close_ok"):
            status_bits.append("收✓")
        status_text = f"({','.join(status_bits)})" if status_bits else ""
        extras.append(
            f"加仓确认: 站上{breakout_plan['trigger']:.2f}/{volume_text}/守{breakout_plan['guard']:.2f}{status_text}"
        )
        size = position_size_advice(entry, breakout_plan.get("guard"), breakout_plan.get("trigger"), bool(breakout_plan.get("confirmed")))
        extras.append(f"仓位: {size['label']}≤{size['max_add_pct']}%")
    if trend_damage in {"跌破EMA20", "跌破EMA60", "短线低点破坏"}:
        extras.append(f"Brooks:{trend_damage}")
    elif pa.get("pa_failed_second_entry") == "失败H2":
        extras.append("Brooks:失败H2")
    elif trap_risk >= 75:
        extras.append(f"陷阱风险{trap_risk:.0f}%")
    if volume_pattern in {"放量失败突破", "缩量阴跌"}:
        extras.append(volume_pattern)
    if eight_rule:
        trigger = eight_rule.get("trigger_price")
        invalidation = eight_rule.get("invalidation_price")
        price_text = f">{trigger}" if eight_rule.get("direction") == "BULLISH" and trigger else f"<{invalidation}" if invalidation else ""
        extras.append(f"八诀:{eight_rule.get('label')} {price_text}".strip())
    health = position_health_score(
        pl_pct=pl_pct,
        stop_buffer_pct=stop_buffer or 0,
        trap_risk=trap_risk,
        trend_damage=trend_damage,
        volume_pattern=volume_pattern,
        confirmed_breakout=bool(breakout_plan.get("confirmed")),
    )
    extras.append(f"持仓评分: {health['score']} {health['label']}")
    instruction = _position_price_instruction(active_stop, structure_stop, breakout_plan, pl_pct)
    if instruction:
        extras.append(f"指令: {instruction}")

    tail = f" | 关键价: {' / '.join(key_prices)}" if key_prices else ""
    extra_text = f" | {' / '.join(extras[:5])}" if extras else ""
    return f"{icon} {action}{tail}{extra_text}"


def _append_real_position_status(lines: List[str]) -> None:
    """Append REAL open-position status and action advice to a Bark message."""
    from sqlalchemy import text
    from core.db import get_db_engine

    engine = get_db_engine()
    if not engine:
        return

    import pandas as pd
    from core.data import get_market_snapshot, get_index_hist, format_freshness
    from core.indicators import calculate_indicators
    from core.strategy import evaluate_exit_signals
    from core.risk_engine import compute_paper_risk_levels_with_context, safe_float
    from core.price_action import analyze_price_action

    try:
        df_real = pd.read_sql(
            "SELECT code, name, entry_price, high_since_entry, signal_sources FROM paper_trading WHERE status = 'OPEN' AND trade_mode = 'REAL'",
            engine,
        )
        if df_real.empty:
            return

        lines.append("────────────────")
        lines.append("【🔴 实盘持仓状态】")

        snapshot = get_market_snapshot()
        # P1：实盘持仓块顶部标注行情新鲜度（滞后/stale 时带 ⚠️，让用户知道现价可信度）
        lines.append(format_freshness(snapshot))
        snap_map = snapshot.set_index('code')['price'].to_dict() if not snapshot.empty else {}
        snap_high_map = snapshot.set_index('code')['high'].to_dict() if not snapshot.empty and 'high' in snapshot.columns else {}
        bench_df = get_index_hist('000001')

        for _, row in df_real.iterrows():
            code = row['code']
            name = row['name']
            entry = safe_float(row['entry_price'])
            high = safe_float(row.get('high_since_entry'), entry)

            curr = snap_map.get(code)
            if not curr:
                lines.append(f"• {name}: 暂无行情")
                continue
            high = max(high, safe_float(snap_high_map.get(code), curr), curr)

            pl_pct = (curr - entry) / entry * 100
            status_line = f"• {name}: 现价 {curr} ({pl_pct:+.2f}%)"

            query = text("SELECT date as \"日期\", close as \"收盘\", open as \"开盘\", high as \"最高\", low as \"最低\", vol as \"成交量\" FROM daily_k WHERE code = :code ORDER BY date DESC LIMIT 260")
            with engine.connect() as conn:
                df_hist = pd.read_sql(query, conn, params={'code': code})
                df_hist = df_hist.sort_values('日期')

            suggestion = "持股观望"
            if len(df_hist) >= 20:
                df_labeled = calculate_indicators(df_hist, current_price=curr, bench_df=bench_df)
                signals = evaluate_exit_signals(
                    df_labeled,
                    entry,
                    high,
                    code=code,
                    signal_sources=str(row.get("signal_sources") or "").split("+") if row.get("signal_sources") else None,
                    close_confirmed=datetime.now().hour >= 15,
                )
                risk = compute_paper_risk_levels_with_context(entry, high, curr, None, code)
                pa = analyze_price_action(df_hist)
                active_stop = risk.get("active_stop_price") or risk.get("stop_price") or 0
                stop_buffer = ((curr - active_stop) / curr * 100) if curr > 0 and active_stop > 0 else None
                suggestion = _real_position_action(curr, entry, high, risk, pa, signals, df_hist)

            lines.append(status_line)
            lines.append(f"   └ {suggestion}")
        lines.append("")
    except Exception as e:
        logger.error(f"Error appending real stock status: {e}")


_REGIME_EMOJI = {"OFFENSIVE": "🚀 进攻模式", "DEFENSIVE": "⚠️ 防守模式", "CRITICAL": "🛡️ 严格防守"}


def _first_market_context(stock_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    for stock in stock_list or []:
        if stock.get("market_sentiment_label") or stock.get("market_sentiment_stage"):
            return stock
    return {}


def _format_regime_line(regime: Dict[str, Any]) -> str:
    """拼成 '🚀 进攻模式 | 上证 -1.37% / 创业 -3.84% | 跌停39家'。
    regime 判定基于 EMA20 趋势（慢变量）+ 市场宽度（跌停家数）降级；
    追加当日涨跌幅与跌停家数，让文案对结构性行情（指数失真但个股惨烈）有感知。"""
    status = regime.get("status", "")
    indices = regime.get("indices") or {}
    parts = []
    sharp_drop = False
    for name in ("上证", "创业"):
        chg = (indices.get(name) or {}).get("chg_pct")
        if chg is not None:
            sharp_drop = sharp_drop or float(chg) <= -1.0
            parts.append(f"{name} {chg:+.2f}%")
    if status == "OFFENSIVE" and sharp_drop:
        base = "⚠️ 指数急跌"
    else:
        base = _REGIME_EMOJI.get(status, "❓ 未知")
    tail = (" | " + " / ".join(parts)) if parts else ""
    limit_down = regime.get("limit_down_count")
    if isinstance(limit_down, int):
        tail += f" | 跌停{limit_down}家"
    return f"{base}{tail}"


def _format_market_line(stock_list: List[Dict[str, Any]], regime: Dict[str, Any]) -> str:
    """Use the scan decision context for Bark market wording when available.

    The regime model is a slow EMA20 trend label. The scan decision layer is the
    execution gate used for position sizing, so Bark must prefer it to avoid
    contradictory messages such as "进攻模式" and "退潮" in one push.
    """
    ctx = _first_market_context(stock_list)
    if not ctx:
        return _format_regime_line(regime)

    label = ctx.get("market_sentiment_label") or ctx.get("market_sentiment_stage") or "--"
    score = ctx.get("market_sentiment_score", "--")
    cap = ctx.get("portfolio_position_cap_pct", "--")
    line = f"{label} {score}分 | 总仓上限 {cap}%"
    regime_tail = _format_regime_line(regime)
    if regime_tail and regime_tail != "❓ 未知":
        line += f" | {regime_tail}"
    return line


def send_intraday_heartbeat(stock_list: List[Dict[str, Any]], reason: str) -> Optional[str]:
    """
    Sends a Sentinel heartbeat when the scheduled scan runs but has no actionable A/B candidates.
    """
    if not is_a_share_intraday_session():
        logger.info("Sentinel: Market is closed, skip Bark heartbeat.")
        return None

    from core.data import get_market_regime, get_market_snapshot, format_freshness
    regime = get_market_regime()
    market_line = _format_market_line(stock_list, regime)
    state_fingerprint = _intraday_state_fingerprint(stock_list, regime)
    fingerprint = hashlib.sha256(
        f"{state_fingerprint}:{reason}".encode("utf-8")
    ).hexdigest()
    if not _should_send_intraday_state(fingerprint):
        logger.info("Sentinel: Bark heartbeat state unchanged, skip duplicate push.")
        return ""

    now_str = datetime.now().strftime("%H:%M")
    total = len(stock_list)
    grade_counts = {}
    for s in stock_list:
        grade = s.get('sop_grade') or '?'
        grade_counts[grade] = grade_counts.get(grade, 0) + 1
    grade_text = " / ".join(f"{g}级{n}只" for g, n in sorted(grade_counts.items())) if grade_counts else "无命中"
    watch_names = "、".join(
        f"{s.get('名称', s.get('name', ''))}({s.get('代码', s.get('code', ''))})"
        for s in stock_list[:3]
    )

    # P1：用真实行情时间戳替换静态"实时快照"行（走 60s 缓存，成本极低）
    freshness_line = format_freshness(get_market_snapshot())
    strategy_label = (
        stock_list[0].get("bark_scan_strategy_label")
        if stock_list else None
    ) or "TV宽松观察池"
    lines = [
        f"大盘：{market_line}",
        f"策略：{strategy_label} | {freshness_line}",
        f"结果：{reason}",
        f"统计：TV宽松池命中 {total} 只 | {grade_text}",
        "执行：无A/B级不买入；等待14:30尾盘确认，不追D级和冲高回落票。",
    ]
    if watch_names:
        lines.append(f"观察但不操作：{watch_names}")

    _append_real_position_status(lines)
    body = "\n".join(lines)
    if not _send_bark_message(f"Alpha Vision 扫描心跳 {now_str}", body):
        return None
    _mark_intraday_state_sent(fingerprint)
    return body


def send_new_strategy_shadow_notification(report: Dict[str, Any]) -> Optional[str]:
    """Send the independent new-strategy shadow report with no executable wording."""
    global _last_new_strategy_shadow_fingerprint

    completed_day = bool(report.get("completed_day"))
    valid_session = (
        is_a_share_after_close_sync_window()
        if completed_day
        else is_a_share_intraday_session()
    )
    if not valid_session:
        logger.info("Sentinel: Outside shadow-report session, skip Bark.")
        return None
    market = report.get("market") or {}
    candidates = report.get("candidates") or []
    state = {
        "data_date": report.get("data_date"),
        "completed_day": completed_day,
        "market": {
            key: market.get(key)
            for key in (
                "index_axis",
                "breadth_axis",
                "breadth_stage",
                "route_a_permission_1d",
                "route_a_permission_2d",
                "route_b_permission",
                "route_c_permission",
            )
        },
        "candidates": [
            {
                "code": item.get("代码") or item.get("code"),
                "route": item.get("shadow_route"),
                "state": item.get("shadow_state"),
                "blockers": (item.get("shadow_blockers") or [])[:2],
            }
            for item in candidates[:10]
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if fingerprint == _last_new_strategy_shadow_fingerprint:
        logger.info("Sentinel: New-strategy shadow state unchanged, skip duplicate Bark.")
        return ""

    counts = report.get("route_counts") or {
        route: sum(item.get("shadow_route") == route for item in candidates)
        for route in ("A", "B", "DISCOVERY")
    }
    lines = [
        "模式：SHADOW研究观察；所有标的均不可下单，不生成执行意图。",
        f"时点：{'收盘确认' if completed_day else '盘中预览（待收盘确认）'}。",
        (
            f"市场：{market.get('index_axis', 'UNKNOWN')} × "
            f"{market.get('breadth_axis', 'UNKNOWN')}"
            f"（{market.get('breadth_stage', 'UNKNOWN')}）"
        ),
        (
            f"路线A权限：1日={market.get('route_a_permission_1d', 'BLOCKED')}｜"
            f"2日={market.get('route_a_permission_2d', 'BLOCKED')}；"
            "参数尚未冻结"
        ),
        (
            f"路线B：{market.get('route_b_permission', 'BLOCKED')}｜"
            f"路线C：{market.get('route_c_permission', 'RESEARCH')}"
        ),
        (
            f"统计：路线A {counts.get('A', 0)}只｜路线B {counts.get('B', 0)}只｜"
            f"宽松发现 {counts.get('DISCOVERY', 0)}只"
        ),
        "",
    ]
    route_labels = {
        "A": "路线A｜普通趋势确认观察",
        "B": "路线B｜强趋势回踩观察",
        "DISCOVERY": "宽松发现｜等待严格双命中",
    }
    for item in candidates[:5]:
        code = item.get("代码") or item.get("code") or "--"
        name = item.get("名称") or item.get("name") or "--"
        route = str(item.get("shadow_route") or "DISCOVERY")
        lines.append(f"【{route_labels.get(route, route)}】{name}({code})")
        lines.append(
            f"  5日涨幅：{float(item.get('pct_5d') or 0):+.1f}%｜"
            f"PA：{float(item.get('price_action_score') or 0):.0f}｜"
            f"状态：{item.get('shadow_state') or '--'}"
        )
        entry = float(item.get("pa_entry_price") or 0)
        stop = float(item.get("pa_stop_price") or 0)
        if entry > 0 and stop > 0:
            lines.append(f"  研究计划：确认价{entry:.2f}｜止损参考{stop:.2f}（不可执行）")
        lines.append(f"  等待：{item.get('shadow_instruction') or '继续观察'}")
        blockers = [str(value) for value in item.get("shadow_blockers") or []]
        if blockers:
            lines.append(f"  阻断：{'；'.join(blockers[:2])}")
    if not candidates:
        lines.append("结果：当前宽松发现池无候选；保持空观察，不降低门槛。")
    if market.get("route_c_market_watch"):
        lines.extend(
            [
                "",
                "【路线C｜市场级修复影子】",
                "当前仅记录修复环境；尚无独立个股入场模型，不迁移路线A/B候选。",
            ]
        )

    now_str = datetime.now().strftime("%H:%M")
    body = "\n".join(lines)
    phase = "收盘确认" if completed_day else "盘中预览"
    title = f"Alpha Vision 新策略影子观察｜{phase}｜不可交易 {now_str}"
    if not _send_bark_message(title, body):
        return None
    _last_new_strategy_shadow_fingerprint = fingerprint
    return body


def run_new_strategy_shadow_cycle(
    source_candidates: List[Dict[str, Any]],
    *,
    completed_day: bool = False,
    notify: bool = True,
) -> Optional[str]:
    """Build and persist one shadow cycle; notification is opt-in for research use."""
    from core.new_strategy_shadow import (
        build_live_shadow_report,
        record_new_strategy_shadow_report,
    )

    report = build_live_shadow_report(
        source_candidates,
        completed_day=completed_day,
    )
    record_new_strategy_shadow_report(report)
    return send_new_strategy_shadow_notification(report) if notify else None


def send_intraday_notification(stock_list: List[Dict[str, Any]]) -> Optional[str]:
    """Send an interruptive Bark only for executable candidates."""
    if not stock_list:
        return None
    if not is_a_share_intraday_session():
        logger.info("Sentinel: Market is closed, skip Bark intraday notification.")
        return None

    priority_adjustments = _load_recommendation_priority_adjustments()
    push_stocks = _select_intraday_push_stocks(
        stock_list,
        priority_adjustments=priority_adjustments,
        push_source="bark",
    )
    if not push_stocks:
        logger.info("Sentinel: No executable stocks to push.")
        return None

    # 获取大盘状态
    from core.data import get_market_regime, get_market_snapshot, format_freshness
    regime = get_market_regime()
    market_line = _format_market_line(push_stocks, regime)
    fingerprint = _intraday_state_fingerprint(push_stocks, regime)
    if not _should_send_intraday_state(fingerprint):
        logger.info("Sentinel: Bark state unchanged, skip duplicate intraday push.")
        return ""
    engine = None
    try:
        from core.db import get_db_engine, save_recommendation_events
        engine = get_db_engine()
        save_recommendation_events(
            push_stocks,
            engine=engine,
            source="bark",
            market_regime=regime.get("status", "UNKNOWN"),
        )
    except Exception as exc:
        logger.warning(f"Sentinel recommendation event persistence skipped: {exc}")

    notification_time = datetime.now()
    now_str = notification_time.strftime("%H:%M")
    current_time = notification_time.time()
    is_tail_decision_window = current_time.hour == 14 and current_time.minute >= 20
    primary_strategy = stock_list[0].get('strategy_type') if stock_list else None
    if primary_strategy == "bottom_discovery":
        title_prefix = "Alpha Vision 底部观察｜不可交易"
    else:
        title_prefix = "Alpha Vision 尾盘决策" if is_tail_decision_window else "Alpha Vision 盘中哨兵"
    title = f"{title_prefix} {now_str}"

    strategy_names = {
        'tv_dual': 'TV均线或ZP',
        'tv_dual_strict': 'TV双策略强共振',
        'tv_reversal_watch': 'TV强修复观察（不可交易）',
        'tv_zp': 'TV-ZP策略',
        'squeeze': '均线B共振',
        'pine': 'Pine多指标',
        'both': '双策略共振',
        'bottom_discovery': '底部起涨发现（仅观察）',
    }
    strategy_label = (
        push_stocks[0].get("bark_scan_strategy_label")
        or strategy_names.get(primary_strategy, primary_strategy or '系统策略')
    )

    # P1：用真实行情时间戳替换静态"实时快照"行（走 60s 缓存）
    freshness_line = format_freshness(get_market_snapshot())
    all_paused = bool(push_stocks) and all(
        (stock.get("strategy_health") or {}).get("status") == "PAUSED"
        for stock in push_stocks
    )
    lines = [
        f"市场：{market_line}",
        f"策略：{strategy_label} | {freshness_line}",
        (
            "结论：策略已暂停，以下只观察。"
            if all_paused
            else "结论：仅“可交易”可复核，其余不下单。"
        ),
        "",
    ]
    section_titles = {
        "可交易": "【可交易候选】",
        "强势异动": "【强势异动｜只观察不追高】",
        "观察": "【只观察】",
        "禁止追买": "【禁止买入】",
    }
    grouped = {"可交易": [], "强势异动": [], "观察": [], "禁止追买": []}
    for s in push_stocks[:10]:
        grouped.setdefault(_candidate_push_bucket(s), []).append(s)

    for section in ("可交易", "强势异动", "观察", "禁止追买"):
        stocks = grouped.get(section) or []
        if not stocks:
            continue
        lines.append(section_titles[section])
        for s in stocks:
            lines.extend(_candidate_brief_lines(s))
            lines.append("")

    total_a = sum(1 for s in stock_list if s.get('sop_grade') == 'A')
    total_a_eod = sum(1 for s in stock_list if s.get('a_eod_controlled_trial'))
    total_a_minus = sum(1 for s in stock_list if s.get('a_minus_trial'))
    total_b = sum(1 for s in stock_list if s.get('sop_grade') == 'B')
    total_m = sum(1 for s in stock_list if s.get('sop_grade') == 'M')
    total_c = sum(1 for s in stock_list if s.get('sop_grade') == 'C')
    total_sector_watch = sum(1 for s in stock_list if s.get('sector_watch_only') and s.get('sop_grade') != 'D')
    lines.append(
        f"汇总：A {total_a}｜A-EOD {total_a_eod}｜A- {total_a_minus}｜B {total_b}｜"
        f"M {total_m}｜C {total_c}｜板块观察 {total_sector_watch}"
    )

    _append_real_position_status(lines)

    body = "\n".join(lines)

    # Persist the decision before delivery so Bark availability cannot bias
    # later strategy-performance measurement.
    try:
        from core.signal_performance import save_intraday_signal_snapshots
        save_intraday_signal_snapshots(push_stocks, engine, source="bark", signal_time=notification_time)
    except Exception as exc:
        logger.warning(f"Signal snapshot persistence skipped: {exc}")

    # A delayed executable instruction can become unsafe; later checkpoints
    # recompute it instead of retrying a stale instruction from the outbox.
    from core.notifier import split_message_body
    parts = split_message_body(body)
    deliveries = [
        _send_bark_message(
            f"{title} ({index}/{len(parts)})" if len(parts) > 1 else title,
            part,
            enqueue_failed=False,
        )
        for index, part in enumerate(parts, start=1)
    ]
    sent = all(deliveries)
    if sent:
        try:
            from core.execution_intents import create_bark_execution_intents
            create_bark_execution_intents(push_stocks, engine, issued_at=notification_time)
        except Exception as exc:
            logger.warning(f"Execution intent persistence skipped: {exc}")
        _mark_intraday_state_sent(fingerprint)
    return body if sent else None


class IntradaySentinel:
    def __init__(self):
        self.last_top_5 = []
        self.thread = None
        self._stop = False
        self.schedule_times = [
            item.strip() for item in config.SENTINEL_SCHEDULE_TIMES.split(",") if item.strip()
        ]
        self.triggered_today = set()
        self._triggered_date = ""  # 修复 BUG5：基于日期变更重置 triggered_today
        # 风控独立高频检查，不与每30分钟一次的全市场策略扫描耦合：
        # 在交易时段内每 WIND_CONTROL_INTERVAL_MINUTES 分钟跑一次 run_wind_control，
        # 不影响扫描频率。
        self.wind_control_interval_minutes = 30
        self.last_wind_control_dt = None
        # 改动 B1：动态紧迫度。run_wind_control 每次跑完后会更新这两个字段，
        # _should_run_wind_control 据此把间隔从常规 30 分钟降到紧迫 10 分钟。
        # _min_stop_buffer_pct：所有持仓中当前价距止损线的最小间距（%）。
        # _last_regime_urgent：上次风控时大盘是否处于弱市(bear/volatile)。
        self._min_stop_buffer_pct: float | None = None
        self._last_regime_urgent: bool = False

    def update_schedule(self, times_str: Optional[str] = None):
        """实时更新调度时间点"""
        if times_str is None:
            times_str = get_setting(
                "sentinel_schedule_times", config.SENTINEL_SCHEDULE_TIMES
            )
        self.schedule_times = [t.strip() for t in times_str.split(",") if t.strip()]
        logger.info(f"Sentinel schedule updated to: {self.schedule_times}")

    def _load_schedule(self):
        stored = get_setting("sentinel_schedule_times", "")
        policy_version = get_setting("sentinel_schedule_policy_version", "")
        if policy_version != _SENTINEL_SCHEDULE_POLICY_VERSION:
            if not stored or stored == _LEGACY_SENTINEL_SCHEDULE:
                stored = config.SENTINEL_SCHEDULE_TIMES
                schedule_saved = save_setting("sentinel_schedule_times", stored)
                if not schedule_saved:
                    logger.warning("Sentinel half-hour schedule migration will retry after save failure.")
                    self.update_schedule(stored)
                    return
            save_setting("sentinel_schedule_policy_version", _SENTINEL_SCHEDULE_POLICY_VERSION)
        self.update_schedule(stored or config.SENTINEL_SCHEDULE_TIMES)

    def _due_schedule_slot(self, now: datetime, grace_minutes: int = 5) -> Optional[str]:
        for scheduled_time in self.schedule_times:
            try:
                hour, minute = (int(part) for part in scheduled_time.split(":"))
                scheduled_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            except (TypeError, ValueError):
                continue
            delay_seconds = (now - scheduled_at).total_seconds()
            if 0 <= delay_seconds <= grace_minutes * 60 and scheduled_time not in self.triggered_today:
                return scheduled_time
        return None

    def _enqueue_strategy_scan(self, scheduled_time: str) -> None:
        from core.tasks import intraday_monitor_checkpoint

        slot = "morning_confirm" if scheduled_time == "10:30" else f"strategy_scan_{scheduled_time.replace(':', '')}"
        intraday_monitor_checkpoint.apply_async(
            kwargs={"slot": slot},
            queue="scan",
            expires=25 * 60,
        )
        logger.info(f"Sentinel queued half-hour strategy scan: {scheduled_time} ({slot})")

    def _effective_wind_control_interval(self) -> float:
        """改动 B1：动态风控间隔。

        常规 30 分钟。当任一持仓 stop_buffer < URGENT_STOP_BUFFER_PCT(2%) 或
        大盘处于弱市(bear/volatile)时，降到 WIND_CONTROL_INTERVAL_URGENT_MINUTES(10)，
        缩短急跌行情下的感知延迟。对上班族尤其关键。
        """
        from core.risk_constants import WIND_CONTROL_INTERVAL_URGENT_MINUTES, URGENT_STOP_BUFFER_PCT
        if self._last_regime_urgent:
            return WIND_CONTROL_INTERVAL_URGENT_MINUTES
        if self._min_stop_buffer_pct is not None and self._min_stop_buffer_pct < URGENT_STOP_BUFFER_PCT:
            return WIND_CONTROL_INTERVAL_URGENT_MINUTES
        return self.wind_control_interval_minutes

    def _should_run_wind_control(self, now: datetime) -> bool:
        """改动 #10：风控独立 tick 判定。

        在 A 股交易时段内，距上次风控超过有效间隔分钟则返回 True。
        有效间隔由 _effective_wind_control_interval 动态决定（常规30/紧迫10）。
        首次（last_wind_control_dt 为空）且在交易时段内也触发。非交易时段不触发。
        """
        if not is_a_share_intraday_session(now):
            return False
        effective_interval = self._effective_wind_control_interval()
        if effective_interval <= 0:
            return False
        if self.last_wind_control_dt is None:
            return True
        elapsed = (now - self.last_wind_control_dt).total_seconds() / 60.0
        return elapsed >= effective_interval

    def start(self):
        self._load_schedule()
        self.triggered_today.clear()
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop:
            try:
                now = datetime.now()
                today_str = now.strftime("%Y-%m-%d")

                # 修复 BUG5：原仅靠 current_time=="00:00" 重置 triggered_today，若该分钟
                # 被错过（长扫描/GC暂停/进程重启），则永久抑制后续扫描。改为基于日期变更重置。
                if today_str != self._triggered_date:
                    self.triggered_today.clear()
                    self._triggered_date = today_str

                due_time = self._due_schedule_slot(now)
                if due_time:
                    if not is_a_share_intraday_session(now):
                        logger.info(f"Sentinel skipped at {due_time}: non-trading session.")
                        self.triggered_today.add(due_time)
                        time.sleep(30)
                        continue

                    logger.info(f"Sentinel Triggered at {due_time}: queue strategy scan...")
                    self.triggered_today.add(due_time)
                    try:
                        self._enqueue_strategy_scan(due_time)
                    except Exception as e:
                        self.triggered_today.discard(due_time)
                        logger.error(f"Sentinel scan enqueue error: {e}")

                # 改动 #10：风控独立高频检查（交易时段内每 N 分钟一次）
                if self._should_run_wind_control(now):
                    try:
                        logger.info("Sentinel: Running Paper Trading Wind Control (independent tick)...")
                        from routers.paper_trade import run_wind_control
                        wc_res = run_wind_control()
                        self.last_wind_control_dt = now
                        # 改动 B1：更新动态紧迫度状态，供下次 _should_run_wind_control 判定
                        self._min_stop_buffer_pct = wc_res.get("min_stop_buffer_pct")
                        self._last_regime_urgent = bool(wc_res.get("regime_urgent"))
                        if wc_res.get("closed_count", 0) > 0:
                            logger.info(f"Wind Control: Closed {wc_res['closed_count']} positions.")
                    except Exception as wc_err:
                        logger.error(f"Sentinel Wind Control Error: {wc_err}")
                        # 即使出错也更新时间戳，避免高频重试淹没日志
                        self.last_wind_control_dt = now

                if now.minute % 10 == 0 and now.second < 30:
                    self._load_schedule()

            except Exception as outer_e:
                logger.error(f"Sentinel Loop Error (will auto-recover): {outer_e}")

            time.sleep(30)


sentinel = IntradaySentinel()
