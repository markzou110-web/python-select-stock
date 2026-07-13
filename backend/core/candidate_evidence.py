"""Deterministic, point-in-time evidence quality for scan candidates."""
from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, time
from typing import Any, Callable, Dict, Iterable, Optional


CONTRACT_VERSION = "candidate-evidence-v1"
VALID_MODES = {"OFF", "SHADOW", "ENFORCED"}
PASS_GRADES = {"A", "B"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _as_of_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.max)
    text = str(value or "").strip()
    if not text:
        return datetime.now()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return datetime.combine(parsed.date(), time.max) if len(text) <= 10 else parsed
    except ValueError:
        return datetime.now()


def _date_status(observed_at: Any, as_of: datetime) -> str:
    if not observed_at:
        return "AVAILABLE"
    observed = _as_of_datetime(observed_at)
    if observed.tzinfo and not as_of.tzinfo:
        observed = observed.replace(tzinfo=None)
    elif as_of.tzinfo and not observed.tzinfo:
        as_of = as_of.replace(tzinfo=None)
    return "FUTURE_DATA" if observed > as_of else "AVAILABLE"


def _domain(
    requirement: str,
    status: str,
    source: str,
    observed_at: Any,
    items: Dict[str, Any],
    missing_reason: str | None = None,
) -> Dict[str, Any]:
    return {
        "requirement": requirement,
        "status": status,
        "source": source,
        "observed_at": str(observed_at) if observed_at else None,
        "freshness": "STALE" if status == "STALE" else ("UNKNOWN" if status in {"UNAVAILABLE", "INVALID"} else "FRESH"),
        "items": items,
        "missing_reason": missing_reason,
    }


def _research_domain(
    research: Optional[Dict[str, Any]],
    keys: tuple[str, ...],
    source: str,
) -> Dict[str, Any]:
    if research is None:
        return _domain("OPTIONAL", "UNAVAILABLE", source, None, {}, "NOT_COLLECTED")
    statuses = research.get("source_status") or {}
    errors = research.get("errors") or {}
    selected = {key: research.get(key) for key in keys if key in research}
    failed = [key for key in keys if key in errors]
    stale = [key for key in keys if (statuses.get(key) or {}).get("status") == "STALE"]
    if stale:
        return _domain("OPTIONAL", "STALE", source, research.get("updated_at"), selected, "STALE_CACHE_ONLY")
    if failed and len(failed) == len(keys):
        reason = (statuses.get(failed[0]) or {}).get("reason") or "SOURCE_UNAVAILABLE"
        return _domain("OPTIONAL", "UNAVAILABLE", source, research.get("updated_at"), selected, str(reason))
    if not selected:
        return _domain("OPTIONAL", "UNAVAILABLE", source, research.get("updated_at"), {}, "NOT_COLLECTED")
    return _domain("OPTIONAL", "AVAILABLE", source, research.get("updated_at"), selected)


