"""Market-context decision layer for scan results.

This module does not create buy signals. It converts existing market, sector,
price-action, and money-flow evidence into execution permissions and sizing.
"""
from typing import Any, Dict, List
import math

import pandas as pd

from core.risk_constants import (
    A_EOD_MAX_CONCURRENT_POSITIONS,
    A_EOD_PORTFOLIO_CAP_PCT,
    A_EOD_POSITION_PCT,
    A_MINUS_TRIAL_PORTFOLIO_CAP_PCT,
    A_MINUS_TRIAL_POSITION_PCT,
    MAX_PORTFOLIO_RISK_PER_TRADE_PCT,
    PRIMARY_TV_STRATEGY,
    SOP_A_GRADE_STRATEGIES,
    STRUCTURAL_REPAIR_MAX_WEAK_RATIO,
    STRUCTURAL_REPAIR_MIN_ADVANCE_RATIO,
    STRUCTURAL_REPAIR_MIN_AVG_RETURN_PCT,
    STRUCTURAL_REPAIR_MIN_SEGMENT_COUNT,
    STRUCTURAL_REPAIR_MIN_STRONG_RATIO,
)
from sqlalchemy import text

from core.sector_strength import classify_mainline_sector


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def build_growth_segment_context(
    snapshot: pd.DataFrame,
    market_regime: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Measure growth-board breadth without changing the global market regime."""
    if snapshot is None or snapshot.empty or not {"code", "pct_chg"}.issubset(snapshot.columns):
        return {}

    codes = snapshot["code"].astype(str).str.zfill(6)
    pct = pd.to_numeric(snapshot["pct_chg"], errors="coerce")
    global_status = str(market_regime.get("status") or "UNKNOWN").upper()
    segments: Dict[str, Dict[str, Any]] = {}
    for label, prefixes in (("创业板", ("300", "301")), ("科创板", ("688", "689"))):
        values = pct[codes.str.startswith(prefixes)].dropna()
        count = int(len(values))
        if not count:
            continue
        advance_ratio = float((values > 0).mean() * 100)
        strong_ratio = float((values >= 5).mean() * 100)
        weak_ratio = float((values <= -5).mean() * 100)
        avg_return = float(values.mean())
        structural_repair = (
            global_status in {"CRITICAL", "DEFENSIVE"}
            and count >= STRUCTURAL_REPAIR_MIN_SEGMENT_COUNT
            and advance_ratio >= STRUCTURAL_REPAIR_MIN_ADVANCE_RATIO
            and strong_ratio >= STRUCTURAL_REPAIR_MIN_STRONG_RATIO
            and avg_return >= STRUCTURAL_REPAIR_MIN_AVG_RETURN_PCT
            and weak_ratio <= STRUCTURAL_REPAIR_MAX_WEAK_RATIO
        )
        segments[label] = {
            "stage": "STRUCTURAL_REPAIR" if structural_repair else "NEUTRAL",
            "label": "结构性强修复" if structural_repair else "未形成独立强势",
            "count": count,
            "advance_ratio": round(advance_ratio, 1),
            "strong_ratio": round(strong_ratio, 1),
            "weak_ratio": round(weak_ratio, 1),
            "avg_return_pct": round(avg_return, 2),
        }
    return segments


def apply_growth_segment_context(
    results: List[Dict[str, Any]],
    snapshot: pd.DataFrame,
    market_regime: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Attach board-specific repair evidence before SOP grading."""
    segments = build_growth_segment_context(snapshot, market_regime)
    global_status = str(market_regime.get("status") or "UNKNOWN").upper()
    for stock in results:
        code = str(stock.get("代码") or stock.get("code") or "").zfill(6)
        segment = "创业板" if code.startswith(("300", "301")) else "科创板" if code.startswith(("688", "689")) else None
        detail = segments.get(segment or "", {})
        stock["market_segment"] = segment or "其他"
        stock["market_segment_stage"] = detail.get("stage", "NEUTRAL")
        stock["market_segment_label"] = detail.get("label", "跟随全市场")
        stock["market_segment_breadth"] = detail
        stock["effective_market_regime"] = (
            "DEFENSIVE"
            if global_status == "CRITICAL" and detail.get("stage") == "STRUCTURAL_REPAIR"
            else global_status
        )
    return segments


def load_market_cycle_history(engine, days: int = 10) -> List[Dict[str, Any]]:
    """Build daily breadth history for cycle classification.

    优先用 breadth_history 表的 MARKET 行（盘中实时聚合写入），修复 6/22 节后首日 bug：
    节后首日 daily_k 还是上个交易日数据，导致 cycle_history 缺今日宽度、情绪误判。
    新表空时回退 daily_k 聚合逻辑（向下兼容）。
    """
    if engine is None:
        return []

    # ── 优先尝试 breadth_history MARKET 行 ──
    try:
        bh = pd.read_sql(text("""
            SELECT bar_date, advance_ratio, strong_ratio, weak_ratio, avg_return
            FROM breadth_history
            WHERE scope = 'MARKET'
            ORDER BY bar_date DESC
            LIMIT :days
        """), engine, params={"days": int(days)})
    except Exception:
        bh = pd.DataFrame()

    if not bh.empty:
        history: List[Dict[str, Any]] = []
        for _, row in bh.iterrows():
            history.append({
                "date": str(row["bar_date"]),
                "advance_ratio": round(float(row["advance_ratio"] or 0), 1),
                "strong_ratio": round(float(row["strong_ratio"] or 0), 1),
                "weak_ratio": round(float(row["weak_ratio"] or 0), 1),
                "avg_return": round(float(row["avg_return"] or 0), 2),
            })
        return sorted(history, key=lambda item: item["date"])[-days:]

    # ── 回退：从 daily_k 聚合 ──
    try:
        query = """
            WITH recent_dates AS (
                SELECT DISTINCT date FROM daily_k ORDER BY date DESC LIMIT :date_limit
            )
            SELECT code, date, close
            FROM daily_k
            WHERE date IN (SELECT date FROM recent_dates)
            ORDER BY code, date
        """
        frame = pd.read_sql(text(query), engine, params={"date_limit": int(days) + 1})
    except Exception:
        return []
    if frame.empty:
        return []

    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["close"]).sort_values(["code", "date"])
    frame["pct_chg"] = frame.groupby("code")["close"].pct_change() * 100
    frame = frame.dropna(subset=["pct_chg"])
    history = []
    for date, group in frame.groupby("date"):
        pct = group["pct_chg"]
        total = max(1, len(pct))
        history.append({
            "date": str(date),
            "advance_ratio": round(float((pct > 0).sum() / total * 100), 1),
            "strong_ratio": round(float((pct >= 5).sum() / total * 100), 1),
            "weak_ratio": round(float((pct <= -5).sum() / total * 100), 1),
            "avg_return": round(float(pct.mean()), 2),
        })
    return sorted(history, key=lambda item: item["date"])[-days:]


