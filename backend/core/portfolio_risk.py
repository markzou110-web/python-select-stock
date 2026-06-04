from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


DEFAULT_RISK_BUDGET = {
    "max_open_positions": 8,
    "max_real_positions": 5,
    "max_sector_positions": 2,
    "max_strategy_positions": 4,
    "max_single_risk_pct": 2.0,
    "max_total_plan_risk_pct": 6.0,
}


def evaluate_portfolio_risk_budget(
    engine: Optional[Engine],
    new_trade: Dict[str, Any],
    force: bool = False,
    budget: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Evaluate portfolio-level exposure before adding a new paper/real trade."""
    if engine is None:
        return {"status": "error", "warnings": ["数据库不可用，无法评估组合风险"], "budget": budget or DEFAULT_RISK_BUDGET}

    limits = {**DEFAULT_RISK_BUDGET, **(budget or {})}
    warnings = []
    summary: Dict[str, Any] = {}

    try:
        open_df = pd.read_sql(text("SELECT * FROM paper_trading WHERE status = :status"), engine, params={"status": "OPEN"})
        sector_df = pd.read_sql(text("SELECT code, industry FROM stock_basic WHERE industry IS NOT NULL"), engine)
        sector_map = dict(zip(sector_df["code"], sector_df["industry"])) if not sector_df.empty else {}
    except Exception as exc:
        return {"status": "error", "warnings": [f"组合风险查询失败: {str(exc)[:80]}"], "budget": limits}

    new_code = str(new_trade.get("code") or "")
    new_strategy = new_trade.get("strategy_type") or "unknown"
    new_mode = new_trade.get("trade_mode") or "SIMULATED"
    new_risk = float(new_trade.get("pa_risk_pct") or 0)
    new_sector = sector_map.get(new_code, "未知")

    open_count = len(open_df)
    real_count = int((open_df.get("trade_mode", pd.Series(dtype=str)) == "REAL").sum()) if not open_df.empty else 0
    total_plan_risk = float(open_df.get("pa_risk_pct", pd.Series(dtype=float)).fillna(0).sum()) if not open_df.empty else 0.0

    strategy_count = 0
    sector_count = 0
    if not open_df.empty:
        strategy_count = int((open_df.get("strategy_type", pd.Series(dtype=str)).fillna("unknown") == new_strategy).sum())
        for code in open_df["code"]:
            if sector_map.get(str(code), "未知") == new_sector and new_sector != "未知":
                sector_count += 1

    if open_count + 1 > int(limits["max_open_positions"]):
        warnings.append(f"总持仓将达到 {open_count + 1} 个，超过上限 {int(limits['max_open_positions'])}")
    if new_mode == "REAL" and real_count + 1 > int(limits["max_real_positions"]):
        warnings.append(f"实盘持仓将达到 {real_count + 1} 个，超过上限 {int(limits['max_real_positions'])}")
    if new_sector != "未知" and sector_count + 1 > int(limits["max_sector_positions"]):
        warnings.append(f"行业 {new_sector} 将达到 {sector_count + 1} 个持仓，超过上限 {int(limits['max_sector_positions'])}")
    if strategy_count + 1 > int(limits["max_strategy_positions"]):
        warnings.append(f"策略 {new_strategy} 将达到 {strategy_count + 1} 个持仓，超过上限 {int(limits['max_strategy_positions'])}")
    if new_risk > float(limits["max_single_risk_pct"]):
        warnings.append(f"单笔计划风险 {new_risk:.1f}% 超过上限 {float(limits['max_single_risk_pct']):.1f}%")
    if total_plan_risk + new_risk > float(limits["max_total_plan_risk_pct"]):
        warnings.append(f"组合计划风险将达到 {total_plan_risk + new_risk:.1f}%，超过上限 {float(limits['max_total_plan_risk_pct']):.1f}%")

    summary.update({
        "open_positions": open_count,
        "real_positions": real_count,
        "new_sector": new_sector,
        "sector_positions": sector_count,
        "strategy_positions": strategy_count,
        "current_plan_risk_pct": round(total_plan_risk, 2),
        "projected_plan_risk_pct": round(total_plan_risk + new_risk, 2),
    })

    status = "ok"
    if warnings:
        status = "override" if force else "warning"

    return {"status": status, "warnings": warnings, "summary": summary, "budget": limits}
