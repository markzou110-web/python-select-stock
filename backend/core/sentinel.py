"""
Alpha Vision - Intraday Sentinel Module.
Handles automated intraday schedule checks, multi-strategy scanning, and push notifications.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import time
import threading
import asyncio

from core.config import config
from core.logging_config import logger
from core.db import get_setting, save_setting
from core.trading_calendar import (
    is_a_share_after_close_sync_window,
    is_a_share_intraday_session,
)
from core.operation_plan import position_health_score, position_size_advice, price_instruction


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
    if stock.get('sector_watch_only'):
        return "观察"
    if stock.get('trade_bucket') == 'TRADE' or stock.get('trade_eligible') is True:
        return "可交易"
    if stock.get('trade_bucket') == 'BLOCK' or stock.get('trade_eligible') is False:
        return "禁止追买"
    if stock.get('sop_grade') in ('A', 'B'):
        return "可交易"
    return "观察"


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
    return f"  回踩判断: {label}{' | ' + ' / '.join(prices) if prices else ''}"


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


def _select_intraday_push_stocks(stock_list: List[Dict[str, Any]], executable_limit: int = 5, sector_watch_limit: int = 3) -> List[Dict[str, Any]]:
    """
    Keep executable candidates and sector-confirmed observation candidates in Bark.
    Sector-watch stocks are observation-only, so they get a separate quota instead of
    competing with A/B/M executable names.
    """
    grade_order = {'A': 0, 'B': 1, 'M': 2, 'C': 3, 'D': 4, '?': 5}

    def rank_key(stock: Dict[str, Any]):
        return (
            grade_order.get(stock.get('sop_grade', '?'), 5),
            -float(stock.get('final_rank_score', stock.get('Score', 0)) or 0),
        )

    executable = [
        s for s in stock_list
        if (
            not s.get('sector_watch_only')
            and s.get('sop_grade') in ('A', 'B', 'M', 'C')
            and s.get('trade_bucket') != 'BLOCK'
            and s.get('trade_eligible') is not False
        )
    ]
    blocked = [
        s for s in stock_list
        if (
            not s.get('sector_watch_only')
            and s.get('sop_grade') in ('A', 'B', 'M', 'C')
            and (s.get('trade_bucket') == 'BLOCK' or s.get('trade_eligible') is False)
        )
    ]
    sector_watch = [
        s for s in stock_list
        if s.get('sector_watch_only') and s.get('sop_grade') != 'D'
    ]

    selected: List[Dict[str, Any]] = []
    seen_codes = set()
    for group, limit in (
        (sorted(executable, key=rank_key), executable_limit),
        (sorted(sector_watch, key=rank_key), sector_watch_limit),
        (sorted(blocked, key=rank_key), max(1, sector_watch_limit)),
    ):
        picked = 0
        for stock in group:
            code = stock.get('代码') or stock.get('code')
            if code in seen_codes:
                continue
            selected.append(stock)
            seen_codes.add(code)
            picked += 1
            if picked >= limit:
                break
    return selected


def _select_after_close_watchlist(stock_list: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    """Select next-day observation candidates without presenting blocked stocks as opportunities."""
    grade_order = {'A': 0, 'B': 1, 'M': 2, 'C': 3, 'D': 4}
    candidates = [
        stock for stock in stock_list
        if (
            (
                stock.get('sop_grade') in {'A', 'B', 'M', 'C'}
                or float(stock.get('trade_opportunity_score') or 0) >= 60
            )
            and stock.get('trade_bucket') != 'BLOCK'
            and not stock.get('sector_watch_only')
        )
    ]
    return sorted(
        candidates,
        key=lambda stock: (
            grade_order.get(stock.get('sop_grade'), 4),
            -float(stock.get('final_trade_score', stock.get('Score', 0)) or 0),
        ),
    )[:limit]


def _after_close_price(stock: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = stock.get(key)
        if value not in (None, "", 0):
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                continue
    return None


def _build_after_close_watchlist_body(stock_list: List[Dict[str, Any]], scan_date: str) -> str:
    first = stock_list[0] if stock_list else {}
    lines = [
        f"数据日期：{scan_date}",
        f"市场：{first.get('market_sentiment_label', '--')} | 情绪分 {first.get('market_sentiment_score', '--')} | 总仓上限 {first.get('portfolio_position_cap_pct', '--')}%",
        "性质：次日观察清单，不是买入指令；高开或冲高时不追价。",
        "执行：次日仅在触发价上方站稳且量能确认后复核，跌破失效价立即取消计划。",
        "",
    ]
    for stock in stock_list:
        name = stock.get('名称') or stock.get('name') or ''
        code = stock.get('代码') or stock.get('code') or ''
        grade = stock.get('sop_grade') or '?'
        current = _after_close_price(stock, '现价', 'price')
        entry = _after_close_price(stock, 'entry_price', 'pa_entry_price')
        stop = _after_close_price(stock, 'plan_stop_price', 'stop_price', 'pa_stop_price')
        blockers = stock.get('trade_blockers') or []
        if isinstance(blockers, str):
            blocker_text = blockers.strip("[]'\" ")
        else:
            blocker_text = "、".join(str(item) for item in blockers[:2])

        lines.append(f"【{grade}级观察】{name} ({code}) | 收盘 {current if current else '--'}")
        lines.append(
            f"  定位：{stock.get('sector_mainline', '--')} / {stock.get('sector_role', '--')}"
            f" | 机会分 {stock.get('trade_opportunity_score', '--')} | {stock.get('trade_opportunity_label', '观望')}"
        )
        lines.append(f"  确认：站稳 >{entry if entry else '--'} 且量能确认，再考虑小仓复核")
        lines.append(f"  失效：跌破 <{stop if stop else '--'}，取消观察/不得买入")
        lines.append(f"  当前：等待确认，不追高{f'；原因：{blocker_text}' if blocker_text else ''}")
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

    selected = _select_after_close_watchlist(stock_list, limit=limit)
    if not selected:
        logger.info("Sentinel: no qualified after-close observation candidates.")
        return None

    body = _build_after_close_watchlist_body(selected, today)
    try:
        from core.db import get_db_engine, save_recommendation_events
        save_recommendation_events(
            selected,
            engine=get_db_engine(),
            source="bark_next_day",
            event_date=today,
        )
    except Exception as exc:
        logger.warning(f"After-close recommendation event persistence skipped: {exc}")

    if not _send_bark_message(f"Alpha Vision 次日观察清单 {today}", body):
        return None
    save_setting("after_close_watchlist_last_date", today)
    return body


def _send_bark_message(title: str, body: str) -> bool:
    logger.info(f"Notification: {body}")

    from core.notifier import notifier
    try:
        asyncio.run(notifier.send(title, body, channels=["bark"]))
        return True
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
    from core.data import get_market_snapshot, get_index_hist
    from core.indicators import calculate_indicators
    from core.strategy import evaluate_exit_signals
    from core.risk_engine import compute_paper_risk_levels, safe_float
    from core.price_action import analyze_price_action

    try:
        df_real = pd.read_sql(
            "SELECT code, name, entry_price, high_since_entry FROM paper_trading WHERE status = 'OPEN' AND trade_mode = 'REAL'",
            engine,
        )
        if df_real.empty:
            return

        lines.append("────────────────")
        lines.append("【🔴 实盘持仓状态】")

        snapshot = get_market_snapshot()
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

            query = text("SELECT date as \"日期\", close as \"收盘\", open as \"开盘\", high as \"最高\", low as \"最低\", vol as \"成交量\" FROM daily_k WHERE code = :code ORDER BY date DESC LIMIT 40")
            with engine.connect() as conn:
                df_hist = pd.read_sql(query, conn, params={'code': code})
                df_hist = df_hist.sort_values('日期')

            suggestion = "持股观望"
            if len(df_hist) >= 20:
                df_labeled = calculate_indicators(df_hist, current_price=curr, bench_df=bench_df)
                signals = evaluate_exit_signals(df_labeled, entry, high)
                risk = compute_paper_risk_levels(entry, high, curr)
                pa = analyze_price_action(df_hist)
                active_stop = risk.get("active_stop_price") or risk.get("stop_price") or 0
                stop_buffer = ((curr - active_stop) / curr * 100) if curr > 0 and active_stop > 0 else None
                suggestion = _real_position_action(curr, entry, high, risk, pa, signals, df_hist)

            lines.append(status_line)
            lines.append(f"   └ {suggestion}")
        lines.append("")
    except Exception as e:
        logger.error(f"Error appending real stock status: {e}")


def send_intraday_heartbeat(stock_list: List[Dict[str, Any]], reason: str) -> Optional[str]:
    """
    Sends a Sentinel heartbeat when the scheduled scan runs but has no actionable A/B candidates.
    """
    if not is_a_share_intraday_session():
        logger.info("Sentinel: Market is closed, skip Bark heartbeat.")
        return None

    from core.data import get_market_regime
    regime_emoji = {"OFFENSIVE": "🚀 进攻模式", "DEFENSIVE": "⚠️ 防守模式", "CRITICAL": "🛡️ 严格防守"}
    regime = get_market_regime()
    regime_str = regime_emoji.get(regime.get('status', ''), '❓ 未知')

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

    lines = [
        f"大盘：{regime_str}",
        "策略：TV双策略强共振 | 数据：实时快照优先，失败回退daily_k",
        f"结果：{reason}",
        f"统计：强共振命中 {total} 只 | {grade_text}",
        "执行：无A/B级不买入；等待14:30尾盘确认，不追D级和冲高回落票。",
    ]
    if watch_names:
        lines.append(f"观察但不操作：{watch_names}")

    _append_real_position_status(lines)
    body = "\n".join(lines)
    _send_bark_message(f"Alpha Vision 扫描心跳 {now_str}", body)
    return body


def send_intraday_notification(stock_list: List[Dict[str, Any]]) -> Optional[str]:
    """
    Sends a push notification via the Notifier for the Sentinel.
    Sends A/B executable candidates and M/C observation candidates.
    """
    if not stock_list:
        return None
    if not is_a_share_intraday_session():
        logger.info("Sentinel: Market is closed, skip Bark intraday notification.")
        return None

    push_stocks = _select_intraday_push_stocks(stock_list)
    if not push_stocks:
        logger.info("Sentinel: No A/B/M/C or sector-watch stocks to push.")
        return None

    # 获取大盘状态
    regime_emoji = {"OFFENSIVE": "🚀 进攻模式", "DEFENSIVE": "⚠️ 防守模式", "CRITICAL": "🛡️ 严格防守"}
    from core.data import get_market_regime
    regime = get_market_regime()
    regime_str = regime_emoji.get(regime.get('status', ''), '❓ 未知')
    try:
        from core.db import get_db_engine, save_recommendation_events
        save_recommendation_events(
            push_stocks,
            engine=get_db_engine(),
            source="bark",
            market_regime=regime.get("status", "UNKNOWN"),
        )
    except Exception as exc:
        logger.warning(f"Sentinel recommendation event persistence skipped: {exc}")

    now_str = datetime.now().strftime("%H:%M")
    current_time = datetime.now().time()
    is_tail_decision_window = current_time.hour == 14 and current_time.minute >= 20
    title_prefix = "Alpha Vision 尾盘决策" if is_tail_decision_window else "Alpha Vision 盘中哨兵"
    title = f"{title_prefix} {now_str}"

    strategy_names = {
        'tv_dual': 'TV双策略对齐',
        'tv_dual_strict': 'TV双策略强共振',
        'tv_zp': 'TV-ZP策略',
        'squeeze': '均线B共振',
        'pine': 'Pine多指标',
        'both': '双策略共振',
    }
    primary_strategy = stock_list[0].get('strategy_type') if stock_list else None
    strategy_label = strategy_names.get(primary_strategy, primary_strategy or '系统策略')

    lines = [
        f"大盘：{regime_str}",
        (
            f"情绪：{push_stocks[0].get('market_sentiment_label', '--')}"
            f" {push_stocks[0].get('market_sentiment_score', '--')}分"
            f" | 总仓上限 {push_stocks[0].get('portfolio_position_cap_pct', '--')}%"
        ),
        f"策略：{strategy_label} | 数据：实时快照优先，失败回退daily_k",
        "执行：候选≠指令；买入只在14:40-14:55确认，14:57后不追单",
        "确认：接近入场价、未跌破失效价、无冲高回落长上影",
        "",
    ]
    sector_emoji = {'LEAD': '🚀领涨', 'FOLLOW': '📈跟涨', 'FLAT': '➖横盘', 'DOWN': '📉下跌'}

    grade_icons = {'A': '🟢', 'B': '🔵', 'M': '🟠', 'C': '⚪'}
    section_titles = {
        "可交易": "【可交易候选】",
        "观察": "【观察池】",
        "禁止追买": "【禁止追买/风险样本】",
    }
    grouped = {"可交易": [], "观察": [], "禁止追买": []}
    for s in push_stocks[:10]:
        grouped.setdefault(_candidate_push_bucket(s), []).append(s)

    for section in ("可交易", "观察", "禁止追买"):
        stocks = grouped.get(section) or []
        if not stocks:
            continue
        lines.append(section_titles[section])
        for s in stocks:
            grade = s.get('sop_grade', '?')
            grade_icon = grade_icons.get(grade, '⚪')
            name = s.get('名称', s.get('name', ''))
            code = s.get('代码', s.get('code', ''))
            sector = s.get('行业', '')
            s_trend = sector_emoji.get(s.get('sector_trend', ''), '')
            s_pct = s.get('sector_pct', 0)
            entry = s.get('entry_price', 0)
            stop = s.get('plan_stop_price') or s.get('stop_price', 0)
            target = s.get('target_price') or s.get('pa_target_price')
            win_rate = s.get('历史胜率', 'N/A')
            pf = s.get('回测统计', {}).get('profit_factor', 'N/A')
            final_score = s.get('final_trade_score')

            score_text = f" | 交易分: {final_score}" if final_score is not None else ""
            lines.append(f"{grade_icon} {grade}级 {name} ({code}){score_text}")
            lines.append(
                f"  定位: {s.get('sector_mainline', '--')} / {s.get('sector_role', '--')}"
                f" | 机会分 {s.get('trade_opportunity_score', '--')} | {s.get('trade_opportunity_label', '观望')}"
            )
            lines.append(f"  {_candidate_action_label(s)}")
            if s.get("execution_instruction"):
                lines.append(f"  明确指令: {s['execution_instruction']}")
            if s.get('sector_watch_only'):
                lines.extend(_sector_watch_advice_lines(s))
            if sector:
                lines.append(f"  板块: {sector} {s_trend}{'+' if s_pct >= 0 else ''}{s_pct}%")
            target_text = f" | 目标: {target}" if target else ""
            lines.append(f"  入场: {entry} | 失效: {stop}{target_text}")
            lines.append(f"  胜率: {win_rate} | 盈亏比: {pf}")
            sector_line = _sector_alignment_line(s)
            if sector_line:
                lines.append(sector_line)
            brooks_line = _brooks_alert_line(s)
            if brooks_line:
                lines.append(brooks_line)
            pullback_line = _pullback_alert_line(s)
            if pullback_line:
                lines.append(pullback_line)
            eight_rule = s.get("pa_eight_rule_primary") or {}
            if eight_rule:
                trigger = eight_rule.get("trigger_price")
                invalidation = eight_rule.get("invalidation_price")
                lines.append(
                    f"  八诀: {eight_rule.get('label')} {eight_rule.get('confidence')}%"
                    f" | 确认>{trigger if trigger else '--'} | 失效<{invalidation if invalidation else '--'}"
                )
            bonuses = s.get('sop_bonuses', [])
            if bonuses:
                lines.append(f"  ⭐ {'、'.join(bonuses)}")
            lines.append("")

    total_a = sum(1 for s in stock_list if s.get('sop_grade') == 'A')
    total_b = sum(1 for s in stock_list if s.get('sop_grade') == 'B')
    total_m = sum(1 for s in stock_list if s.get('sop_grade') == 'M')
    total_c = sum(1 for s in stock_list if s.get('sop_grade') == 'C')
    total_sector_watch = sum(1 for s in stock_list if s.get('sector_watch_only') and s.get('sop_grade') != 'D')
    lines.append(f"A级{total_a}只 | B级{total_b}只 | M级{total_m}只 | C级{total_c}只 | 板块观察{total_sector_watch}只")
    lines.append("")

    _append_real_position_status(lines)

    body = "\n".join(lines)
    _send_bark_message(title, body)
    return body


class IntradaySentinel:
    def __init__(self):
        self.last_top_5 = []
        self.thread = None
        self._stop = False
        self.schedule_times = ["14:20"]
        self.triggered_today = set()

    def update_schedule(self, times_str: Optional[str] = None):
        """实时更新调度时间点"""
        if times_str is None:
            times_str = get_setting("sentinel_schedule_times", "14:20")
        self.schedule_times = [t.strip() for t in times_str.split(",") if t.strip()]
        logger.info(f"Sentinel schedule updated to: {self.schedule_times}")

    def _load_schedule(self):
        # 保持兼容性调用 update_schedule
        self.update_schedule()

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
                current_time = now.strftime("%H:%M")

                # Reset tracking every day
                if current_time == "00:00":
                    self.triggered_today.clear()

                if current_time in self.schedule_times and current_time not in self.triggered_today:
                    if not is_a_share_intraday_session(now):
                        logger.info(f"Sentinel skipped at {current_time}: non-trading session.")
                        self.triggered_today.add(current_time)
                        time.sleep(30)
                        continue

                    logger.info(f"Sentinel Triggered at {current_time}: Automated check...")
                    self.triggered_today.add(current_time)
                    try:
                        from routers.scan import run_market_scan_task
                        
                        logger.info("Sentinel: Running TV Dual Strict strategy scan with realtime snapshot first...")
                        results = run_market_scan_task(local_only=False, strategy_type="tv_dual_strict") or []
                        for s in results:
                            s['strategy_type'] = s.get('strategy_type') or 'tv_dual_strict'

                        # 按评级排序（A级优先，B级次之，C级再次），其次按 Score 降序
                        grade_order = {'A': 0, 'B': 1, 'M': 2, 'C': 3, 'D': 4, '?': 5}
                        results = sorted(results, key=lambda x: (grade_order.get(x.get('sop_grade', '?'), 4), -x.get('final_rank_score', x.get('Score', 0))))
                        
                        if results:
                            self.last_top_5 = _select_intraday_push_stocks(results)
                            pushed_body = send_intraday_notification(self.last_top_5)
                            if not pushed_body:
                                send_intraday_heartbeat(
                                    self.last_top_5,
                                    "强共振有命中，但没有达到A/B/M/C推送级别，今日暂不操作。"
                                )
                        else:
                            send_intraday_heartbeat([], "强共振无命中，今日暂不操作。")

                        # --- 新增: 拟合实盘风控检查 ---
                        logger.info("Sentinel: Running Paper Trading Wind Control...")
                        from routers.paper_trade import run_wind_control
                        wc_res = run_wind_control()
                        if wc_res.get("closed_count", 0) > 0:
                            logger.info(f"Wind Control: Closed {wc_res['closed_count']} positions.")
                    except Exception as e:
                        logger.error(f"Sentinel Scan Error: {e}")
                        send_intraday_heartbeat([], f"扫描异常，未产生可执行候选：{str(e)[:80]}")

                if now.minute % 10 == 0 and now.second < 30:
                    self._load_schedule()

            except Exception as outer_e:
                logger.error(f"Sentinel Loop Error (will auto-recover): {outer_e}")

            time.sleep(30)


sentinel = IntradaySentinel()