def build_market_decision_context(
    snapshot: pd.DataFrame,
    market_regime: Dict[str, Any],
    cycle_history: List[Dict[str, Any]] | None = None,
    data_date: str | None = None,
) -> Dict[str, Any]:
    """Classify the multi-day emotion cycle and provide execution permissions.

    改动 A3：新增 data_date 参数。原逻辑用 pd.Timestamp.now().date() 判断 cycle_history
    最后一条是否为"当日"需剔除，但在周末/节假日扫描时 now() 是非交易日，与 cycle 最后
    一条（最近交易日）不匹配 → 不剔除 → prior3 重复计入当日宽度 → trend 偏移 →
    仓位上限可能从 70% 错降到 10%。data_date 应传 scanner 的 max_date（最新数据日）。
    """
    regime = str(market_regime.get("status") or "UNKNOWN").upper()
    # 改动 A3：用 data_date（扫描的数据日）替代墙钟 now()。data_date 为空时回退到 now()。
    _today_str = data_date or str(pd.Timestamp.now().date())
    # 当 snapshot 为空（盘前/无实时数据）时，回退到 cycle_history 最后一天作为"当前"宽度，
    # 避免 advance_ratio=0 导致 trend=0-prior3 大幅负值 → 误判 RETREAT。
    _cycle = list(cycle_history or [])
    _fallback = _cycle[-1] if _cycle else {}
    current_cycle_date = None
    if snapshot is None or snapshot.empty or "pct_chg" not in snapshot.columns:
        advance_ratio = float(_fallback.get("advance_ratio", 0.0))
        strong_ratio = float(_fallback.get("strong_ratio", 0.0))
        weak_ratio = float(_fallback.get("weak_ratio", 0.0))
        limit_up_count = 0
        limit_down_count = 0
        limit_up_ratio = 0.0
        limit_down_ratio = 0.0
        current_cycle_date = _fallback.get("date")
    else:
        pct = pd.to_numeric(snapshot["pct_chg"], errors="coerce").dropna()
        total = max(1, len(pct))
        advance_ratio = float((pct > 0).sum() / total * 100)
        strong_ratio = float((pct >= 5).sum() / total * 100)
        weak_ratio = float((pct <= -5).sum() / total * 100)
        limit_up_count = int((pct >= 9.8).sum())
        limit_down_count = int((pct <= -9.8).sum())
        limit_up_ratio = float((pct >= 9.8).sum() / total * 100)
        limit_down_ratio = float((pct <= -9.8).sum() / total * 100)

    reported_limit_down_count = market_regime.get("limit_down_count")
    if isinstance(reported_limit_down_count, int) and reported_limit_down_count >= 0:
        limit_down_count = reported_limit_down_count

    # 改动 A3：统一 prior 计算（原来有两段重复逻辑，其中第一段 trend 是被覆盖的死代码）。
    # 用 data_date(_today_str) 判断 cycle 最后一条是否为"当日"需剔除，避免周末/节假日误判。
    previous = list(cycle_history or [])
    current_date = current_cycle_date or _today_str
    previous = previous[:-1] if previous and previous[-1].get("date") == current_date else previous
    previous = previous[-5:]
    prior3 = previous[-3:]
    prior5_avg = sum(_num(item.get("advance_ratio"), 50) for item in previous) / len(previous) if previous else 50
    prior3_avg = sum(_num(item.get("advance_ratio"), 50) for item in prior3) / len(prior3) if prior3 else prior5_avg
    prior3_strong_avg = sum(_num(item.get("strong_ratio")) for item in prior3) / len(prior3) if prior3 else 0
    prior3_weak_avg = sum(_num(item.get("weak_ratio")) for item in prior3) / len(prior3) if prior3 else 0
    prior3_return_avg = sum(_num(item.get("avg_return")) for item in prior3) / len(prior3) if prior3 else 0
    hot_days = sum(_num(item.get("advance_ratio")) >= 60 for item in prior3)
    weak_days = sum(_num(item.get("advance_ratio"), 50) <= 35 for item in prior3)
    trend = round(advance_ratio - prior3_avg, 1)

    from core.risk_constants import BREADTH_DOWNGRADE_CRITICAL

    if limit_down_count >= BREADTH_DOWNGRADE_CRITICAL:
        regime = "CRITICAL"

    # score 统一计算（snapshot 非空时用宽度+历史加权，空时用 regime 兜底）
    if snapshot is None or snapshot.empty or "pct_chg" not in snapshot.columns:
        score = {"OFFENSIVE": 72, "DEFENSIVE": 42, "CRITICAL": 18}.get(regime, 45)
    else:
        current_quality = _clamp(advance_ratio + (strong_ratio - weak_ratio) * 2.5)
        regime_score = {"OFFENSIVE": 75, "DEFENSIVE": 50, "CRITICAL": 25}.get(regime, 50)
        score = round(_clamp(current_quality * 0.3 + prior3_avg * 0.35 + prior5_avg * 0.2 + regime_score * 0.15), 1)

    from core.risk_constants import (
        V_REPAIR_MIN_ADVANCE_RATIO,
        V_REPAIR_MIN_BREADTH_IMPROVEMENT,
        V_REPAIR_MIN_STRONG_RATIO,
    )

    if limit_down_count >= BREADTH_DOWNGRADE_CRITICAL:
        stage, label, max_position = "ICE", "冰点", 15
        allowed, forbidden = ["观察止跌、核心反转试错"], ["重仓抄底", "无确认追涨", "后排股"]
        reason = f"跌停{limit_down_count}家达到极端宽度阈值，风险状态不得提前修复"
    elif weak_days >= 2 and advance_ratio <= 30:
        stage, label, max_position = "ICE", "冰点", 15
        allowed, forbidden = ["观察止跌、核心反转试错"], ["重仓抄底", "无确认追涨", "后排股"]
        reason = "近3日持续极弱，等待止跌与首批主动走强"
    elif prior3_avg >= 58 and trend <= -15:
        stage, label, max_position = "DIVERGENCE", "高位分歧", 40
        allowed, forbidden = ["核心去弱留强", "分歧后回流确认"], ["追涨后排", "扩大仓位"]
        reason = "此前赚钱效应较强，但今日市场宽度明显回落"
    elif prior3_avg <= 45 and advance_ratio < 38 and trend < -5:
        stage, label, max_position = "RETREAT", "退潮", 10
        allowed, forbidden = ["处理风险、观察修复"], ["新增仓位", "高位追涨", "非主流交易"]
        reason = "多日市场宽度偏弱且继续恶化"
    elif hot_days >= 2 and prior3_strong_avg >= 7 and advance_ratio >= 72 and strong_ratio >= 10:
        stage, label, max_position = "CLIMAX", "高潮", 50
        allowed, forbidden = ["核心持有", "分歧低吸"], ["盲目追高", "后排跟风"]
        reason = "连续高热后进一步扩散，注意次日分歧风险"
    elif (
        regime == "CRITICAL"
        and len(previous) >= 3
        and advance_ratio >= V_REPAIR_MIN_ADVANCE_RATIO
        and strong_ratio >= V_REPAIR_MIN_STRONG_RATIO
        and trend >= V_REPAIR_MIN_BREADTH_IMPROVEMENT
        and prior3_avg < 52
    ):
        stage, label, max_position = "V_REPAIR", "强修复", 30
        allowed, forbidden = ["首批主线核心观察", "确认后回踩复核"], ["追涨加速票", "后排跟风", "直接扩大仓位"]
        reason = "前期偏弱后当日宽度快速修复，只观察首批主线核心并等待确认"
    elif prior3_avg >= 52 and prior3_strong_avg >= 5 and advance_ratio >= 55 and regime != "CRITICAL":
        stage, label, max_position = "ADVANCE", "主升", 70
        allowed, forbidden = ["主流核心", "确认后加仓"], ["无主线交易", "冲高追价"]
        reason = "赚钱效应连续维持，趋势与市场宽度共振"
    elif advance_ratio >= 45 or trend >= 10:
        stage, label, max_position = "REPAIR", "修复", 40 if regime != "CRITICAL" else 30
        allowed, forbidden = ["首批主动走强", "主流核心回踩"], ["非主流重仓", "高位跟风"]
        reason = "市场宽度正在改善，但持续性仍需后续交易日确认"
    else:
        stage, label, max_position = "RETREAT", "退潮", 10
        allowed, forbidden = ["处理风险、等待修复"], ["新增仓位", "高位追涨", "非主流交易"]
        reason = "赚钱效应不足，尚未形成有效修复"

    return {
        "market_sentiment_stage": stage,
        "market_sentiment_label": label,
        "market_sentiment_score": round(score, 1),
        "market_regime": regime,
        "portfolio_position_cap_pct": max_position,
        "market_allowed_actions": allowed,
        "market_forbidden_actions": forbidden,
        "market_sentiment_reason": reason,
        "market_cycle_metrics": {
            "prior3_advance_avg": round(prior3_avg, 1),
            "prior3_strong_avg": round(prior3_strong_avg, 1),
            "prior3_weak_avg": round(prior3_weak_avg, 1),
            "prior3_return_avg": round(prior3_return_avg, 2),
            "breadth_trend": trend,
            "hot_days_3d": hot_days,
            "weak_days_3d": weak_days,
            "history_days": len(previous),
        },
        "market_sentiment_model_version": "cycle-v3",
        "market_breadth": {
            "advance_ratio": round(advance_ratio, 1),
            "strong_ratio": round(strong_ratio, 1),
            "weak_ratio": round(weak_ratio, 1),
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "limit_up_ratio": round(limit_up_ratio, 2),
            "limit_down_ratio": round(limit_down_ratio, 2),
        },
    }


