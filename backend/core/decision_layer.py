"""Market-context decision layer for scan results.

This module does not create buy signals. It converts existing market, sector,
price-action, and money-flow evidence into execution permissions and sizing.
"""
from typing import Any, Dict, List

import pandas as pd

from core.sector_strength import classify_mainline_sector


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def build_market_decision_context(snapshot: pd.DataFrame, market_regime: Dict[str, Any]) -> Dict[str, Any]:
    """Classify short-term market emotion and provide a portfolio exposure cap."""
    regime = str(market_regime.get("status") or "UNKNOWN").upper()
    if snapshot is None or snapshot.empty or "pct_chg" not in snapshot.columns:
        score = {"OFFENSIVE": 72, "DEFENSIVE": 42, "CRITICAL": 18}.get(regime, 45)
        advance_ratio = strong_ratio = weak_ratio = limit_up_ratio = limit_down_ratio = 0.0
    else:
        pct = pd.to_numeric(snapshot["pct_chg"], errors="coerce").dropna()
        total = max(1, len(pct))
        advance_ratio = float((pct > 0).sum() / total * 100)
        strong_ratio = float((pct >= 5).sum() / total * 100)
        weak_ratio = float((pct <= -5).sum() / total * 100)
        limit_up_ratio = float((pct >= 9.8).sum() / total * 100)
        limit_down_ratio = float((pct <= -9.8).sum() / total * 100)
        breadth_score = _clamp((advance_ratio - 25) * 1.6)
        strength_score = _clamp(50 + (strong_ratio - weak_ratio) * 8)
        limit_score = _clamp(50 + (limit_up_ratio - limit_down_ratio) * 12)
        regime_score = {"OFFENSIVE": 90, "DEFENSIVE": 45, "CRITICAL": 10}.get(regime, 50)
        score = round(breadth_score * 0.35 + strength_score * 0.25 + limit_score * 0.2 + regime_score * 0.2, 1)

    if regime == "CRITICAL" or score < 25:
        stage, label, max_position = "RETREAT", "退潮", 10
        allowed, forbidden = ["处理风险、观察修复"], ["新增仓位", "高位追涨", "非主流交易"]
    elif score < 42:
        stage, label, max_position = "ICE", "冰点", 20
        allowed, forbidden = ["主流核心轻仓试错"], ["重仓", "跟风股", "无确认追涨"]
    elif score < 58:
        stage, label, max_position = "REPAIR", "修复", 40
        allowed, forbidden = ["首批主动走强", "主流核心回踩"], ["非主流重仓", "高位跟风"]
    elif score < 78:
        stage, label, max_position = "ADVANCE", "主升", 70
        allowed, forbidden = ["主流核心", "确认后加仓"], ["无主线交易", "冲高追价"]
    else:
        stage, label, max_position = "CLIMAX", "高潮", 50
        allowed, forbidden = ["核心持有", "分歧低吸"], ["盲目追高", "后排跟风"]

    return {
        "market_sentiment_stage": stage,
        "market_sentiment_label": label,
        "market_sentiment_score": round(score, 1),
        "market_regime": regime,
        "portfolio_position_cap_pct": max_position,
        "market_allowed_actions": allowed,
        "market_forbidden_actions": forbidden,
        "market_breadth": {
            "advance_ratio": round(advance_ratio, 1),
            "strong_ratio": round(strong_ratio, 1),
            "weak_ratio": round(weak_ratio, 1),
            "limit_up_ratio": round(limit_up_ratio, 2),
            "limit_down_ratio": round(limit_down_ratio, 2),
        },
    }


def _leadership_score(stock: Dict[str, Any]) -> float:
    role_score = {"LEADER": 92, "CORE": 78, "FOLLOWER": 48, "LAGGARD": 20}.get(stock.get("sector_role"), 40)
    alignment = _num(stock.get("sector_alignment_score"), 40)
    relative = _clamp(50 + _num(stock.get("sector_relative_pct")) * 10)
    active = _clamp(50 + _num(stock.get("涨幅%")) * 5)
    return round(role_score * 0.4 + alignment * 0.3 + relative * 0.2 + active * 0.1, 1)


def _money_flow_score(stock: Dict[str, Any]) -> float:
    flow = stock.get("money_flow") or {}
    ratio = _num(flow.get("main_net_ratio"))
    amount = _num(flow.get("main_net_inflow_yi"))
    return round(_clamp(50 + ratio * 2 + amount * 3), 1)


def _risk_reward_score(stock: Dict[str, Any]) -> float:
    rr = _num(stock.get("pa_risk_reward") or stock.get("risk_reward"))
    trap = _num(stock.get("pa_trap_risk"))
    failure = _num(stock.get("pa_failure_risk"))
    return round(_clamp(rr * 25 + 45 - trap * 0.2 - failure * 0.15), 1)


