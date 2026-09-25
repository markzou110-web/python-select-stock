"""Explicit next-session review for candidates that were not tradable yesterday."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Any, Dict, Iterable

from sqlalchemy import text

from core.execution_labels import daily_limit_pct
from core.risk_constants import (
    A_EOD_MAX_ENTRY_EXTENSION_PCT,
    A_EOD_T1_MAX_POSITIONS,
    A_EOD_T1_POLICY_VERSION,
    A_EOD_T1_PORTFOLIO_CAP_PCT,
    A_EOD_T1_POSITION_PCT,
    LIMIT_PRICE_TOLERANCE,
    PRIMARY_TV_STRATEGY,
)
from core.trading_calendar import previous_a_share_trading_date


def _code(value: Any) -> str:
    raw = str(value or "").strip()
    return raw.zfill(6) if raw else ""


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def load_pending_next_day_reviews(engine, as_of: date | None = None) -> list[Dict[str, Any]]:
    """Load the latest prior-session review set without using today's outcome."""
    if engine is None:
        return []
    today = as_of or date.today()
    review_date = previous_a_share_trading_date(today.isoformat())
    with engine.connect() as conn:
        snapshots = conn.execute(text("""
            SELECT signal_date, signal_time, source, code, name, strategy_type,
                   grade, signal_price, confirmation_price, stop_price,
                   trade_bucket, trade_eligible, instruction_state,
                   market_stage, sector_phase, sector_mainline, snapshot_payload
            FROM intraday_signal_snapshots
            WHERE signal_date = :review_date AND instruction_state = 'NEXT_DAY_REVIEW'
            ORDER BY signal_time DESC
        """), {"review_date": review_date}).mappings().all()
        after_close = conn.execute(text("""
            SELECT event_date AS signal_date, event_time AS signal_time, source,
                   code, name, strategy_type, recommendation_price AS signal_price,
                   pa_entry_price AS confirmation_price, pa_stop_price AS stop_price
            FROM recommendation_events
            WHERE event_date = :review_date AND source = 'bark_next_day'
            ORDER BY event_time DESC
        """), {"review_date": review_date}).mappings().all()

    # Rich immutable snapshots take precedence over the compact recommendation row.
    merged: dict[tuple[str, str], Dict[str, Any]] = {}
    for row in snapshots:
        raw = dict(row)
        item = {**_json_object(raw.pop("snapshot_payload", None)), **raw}
        item["target_price"] = item.get("a_eod_t1_frozen_target_price") or item.get("pa_target_price")
        item["confirmation_price"] = item.get("a_eod_t1_frozen_entry_price") or item.get("confirmation_price")
        item["stop_price"] = item.get("a_eod_t1_frozen_stop_price") or item.get("stop_price")
        item["code"] = _code(item.get("code"))
        item["strategy_type"] = str(item.get("strategy_type") or PRIMARY_TV_STRATEGY)
        key = (item["code"], item["strategy_type"])
        if item["code"] and key not in merged:
            merged[key] = item
    for row in after_close:
        item = dict(row)
        item["code"] = _code(item.get("code"))
        item["strategy_type"] = str(item.get("strategy_type") or PRIMARY_TV_STRATEGY)
        key = (item["code"], item["strategy_type"])
        if item["code"] and key not in merged:
            merged[key] = item
    return list(merged.values())