def _leadership_score(stock: Dict[str, Any]) -> float:
    role_score = {"LEADER": 92, "CORE": 78, "FOLLOWER": 48, "LAGGARD": 20}.get(stock.get("sector_role"), 40)
    alignment = _num(stock.get("sector_alignment_score"), 40)
    # 改动 D1：原 *10 缩放过激进——实测 sector_relative_pct 中位数 4.66%，*10 后
    # 50+46.6=96.6，>5% 即撞顶 100，导致 46% 股票饱和满分、丧失区分度（同 A1/A2 修复模式）。
    # 改为 *5：5% 跑赢=75 分（中位），10%=100 满分，分布更均匀。
    relative = _clamp(50 + _num(stock.get("sector_relative_pct")) * 5)
    active = _clamp(50 + _num(stock.get("涨幅%")) * 5)
    base_score = role_score * 0.4 + alignment * 0.3 + relative * 0.2 + active * 0.1

    status = stock.get("limit_up_status")
    if not status:
        stock["leadership_components"] = {
            "sector_role": round(role_score, 1),
            "sector_alignment": round(alignment, 1),
            "relative_strength": round(relative, 1),
            "activity": round(active, 1),
        }
        stock["leadership_reason"] = f"{stock.get('sector_role') or 'UNKNOWN'}，相对板块{_num(stock.get('sector_relative_pct')):+.1f}%"
        return round(base_score, 1)

    rank = max(1, int(_num(stock.get("limit_up_sector_rank"), 10)))
    first_board = _clamp(100 - (rank - 1) * 15, 35, 100)
    breaks = max(0, int(_num(stock.get("break_count"))))
    stability = _clamp((100 if status == "SEALED" else 45) - breaks * 12)
    streak = _clamp(35 + _num(stock.get("limit_up_streak")) * 20)
    seal_amount = _clamp(45 + _num(stock.get("seal_amount")) / 100000000 * 12)
    limit_score = first_board * 0.3 + stability * 0.25 + streak * 0.25 + seal_amount * 0.2
    score = round(base_score * 0.65 + limit_score * 0.35, 1)
    stock["leadership_components"] = {
        "sector_base": round(base_score, 1),
        "first_limit_rank": round(first_board, 1),
        "seal_stability": round(stability, 1),
        "limit_streak": round(streak, 1),
        "seal_amount": round(seal_amount, 1),
    }
    status_label = "封板" if status == "SEALED" else "炸板"
    stock["leadership_reason"] = f"板块第{rank}只触及涨停，{status_label}，炸板{breaks}次，{int(_num(stock.get('limit_up_streak')))}连板"
    return score