def _quality(domains: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    quote_status = domains["quote"]["status"]
    core_failures = [
        name for name in ("execution_plan", "technical_structure")
        if domains[name]["status"] != "AVAILABLE"
    ]
    event = domains["event_catalyst"]
    optional_failures = [
        name for name in ("money_flow", "supply_risk", "fundamentals", "news_policy")
        if domains[name]["status"] not in {"AVAILABLE", "NOT_APPLICABLE"}
    ]
    degraded = [
        name for name in ("market_regime", "sector_context")
        if domains[name]["status"] != "AVAILABLE"
    ]

    if quote_status in {"INVALID", "FUTURE_DATA"}:
        grade, status = "F", "BLOCKED"
        reasons = [f"QUOTE_{quote_status}"]
    elif quote_status != "AVAILABLE" or core_failures or (
        event["requirement"] == "CONDITIONAL_REQUIRED" and event["status"] != "AVAILABLE"
    ):
        grade, status = "D", "BLOCKED"
        reasons = [f"REQUIRED_{name.upper()}_{domains[name]['status']}" for name in core_failures]
        if quote_status != "AVAILABLE":
            reasons.insert(0, f"QUOTE_{quote_status}")
        if event["requirement"] == "CONDITIONAL_REQUIRED" and event["status"] != "AVAILABLE":
            reasons.append(f"REQUIRED_EVENT_CATALYST_{event['status']}")
    elif degraded:
        grade, status = "C", "DEGRADED"
        reasons = [f"{name.upper()}_{domains[name]['status']}" for name in degraded]
    elif optional_failures:
        grade, status = "B", "PASS"
        reasons = [f"OPTIONAL_{name.upper()}_{domains[name]['status']}" for name in optional_failures]
    else:
        grade, status, reasons = "A", "PASS", []

    summaries = {
        "A": "核心与研究证据完整",
        "B": "核心证据完整，部分可选研究证据缺失或降级",
        "C": "市场或板块证据不足，仅观察等待确认",
        "D": "核心必需证据缺失、过期或不一致",
        "F": "行情或证据输入无效，禁止交易",
    }
    return {
        "grade": grade,
        "status": status,
        "reason_codes": reasons,
        "summary": summaries[grade],
        "evaluated_by": "RULES",
    }


def _decision_memo(
    candidate: Dict[str, Any],
    research: Optional[Dict[str, Any]],
    domains: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    bull: list[Dict[str, Any]] = []
    bear: list[Dict[str, Any]] = []

    def add(target: list[Dict[str, Any]], text: str, evidence_id: str) -> None:
        if text and text not in {item["text"] for item in target} and len(target) < 3:
            target.append({"text": text, "evidence_ids": [evidence_id]})

    if candidate.get("共振") == "🔥 核心热点" or str(candidate.get("sector_mainline") or "").upper() in {"ACTIVE", "LEADING"}:
        add(bull, "板块主线与个股方向形成共振", "sector_context.mainline")
    if candidate.get("pa_volume_confirmed"):
        add(bull, "价格结构已获得量能确认", "technical_structure.volume")
    if str(candidate.get("price_action_regime") or "") in {"向上突破", "多头趋势"}:
        add(bull, f"价格行为处于{candidate.get('price_action_regime')}", "technical_structure.regime")
    catalyst = candidate.get("event_catalyst") or {}
    if catalyst.get("verified"):
        add(bull, str(catalyst.get("title") or "官方事件催化已验证"), "event_catalyst.primary")

    summary = (research or {}).get("summary") or {}
    for item in summary.get("opportunity_flags") or []:
        add(bull, str(item), "research.opportunity")
    for item in candidate.get("trade_blockers") or []:
        add(bear, str(item), "execution_plan.blocker")
    for item in summary.get("risk_flags") or []:
        add(bear, str(item), "research.risk")
    if not bear and _number(candidate.get("涨幅%") or candidate.get("pct")) and _number(candidate.get("涨幅%") or candidate.get("pct")) > 7:
        add(bear, "短线涨幅偏高，仍需遵守不追高规则", "technical_structure.extension")

    unknowns = []
    for name, domain in domains.items():
        if domain["status"] not in {"AVAILABLE", "NOT_APPLICABLE"}:
            unknowns.append(f"{name}: {domain.get('missing_reason') or domain['status']}")

    invalidation = candidate.get("pa_invalidation") or candidate.get("pa_pullback_invalidation_price")
    stop = _number(candidate.get("pa_stop_price") or candidate.get("stop_price"))
    invalidation_conditions = []
    if invalidation:
        invalidation_conditions.append(str(invalidation))
    elif stop:
        invalidation_conditions.append(f"跌破计划止损价 {stop:g}")
    if str(candidate.get("sector_mainline") or "").upper() not in {"", "UNKNOWN"}:
        invalidation_conditions.append("板块主线转为FADING或市场进入退潮阶段")

    return {
        "bull_case": bull,
        "bear_case": bear,
        "unknowns": unknowns[:3],
        "invalidation_conditions": invalidation_conditions[:2],
        "summary": "；".join([item["text"] for item in bull[:2]]) or "暂无足够的确定性看多证据",
        "generated_by": "RULES",
    }


def build_candidate_evidence(
    candidate: Dict[str, Any],
    research: Optional[Dict[str, Any]] = None,
    as_of: Any = None,
) -> Dict[str, Any]:
    """Build a deterministic evidence bundle without changing trade eligibility."""
    as_of_dt = _as_of_datetime(as_of or candidate.get("as_of") or candidate.get("data_date"))
    code = str(candidate.get("代码") or candidate.get("code") or "").zfill(6)
    strategy = str(candidate.get("strategy_type") or "squeeze")
    identity = f"{code}:{strategy}:{as_of_dt.isoformat()}"
    evidence_id = f"ev_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"

    price = _number(candidate.get("现价") or candidate.get("price") or candidate.get("收盘"))
    quote_at = candidate.get("quote_updated_at") or candidate.get("snapshot_time") or as_of_dt.isoformat()
    quote_status = "AVAILABLE" if price and price > 0 else "INVALID"
    if quote_status == "AVAILABLE":
        quote_status = _date_status(quote_at, as_of_dt)
    if candidate.get("snapshot_stale") or "过期" in str(candidate.get("data_source") or candidate.get("source") or ""):
        quote_status = "STALE"

    entry = _number(candidate.get("pa_entry_price") or candidate.get("entry_price") or candidate.get("frozen_confirmation_price"))
    stop = _number(candidate.get("pa_stop_price") or candidate.get("stop_price") or candidate.get("frozen_stop_price"))
    target = _number(candidate.get("pa_target_price") or candidate.get("target_price") or candidate.get("frozen_target_price"))
    execution_valid = bool(entry and stop and entry > stop)
    technical_valid = any(candidate.get(key) not in (None, "", []) for key in (
        "price_action_score", "price_action_signal", "price_action_regime", "pa_trade_plan"
    ))
    market_value = candidate.get("market_sentiment_stage") or candidate.get("market_regime")
    sector_value = candidate.get("sector_mainline") or candidate.get("sector_trend") or candidate.get("sector_alignment_score")

    event_required = bool(candidate.get("event_driven_candidate"))
    catalyst = candidate.get("event_catalyst") or {}
    event_status = "NOT_APPLICABLE"
    event_reason = None
    if event_required:
        if not catalyst or not catalyst.get("published_at") or not catalyst.get("source_url"):
            event_status, event_reason = "UNAVAILABLE", "REQUIRED_FIELD_MISSING"
        elif not catalyst.get("verified"):
            event_status, event_reason = "UNAVAILABLE", "EVENT_NOT_VERIFIED"
        else:
            event_status = _date_status(catalyst.get("published_at"), as_of_dt)
            event_reason = "FUTURE_DATA_REJECTED" if event_status == "FUTURE_DATA" else None

    domains = {
        "quote": _domain("CORE", quote_status, str(candidate.get("data_source") or "scan_snapshot"), quote_at, {
            "price": price,
            "pct": _number(candidate.get("涨幅%") or candidate.get("pct")),
            "limit_state": candidate.get("limit_up_status") or "UNKNOWN",
        }, None if quote_status == "AVAILABLE" else f"QUOTE_{quote_status}"),
        "execution_plan": _domain("CORE", "AVAILABLE" if execution_valid else "UNAVAILABLE", "execution_profile", as_of_dt.isoformat(), {
            "entry": entry, "stop": stop, "target": target,
            "risk_reward": _number(candidate.get("pa_risk_reward")),
        }, None if execution_valid else "ENTRY_OR_STOP_MISSING"),
        "technical_structure": _domain("CORE", "AVAILABLE" if technical_valid else "UNAVAILABLE", "price_action", as_of_dt.isoformat(), {
            "regime": candidate.get("price_action_regime"),
            "signal": candidate.get("price_action_signal"),
            "volume_confirmed": bool(candidate.get("pa_volume_confirmed")),
        }, None if technical_valid else "TECHNICAL_STRUCTURE_MISSING"),
        "market_regime": _domain("REQUIRED", "AVAILABLE" if market_value else "UNAVAILABLE", "decision_layer", as_of_dt.isoformat(), {
            "stage": market_value,
            "position_cap_pct": _number(candidate.get("portfolio_position_cap_pct")),
        }, None if market_value else "MARKET_REGIME_MISSING"),
        "sector_context": _domain("REQUIRED", "AVAILABLE" if sector_value not in (None, "") else "UNAVAILABLE", "sector_strength", as_of_dt.isoformat(), {
            "mainline": candidate.get("sector_mainline"),
            "trend": candidate.get("sector_trend"),
            "alignment": _number(candidate.get("sector_alignment_score")),
        }, None if sector_value not in (None, "") else "SECTOR_CONTEXT_MISSING"),
        "event_catalyst": _domain("CONDITIONAL_REQUIRED" if event_required else "OPTIONAL", event_status, "event_catalyst", catalyst.get("published_at"), catalyst, event_reason),
        "money_flow": _research_domain(research, ("money_flow", "intraday_fund_flow", "dragon_tiger", "daily_dragon_tiger"), "research_sources"),
        "supply_risk": _research_domain(research, ("lockup", "holder_count", "block_trade"), "research_sources"),
        "fundamentals": _research_domain(research, ("reports", "dividends"), "research_sources"),
        "news_policy": _research_domain(research, ("news", "announcements"), "research_sources"),
    }
    quality = _quality(domains)
    return {
        "contract_version": CONTRACT_VERSION,
        "evidence_id": evidence_id,
        "code": code,
        "name": candidate.get("名称") or candidate.get("name"),
        "strategy_type": strategy,
        "signal_time": as_of_dt.isoformat(),
        "as_of": as_of_dt.isoformat(),
        "pipeline_stage": "DECISION_READY",
        "domains": domains,
        "quality": quality,
        "decision_memo": _decision_memo(candidate, research, domains),
    }


def apply_candidate_evidence(
    candidates: Iterable[Dict[str, Any]],
    as_of: Any = None,
    mode: str = "SHADOW",
    research_lookup: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Attach evidence metadata; only ENFORCED mode may lower permissions."""
    selected_mode = str(mode or "SHADOW").upper()
    if selected_mode not in VALID_MODES:
        selected_mode = "SHADOW"
    summary = {"mode": selected_mode, "processed": 0, "grades": {}, "downgraded": 0}
    if selected_mode == "OFF":
        return summary
    for candidate in candidates:
        research = research_lookup(candidate) if research_lookup else None
        bundle = build_candidate_evidence(candidate, research=research, as_of=as_of)
        quality = bundle["quality"]
        candidate.update({
            "evidence_id": bundle["evidence_id"],
            "evidence_grade": quality["grade"],
            "evidence_status": quality["status"],
            "evidence_summary": quality["summary"],
            "evidence_reason_codes": quality["reason_codes"],
            "evidence_gate_mode": selected_mode,
            "evidence_pipeline_stage": bundle["pipeline_stage"],
            "evidence_bundle": bundle,
            "decision_memo": bundle["decision_memo"],
        })
        summary["processed"] += 1
        summary["grades"][quality["grade"]] = summary["grades"].get(quality["grade"], 0) + 1
        if selected_mode != "ENFORCED" or quality["grade"] in PASS_GRADES or not candidate.get("trade_eligible"):
            continue
        candidate["trade_eligible"] = False
        candidate["trade_bucket"] = "OBSERVE" if quality["grade"] == "C" else "BLOCK"
        candidate["trade_execution_policy"] = "WAIT_EVIDENCE" if quality["grade"] == "C" else "NO_TRADE"
        candidate["requires_bark_confirmation"] = True
        blockers = list(candidate.get("trade_blockers") or [])
        blockers.append(f"证据质量{quality['grade']}级：{quality['summary']}")
        candidate["trade_blockers"] = list(dict.fromkeys(blockers))
        summary["downgraded"] += 1
    return summary