def _evaluate_a_eod_t1_plan(prior: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate the frozen signal-day plan using only next-session live prices."""
    code = _code(prior.get("code") or current.get("代码") or current.get("code"))
    name = current.get("名称") or current.get("name") or prior.get("name")
    entry = _number(prior.get("a_eod_t1_frozen_entry_price") or prior.get("confirmation_price"))
    stop = _number(prior.get("a_eod_t1_frozen_stop_price") or prior.get("stop_price"))
    target = _number(prior.get("a_eod_t1_frozen_target_price") or prior.get("target_price"))
    signal_price = _number(prior.get("signal_price"))
    current_price = _number(current.get("现价") or current.get("price"))
    open_price = _number(current.get("开盘") or current.get("open"))
    high_price = _number(current.get("最高") or current.get("high"))
    low_price = _number(current.get("最低") or current.get("low"))
    limit_up = _number(current.get("limit_up"))

    def rejected(reason: str, status: str = "NOT_CONFIRMED") -> Dict[str, Any]:
        return {
            **prior, "code": code, "name": name, "status": status,
            "instruction": "不可交易", "reason": reason,
            "current_price": current_price, "confirmation_price": entry,
            "stop_price": stop, "current_candidate": current,
        }

    if not entry or not stop or stop >= entry:
        return rejected("冻结交易计划缺少有效确认价或失效价")
    if not current_price or not open_price or not high_price or not low_price:
        return rejected("次日实时价格字段不完整")
    market_stage = str(current.get("market_sentiment_stage") or prior.get("market_stage") or "").upper()
    if market_stage in {"RETREAT", "ICE"}:
        return rejected("市场进入退潮/冰点，取消次日计划")
    if low_price <= stop:
        return rejected("盘中已跌破冻结失效价，计划取消")
    if limit_up and current_price >= limit_up - LIMIT_PRICE_TOLERANCE:
        return rejected("当前接近或封住涨停，无法按计划成交")

    board_limit = daily_limit_pct(code)
    if signal_price and (open_price / signal_price - 1) * 100 >= board_limit - 0.2:
        return rejected("开盘接近涨停，无法按计划成交")
    if current_price < entry:
        reason = "触发后已跌回冻结确认价下方" if high_price >= entry else "尚未触发并守住冻结确认价"
        status = "NOT_CONFIRMED" if high_price >= entry else "CONTINUE_WAIT"
        return rejected(reason, status=status)

    effective_entry = entry
    extension_pct = 0.0
    if open_price > entry:
        extension_pct = (open_price / entry - 1) * 100
        if extension_pct > A_EOD_MAX_ENTRY_EXTENSION_PCT:
            return rejected(f"开盘相对确认价偏离 {extension_pct:.1f}%，超过3%上限")
        effective_entry = open_price
    elif high_price < entry:
        return rejected("尚未触发并守住冻结确认价", status="CONTINUE_WAIT")

    tradable = dict(current)
    tradable.update({
        "代码": code,
        "名称": name,
        "strategy_type": str(prior.get("strategy_type") or current.get("strategy_type") or PRIMARY_TV_STRATEGY),
        "trade_eligible": True,
        "trade_bucket": "TRADE",
        "execution_review_state": "TRADE",
        "a_eod_t1_plan": True,
        "a_eod_t1_confirmed": True,
        "a_eod_t1_policy_version": prior.get("a_eod_t1_policy_version") or A_EOD_T1_POLICY_VERSION,
        "a_eod_t1_frozen_entry_price": entry,
        "a_eod_t1_frozen_stop_price": stop,
        "a_eod_t1_frozen_target_price": target,
        "a_eod_t1_entry_extension_pct": round(extension_pct, 2),
        "pa_execution_policy_version": prior.get("pa_execution_policy_version"),
        "pa_execution_tier": prior.get("pa_execution_tier") or "T1_CONFIRM",
        "pa_execution_tier_label": prior.get("pa_execution_tier_label") or "次日确认",
        "a_eod_t1_portfolio_cap_pct": A_EOD_T1_PORTFOLIO_CAP_PCT,
        "a_eod_t1_max_positions": A_EOD_T1_MAX_POSITIONS,
        "pa_entry_price": round(effective_entry, 2),
        "pa_stop_price": stop,
        "pa_target_price": target,
        "trade_blockers": [],
        "suggested_position_pct": A_EOD_T1_POSITION_PCT,
        "position_plan": {
            "initial_position_pct": A_EOD_T1_POSITION_PCT,
            "max_position_pct": A_EOD_T1_POSITION_PCT,
            "portfolio_cap_pct": A_EOD_T1_PORTFOLIO_CAP_PCT,
            "max_positions": A_EOD_T1_MAX_POSITIONS,
        },
        "execution_instruction": (
            f"可交易：参考价 {effective_entry:.2f}，止损 {stop:.2f}；"
            f"单票不超过{A_EOD_T1_POSITION_PCT:g}%"
        ),
    })
    return {
        **prior, "code": code, "name": name, "status": "CONFIRMED_TRADE",
        "instruction": "可交易",
        "reason": f"次日已触发并守住冻结确认价；开盘偏离 {extension_pct:.1f}%",
        "current_price": current_price, "confirmation_price": effective_entry,
        "stop_price": stop, "current_candidate": tradable,
    }


def evaluate_next_day_reviews(
    pending: Iterable[Dict[str, Any]],
    current_candidates: Iterable[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Use frozen T+1 plans or require a fresh supported TV trade state for legacy reviews."""
    current_by_key: dict[tuple[str, str], Dict[str, Any]] = {}
    current_by_code: dict[str, Dict[str, Any]] = {}
    for candidate in current_candidates:
        code = _code(candidate.get("代码") or candidate.get("code"))
        strategy = str(candidate.get("strategy_type") or PRIMARY_TV_STRATEGY)
        if code:
            current_by_key[(code, strategy)] = candidate
            current_by_code.setdefault(code, candidate)

    reviewed = []
    for prior in pending:
        code = _code(prior.get("code"))
        strategy = str(prior.get("strategy_type") or PRIMARY_TV_STRATEGY)
        current = current_by_key.get((code, strategy)) or current_by_code.get(code)
        if current is None:
            reviewed.append({
                **prior, "code": code, "status": "NOT_RESELECTED",
                "instruction": "不可交易", "reason": "未进入今日严格扫描结果",
                "current_candidate": None,
            })
            continue

        if prior.get("a_eod_t1_plan"):
            reviewed.append(_evaluate_a_eod_t1_plan(prior, current))
            continue

        detail = current.get("price_action_detail") or {}
        if not isinstance(detail, dict):
            detail = {}
        eligible = bool(current.get("trade_eligible") or detail.get("trade_eligible"))
        bucket = str(current.get("trade_bucket") or detail.get("trade_bucket") or "OBSERVE")
        review_state = str(current.get("execution_review_state") or detail.get("execution_review_state") or "")
        blockers = current.get("trade_blockers") or detail.get("trade_blockers") or []
        confirmed = eligible and bucket == "TRADE" and review_state in {"", "TRADE"}
        waiting = review_state in {"NEXT_DAY_REVIEW", "STRONG_WATCH"}
        reviewed.append({
            **prior,
            "code": code,
            "name": current.get("名称") or current.get("name") or prior.get("name"),
            "status": "CONFIRMED_TRADE" if confirmed else "CONTINUE_WAIT" if waiting else "NOT_CONFIRMED",
            "instruction": "可交易" if confirmed else "不可交易",
            "reason": "今日重新满足全部交易条件" if confirmed else str(blockers[0]) if blockers else "今日交易条件未全部通过",
            "current_price": _number(current.get("现价") or current.get("price")),
            "confirmation_price": _number(current.get("pa_entry_price") or detail.get("pa_entry_price") or prior.get("confirmation_price")),
            "stop_price": _number(current.get("pa_stop_price") or detail.get("pa_stop_price") or prior.get("stop_price")),
            "current_candidate": current,
        })
    return reviewed


def _merge_a_eod_t1_live_quotes(
    pending: Iterable[Dict[str, Any]],
    current_candidates: Iterable[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    candidates = [dict(item) for item in current_candidates]
    codes = [_code(item.get("code")) for item in pending if item.get("a_eod_t1_plan")]
    codes = [code for code in dict.fromkeys(codes) if code]
    if not codes:
        return candidates
    try:
        from core.direct_sources import tencent_quote
        quotes = tencent_quote(codes)
    except Exception:
        quotes = {}
    by_code = {_code(item.get("代码") or item.get("code")): item for item in candidates}
    for code in codes:
        quote = quotes.get(code) or {}
        if not quote:
            continue
        current = by_code.get(code, {"代码": code})
        current.update({
            "代码": code,
            "名称": quote.get("name") or current.get("名称") or current.get("name"),
            "现价": quote.get("price"),
            "开盘": quote.get("open"),
            "最高": quote.get("high"),
            "最低": quote.get("low"),
            "涨幅%": quote.get("change_pct"),
            "limit_up": quote.get("limit_up"),
            "limit_down": quote.get("limit_down"),
        })
        if code not in by_code:
            candidates.append(current)
            by_code[code] = current
    return candidates


def _body(
    items: list[Dict[str, Any]],
    review_date: Any,
    reviewed_count: int | None = None,
) -> str:
    confirmed = sum(item["status"] == "CONFIRMED_TRADE" for item in items)
    lines = [
        f"昨日待确认 {reviewed_count if reviewed_count is not None else len(items)} 只｜今日确认可交易 {confirmed} 只",
        "规则：只有明确显示“指令：可交易”才可执行。",
        "",
    ]
    icons = {"CONFIRMED_TRADE": "✅", "CONTINUE_WAIT": "⏳", "NOT_CONFIRMED": "⛔", "NOT_RESELECTED": "⛔"}
    for item in items:
        name = item.get("name") or "--"
        plan_label = "｜尾盘T1" if item.get("a_eod_t1_plan") else ""
        lines.append(f"{icons.get(item['status'], '⛔')} {name}({item['code']}){plan_label}｜指令：{item['instruction']}")
        price_parts = []
        if item.get("current_price"):
            price_parts.append(f"现价 {item['current_price']:g}")
        if item.get("confirmation_price"):
            price_parts.append(f"确认价 {item['confirmation_price']:g}")
        if item.get("stop_price"):
            price_parts.append(f"止损 {item['stop_price']:g}")
        if price_parts:
            lines.append("  " + "｜".join(price_parts))
        lines.append(f"  结论：{item['reason']}")
        if item["status"] == "CONFIRMED_TRADE" and item.get("a_eod_t1_plan"):
            lines.append(
                f"  仓位：单票≤{A_EOD_T1_POSITION_PCT:g}%｜"
                f"组合≤{A_EOD_T1_PORTFOLIO_CAP_PCT:g}%｜最多{A_EOD_T1_MAX_POSITIONS}只"
            )
    lines.extend(["", f"复核来源：{review_date} 冻结收盘计划；普通候选按今日严格策略复核。"])
    return "\n".join(lines)


def send_next_day_confirmation(
    engine,
    current_candidates: Iterable[Dict[str, Any]],
    now: datetime | None = None,
) -> Dict[str, Any]:
    now = now or datetime.now()
    pending = load_pending_next_day_reviews(engine, as_of=now.date())
    if not pending:
        return {"reviewed": 0, "confirmed": 0, "bark": False, "reason": "no_pending_reviews", "items": []}
    current = _merge_a_eod_t1_live_quotes(pending, current_candidates)
    items = evaluate_next_day_reviews(pending, current)
    review_date = pending[0].get("signal_date")
    confirmed_items = [item for item in items if item["status"] == "CONFIRMED_TRADE"]
    bark = False
    if confirmed_items:
        body = _body(confirmed_items, review_date, reviewed_count=len(items))
        from core.notifier import notifier
        delivery = asyncio.run(notifier.send(
            f"Alpha Vision 隔日确认 {now.strftime('%H:%M')}",
            body,
            channels=["bark"],
            group="AlphaVision_Confirmation",
            enqueue_failed=False,
        ))
        bark = bool(delivery.get("bark"))

    from core.audit_log import record_lifecycle_event
    for item in items:
        record_lifecycle_event(
            "NEXT_DAY_CONFIRMATION",
            source="bark_next_day_confirmation",
            code=item["code"],
            name=item.get("name"),
            strategy_type=item.get("strategy_type"),
            payload={
                "review_date": str(review_date), "status": item["status"],
                "instruction": item["instruction"], "reason": item["reason"],
                "bark_delivered": bark,
            },
        )

    if bark:
        confirmed_candidates = [
            item["current_candidate"] for item in confirmed_items
            if item.get("current_candidate")
        ]
        if confirmed_candidates:
            from core.execution_intents import create_bark_execution_intents
            create_bark_execution_intents(confirmed_candidates, engine, issued_at=now)
    return {
        "reviewed": len(items),
        "confirmed": sum(item["status"] == "CONFIRMED_TRADE" for item in items),
        "bark": bark,
        "reason": "confirmed_trade" if confirmed_items else "no_confirmed_trade",
        "review_date": str(review_date),
        "items": items,
    }