def _money_flow_score(stock: Dict[str, Any]) -> float:
    # 改动 A2：原公式用绝对金额(亿元)评分：amount*3 会让工行15亿流入碾压小盘0.5亿，
    # 系统性高估大盘股、低估小盘股。修复：用流通市值归一化为"相对流入强度"，
    # 再分段评分（饱和上限 20 分，避免极端值 dominating）。
    flow = stock.get("money_flow") or {}
    ratio = _num(flow.get("main_net_ratio"))
    amount = _num(flow.get("main_net_inflow_yi"))
    mkt_cap_yi = _num(stock.get("mkt_cap_yi"))
    # 归一化：流入金额占流通市值的百分比。max(...,50) 防止极小市值除零放大。
    relative_flow = amount / max(mkt_cap_yi, 50.0) * 100.0 if mkt_cap_yi > 0 else amount * 2.0
    sign = math.copysign(1.0, relative_flow) if relative_flow != 0 else 0.0
    # 分段：相对流入强度贡献（饱和在 ±20 分）+ 主力净占比（ratio 贡献减半避免饱和）
    flow_component = sign * min(20.0, abs(relative_flow) * 8.0)
    return round(_clamp(50.0 + flow_component + ratio * 0.5), 1)


def _risk_reward_score(stock: Dict[str, Any]) -> float:
    # 改动 A1：原公式用 risk_reward(盈亏比) 评分，但 risk_engine 把目标价定为
    # entry + 2*risk（固定 2:1），导致 rr 恒≈2，代入 rr*25+45=95，几乎所有票都撞
    # 上限，15% 权重变成常量，完全丧失区分度。
    # 修复：改为基于"止损距离百分比"评分——止损越紧（结构风险越小）分越高。
    # 数据来源 pa_risk_pct（scanner 已计算 = (entry-stop)/entry*100）。
    # 映射：5% 止损=100 分（满分），每多 1% 扣 4 分，16% 止损=12 分。
    risk_pct = _num(stock.get("pa_risk_pct"))
    trap = _num(stock.get("pa_trap_risk"))
    failure = _num(stock.get("pa_failure_risk"))
    if risk_pct <= 0:
        # 无止损数据时回退到中性基线（避免极端打分）
        base = 55.0
    else:
        base = _clamp(100.0 - max(0.0, risk_pct - 5.0) * 4.0)
    return round(_clamp(base - trap * 0.2 - failure * 0.15), 1)


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


