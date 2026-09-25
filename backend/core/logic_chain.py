"""把扫描结果中已有的市场/板块/个股/资金字段串成"势→术"因果链。

资金逻辑是上层决策（为什么选它），技术信号是执行确认（怎么买卖）。
这里只做字段重组与展示，不产生新的判定，不改变任何风控语义。
"""
from typing import Any, Dict

SECTOR_PHASE_LABELS = {
    "SECTOR_FADE": "退潮",
    "SECTOR_CLIMAX": "高潮",
    "SECTOR_CONFIRM": "主升",
    "SECTOR_EARLY": "启动",
    "SECTOR_NEUTRAL": "中性",
}

ROLE_LABELS = {
    "LEADER": "板块龙头",
    "CORE": "板块核心",
    "FOLLOWER": "板块跟随",
    "LAGGARD": "板块滞后",
}


def build_logic_chain_line(stock: Dict[str, Any]) -> str:
    """市场情绪 → 板块结构 → 个股角色；字段不足两项时返回空串（不渲染）。"""
    parts = []
    market = str(stock.get("market_sentiment_label") or "").strip()
    if not market:
        market = str(stock.get("market_regime") or "").strip()
    if market:
        parts.append(f"市场{market}")
    industry = str(stock.get("行业") or stock.get("industry") or "").strip()
    phase = SECTOR_PHASE_LABELS.get(str(stock.get("sector_phase") or ""))
    mainline = "·主线" if str(stock.get("sector_mainline") or "") == "MAIN" else ""
    if industry or phase:
        sector_text = f"{industry}板块{phase}" if phase else industry
        parts.append(f"{sector_text}{mainline}")
    role = ROLE_LABELS.get(str(stock.get("sector_role") or ""))
    if role:
        parts.append(role)
    return f"逻辑：{' → '.join(parts)}" if len(parts) >= 2 else ""


def build_capital_evidence_line(stock: Dict[str, Any]) -> str:
    """资金证据：主力净流入 / RPS120 / 板块涨停家数 / 行业景气标签。"""
    evidences = []
    flow = stock.get("money_flow") if isinstance(stock.get("money_flow"), dict) else {}
    inflow = flow.get("main_net_inflow_yi")
    if isinstance(inflow, (int, float)):
        if inflow >= 0:
            evidences.append(f"主力净流入{inflow:.1f}亿")
        else:
            evidences.append(f"主力净流出{abs(inflow):.1f}亿")
    rps = stock.get("rps_120")
    if isinstance(rps, (int, float)) and rps > 0:
        evidences.append(f"RPS120={rps:.0f}")
    limit_count = stock.get("sector_limit_count")
    if isinstance(limit_count, (int, float)) and limit_count > 0:
        evidences.append(f"板块涨停{int(limit_count)}家")
    prosperity = stock.get("industry_prosperity") if isinstance(stock.get("industry_prosperity"), dict) else {}
    if prosperity.get("label"):
        evidences.append(f"行业{prosperity['label']}")
    return f"资金：{'｜'.join(evidences)}" if evidences else ""