def _position_plan(score: float, market_cap: int, mainline: str, blocked: bool) -> Dict[str, Any]:
    if blocked or score < 60:
        base = 0
        label = "观望"
    elif score < 70:
        base, label = 5, "试错仓"
    elif score < 80:
        base, label = 10, "标准仓"
    elif score < 90:
        base, label = 15, "重点仓"
    else:
        base, label = 20, "高确定性候选"
    if mainline in {"NON_MAIN", "FADING"}:
        base = min(base, 5)
    return {
        "label": label if base else "观望",
        "initial_position_pct": min(base, market_cap),
        "max_position_pct": min(base + 5 if base else 0, market_cap),
        "portfolio_position_cap_pct": market_cap,
    }


def _trade_state(stock: Dict[str, Any], position: Dict[str, Any]) -> str:
    if stock.get("trade_bucket") == "BLOCK" or position["initial_position_pct"] == 0:
        return "BLOCKED"
    action = str((stock.get("pa_trade_plan") or {}).get("action") or stock.get("pa_trade_action") or "").upper()
    pullback = stock.get("pa_pullback_status")
    if stock.get("trade_bucket") != "TRADE":
        return "WATCHLIST"
    if action == "READY" and pullback == "CONFIRMED":
        return "CONFIRM_ADD"
    if action == "READY":
        return "PROBE"
    return "WATCHLIST"


def _execution_instruction(stock: Dict[str, Any], state: str, position: Dict[str, Any]) -> str:
    entry = _num(stock.get("pa_entry_price") or stock.get("entry_price"))
    stop = _num(stock.get("pa_stop_price") or stock.get("plan_stop_price") or stock.get("stop_price"))
    support = _num(stock.get("pa_pullback_support_price"))
    confirm = _num(stock.get("pa_pullback_confirmation_price") or entry)
    confirm_text = f"站稳 {confirm:.2f} 且量能确认" if confirm > 0 else "价量结构重新确认"
    stop_text = f"跌破 {stop:.2f} 退出" if stop > 0 else "结构失效立即退出"
    if state == "BLOCKED":
        return f"禁止新增仓位；{f'跌破 {stop:.2f} 取消观察' if stop else '等待市场、板块与结构重新确认'}"
    if state == "WATCHLIST":
        return f"仅观察；{confirm_text}后复核，{stop_text}"
    if state == "PROBE":
        support_text = f"回踩 {support:.2f} 附近承接后" if support > 0 else "结构承接确认后"
        return f"{support_text}试错 {position['initial_position_pct']}%，{confirm_text}再复核加仓，{stop_text}"
    return f"{confirm_text}，可执行 {position['initial_position_pct']}%-{position['max_position_pct']}%；{stop_text}"


def apply_decision_layer(
    results: List[Dict[str, Any]],
    snapshot: pd.DataFrame,
    market_regime: Dict[str, Any],
) -> Dict[str, Any]:
    """Enrich scan results with market permission, opportunity score, and execution state."""
    context = build_market_decision_context(snapshot, market_regime)
    stage_score = _num(context["market_sentiment_score"])
    market_cap = int(context["portfolio_position_cap_pct"])
    for stock in results:
        mainline = classify_mainline_sector(stock)
        leader_score = _leadership_score(stock)
        sector_score = _num(stock.get("sector_momentum_score"), 40)
        technical_score = _clamp(_num(stock.get("final_trade_score") or stock.get("final_rank_score") or stock.get("Score")))
        flow_score = _money_flow_score(stock)
        risk_score = _risk_reward_score(stock)
        opportunity = round(
            stage_score * 0.2
            + sector_score * 0.2
            + leader_score * 0.15
            + technical_score * 0.2
            + flow_score * 0.1
            + risk_score * 0.15,
            1,
        )
        market_blocked = context["market_sentiment_stage"] == "RETREAT"
        sector_blocked = mainline == "FADING"
        score_blocked = opportunity < 60
        blocked = market_blocked or sector_blocked or score_blocked or stock.get("trade_bucket") == "BLOCK"
        position = _position_plan(opportunity, market_cap, mainline, blocked)
        state = _trade_state(stock, position)

        stock.update(context)
        stock["sector_mainline"] = mainline
        stock["leadership_score"] = leader_score
        stock["trade_opportunity_score"] = opportunity
        stock["trade_opportunity_label"] = position["label"]
        stock["position_plan"] = position
        stock["trade_state"] = state
        stock["execution_instruction"] = _execution_instruction(stock, state, position)
        stock["decision_score_components"] = {
            "market": stage_score,
            "sector": sector_score,
            "leadership": leader_score,
            "technical": round(technical_score, 1),
            "money_flow": flow_score,
            "risk_reward": risk_score,
        }
        if blocked:
            blockers = list(stock.get("trade_blockers") or [])
            for reason, active in (
                ("市场退潮，暂停新增仓位", market_blocked),
                ("板块退潮，暂停新增仓位", sector_blocked),
                ("综合机会分<60，暂不交易", score_blocked),
            ):
                if active and reason not in blockers:
                    blockers.append(reason)
            stock["trade_blockers"] = blockers
            stock["trade_eligible"] = False
            if stock.get("trade_bucket") != "BLOCK":
                stock["trade_bucket"] = "OBSERVE"
    return context