def _risk_normalize_position(position: Dict[str, Any], stock: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(position)
    risk_pct = _num(stock.get("pa_risk_pct"))
    normalized["risk_budget_pct"] = MAX_PORTFOLIO_RISK_PER_TRADE_PCT
    normalized["stop_risk_pct"] = round(risk_pct, 2) if risk_pct > 0 else None
    normalized["risk_capped"] = False
    if risk_pct <= 0 or normalized.get("initial_position_pct", 0) <= 0:
        return normalized
    max_by_risk = round(MAX_PORTFOLIO_RISK_PER_TRADE_PCT / risk_pct * 100, 1)
    old_initial = float(normalized["initial_position_pct"])
    old_max = float(normalized["max_position_pct"])
    normalized["initial_position_pct"] = min(old_initial, max_by_risk)
    normalized["max_position_pct"] = min(old_max, max_by_risk)
    normalized["risk_capped"] = normalized["max_position_pct"] < old_max
    normalized["max_position_by_risk_pct"] = max_by_risk
    normalized["estimated_initial_risk_pct"] = round(
        normalized["initial_position_pct"] * risk_pct / 100, 2
    )
    return normalized


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
    cycle_history: List[Dict[str, Any]] | None = None,
    data_date: str | None = None,
) -> Dict[str, Any]:
    """Enrich scan results with market permission, opportunity score, and execution state.

    改动 A3：新增 data_date 参数，透传给 build_market_decision_context，修复周末/节假日
    扫描时用墙钟 now() 误判市场宽度的问题。
    """
    context = build_market_decision_context(snapshot, market_regime, cycle_history, data_date=data_date)
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
        # 修复#1: ICE(冰点)阶段也应阻断建仓（原仅阻断RETREAT）。
        # 同时允许防御性板块(黄金/公用事业等)在RETREAT时轮动（不一刀切阻断）。
        _stage = context["market_sentiment_stage"]
        _is_defensive_sector = mainline in ("防御", "DEFENSIVE") or stock.get("行业", "") in (
            "黄金", "贵金属", "电力", "水务", "燃气", "高速公路", "港口", "银行", "煤炭"
        )
        # ICE 和 RETREAT 都阻断，但 RETREAT 时防御性板块可观察（不禁止）
        market_blocked = _stage in ("RETREAT", "ICE")
        if market_blocked and _is_defensive_sector and _stage == "RETREAT":
            # 防御性板块在退潮期可观察（降级但不一刀切禁止）
            market_blocked = False
            stock["defensive_rotation"] = True
        sector_blocked = mainline == "FADING"
        strategy_type = str(stock.get("strategy_type") or PRIMARY_TV_STRATEGY)
        score_gate_applicable = strategy_type in SOP_A_GRADE_STRATEGIES
        score_blocked = (
            score_gate_applicable
            and opportunity < 60
            and not stock.get("a_eod_controlled_trial")
        )
        blocked = market_blocked or sector_blocked or score_blocked or stock.get("trade_bucket") == "BLOCK"
        position_blocked = blocked or not score_gate_applicable
        if stock.get("a_eod_controlled_trial") and not position_blocked:
            controlled_cap = min(float(market_cap), A_EOD_PORTFOLIO_CAP_PCT)
            controlled_position = min(A_EOD_POSITION_PCT, controlled_cap)
            position = _risk_normalize_position({
                "label": "A-EOD受控小仓" if controlled_position > 0 else "观望",
                "initial_position_pct": controlled_position,
                "max_position_pct": controlled_position,
                "portfolio_position_cap_pct": controlled_cap,
                "a_eod_max_positions": A_EOD_MAX_CONCURRENT_POSITIONS,
            }, stock)
        else:
            position = _risk_normalize_position(
                _position_plan(opportunity, market_cap, mainline, position_blocked), stock,
            )
        if stock.get("a_minus_trial"):
            position["initial_position_pct"] = min(
                float(position.get("initial_position_pct") or 0), A_MINUS_TRIAL_POSITION_PCT,
            )
            position["max_position_pct"] = min(
                float(position.get("max_position_pct") or 0), A_MINUS_TRIAL_POSITION_PCT,
            )
            position["label"] = "A-受控试仓" if position["initial_position_pct"] > 0 else "观望"
            position["a_minus_portfolio_cap_pct"] = A_MINUS_TRIAL_PORTFOLIO_CAP_PCT
            risk_pct = _num(stock.get("pa_risk_pct"))
            if risk_pct > 0 and position["initial_position_pct"] > 0:
                position["estimated_initial_risk_pct"] = round(
                    position["initial_position_pct"] * risk_pct / 100, 2,
                )
            stock["a_minus_portfolio_cap_pct"] = A_MINUS_TRIAL_PORTFOLIO_CAP_PCT
        if stock.get("a_eod_controlled_trial"):
            position["a_eod_portfolio_cap_pct"] = A_EOD_PORTFOLIO_CAP_PCT
            position["a_eod_max_positions"] = A_EOD_MAX_CONCURRENT_POSITIONS
            stock["a_eod_portfolio_cap_pct"] = A_EOD_PORTFOLIO_CAP_PCT
            stock["a_eod_max_positions"] = A_EOD_MAX_CONCURRENT_POSITIONS
        tv_risk_unit = _num(stock.get("tv_execution_risk_unit"), 1.0)
        if strategy_type == "tv_dual" and 0 < tv_risk_unit < 1:
            position["initial_position_pct"] = round(
                float(position.get("initial_position_pct") or 0) * tv_risk_unit, 1,
            )
            position["max_position_pct"] = round(
                float(position.get("max_position_pct") or 0) * tv_risk_unit, 1,
            )
            risk_pct = _num(stock.get("pa_risk_pct"))
            if risk_pct > 0 and position["initial_position_pct"] > 0:
                position["estimated_initial_risk_pct"] = round(
                    position["initial_position_pct"] * risk_pct / 100, 2,
                )
        position["tv_execution_risk_unit"] = tv_risk_unit
        state = _trade_state(stock, position)

        stock.update(context)
        stock["sector_mainline"] = mainline
        stock["leadership_score"] = leader_score
        stock["trade_opportunity_score"] = opportunity
        stock["opportunity_gate_applicable"] = score_gate_applicable
        stock["trade_opportunity_label"] = position["label"]
        stock["position_plan"] = position
        stock["trade_state"] = state
        instruction = _execution_instruction(stock, state, position)
        if stock.get("a_minus_trial") and state != "BLOCKED":
            instruction = (
                f"A-受控试仓（单票≤{A_MINUS_TRIAL_POSITION_PCT:g}%，"
                f"组合≤{A_MINUS_TRIAL_PORTFOLIO_CAP_PCT:g}%）；{instruction}"
            )
        elif stock.get("a_eod_controlled_trial") and state != "BLOCKED":
            cautions = "；".join(str(item) for item in (stock.get("a_eod_trade_cautions") or [])[:2])
            caution_text = f"；软约束：{cautions}" if cautions else ""
            instruction = (
                f"A-EOD受控小仓（单票≤{A_EOD_POSITION_PCT:g}%，"
                f"组合≤{A_EOD_PORTFOLIO_CAP_PCT:g}%，"
                f"最多{A_EOD_MAX_CONCURRENT_POSITIONS}只）{caution_text}；{instruction}"
            )
        stock["execution_instruction"] = instruction
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
