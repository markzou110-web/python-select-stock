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
    # 改动 #12：日内亏损熔断。当日已平仓实现亏损占初始总资金比例超过此值时，
    # 暂停当日新开仓（halt），避免连续止损放大系统性回撤。
    "daily_loss_limit_pct": 5.0,
}


def evaluate_daily_loss_circuit_breaker(
    engine: Optional[Engine],
    budget: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """改动 #12：日内亏损熔断。

    统计当日（CLOSED 且 close_date=今天）已实现盈亏，若亏损占初始总资金比例
    超过 daily_loss_limit_pct，返回 status="halt"（阻止新开仓）。否则 status="ok"。

    盈亏按 (close_price - entry_price) / entry_price * shares 近似，因 paper_trading
    无 capital 字段，这里用"亏损笔的 entry_price*shares 之和"作分母近似资金占比，
    已足以触发熔断保护（保守口径）。
    """
    if engine is None:
        return {"status": "error", "halted": False, "daily_loss_pct": 0.0, "message": "数据库不可用"}

    limits = {**DEFAULT_RISK_BUDGET, **(budget or {})}
    loss_limit = float(limits.get("daily_loss_limit_pct", 5.0))
    today_str = pd.Timestamp.now().strftime("%Y-%m-%d")

    try:
        df = pd.read_sql(
            text("""
                SELECT entry_price, close_price, shares
                FROM paper_trading
                WHERE status = 'CLOSED' AND close_date = :d
                  AND close_price > 0 AND entry_price > 0
            """),
            engine,
            params={"d": today_str},
        )
    except Exception as exc:
        return {"status": "error", "halted": False, "daily_loss_pct": 0.0, "daily_loss_limit_pct": loss_limit, "message": f"日内亏损查询失败: {str(exc)[:80]}"}

    if df.empty:
        return {"status": "ok", "halted": False, "daily_loss_pct": 0.0, "daily_loss_limit_pct": loss_limit, "message": "今日无平仓"}

    df["shares"] = pd.to_numeric(df.get("shares"), errors="coerce").fillna(0).astype(float)
    # 单笔实现盈亏金额（近似）= (close - entry) * shares
    df["pnl"] = (df["close_price"].astype(float) - df["entry_price"].astype(float)) * df["shares"]
    realized_pnl = float(df["pnl"].sum())
    # 资金分母：当日所有平仓笔的初始投入 entry*shares 之和（近似总资金口径）
    capital_base = float((df["entry_price"].astype(float) * df["shares"]).sum())
    daily_loss_pct = (realized_pnl / capital_base * 100.0) if capital_base > 0 else 0.0

    if daily_loss_pct < -abs(loss_limit):
        return {
            "status": "halt",
            "halted": True,
            "daily_loss_pct": round(daily_loss_pct, 2),
            "daily_loss_limit_pct": loss_limit,
            "message": f"日内已实现亏损 {daily_loss_pct:.2f}% 超过熔断线 -{loss_limit:.1f}%，暂停当日新开仓",
        }
    return {
        "status": "ok",
        "halted": False,
        "daily_loss_pct": round(daily_loss_pct, 2),
        "daily_loss_limit_pct": loss_limit,
        "message": "日内亏损在熔断线内",
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


def build_portfolio_exposure(engine: Optional[Engine], budget: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Summarize open-position concentration against the same risk budget used at entry."""
    if engine is None:
        return {"status": "error", "items": [], "warnings": ["数据库不可用"], "budget": budget or DEFAULT_RISK_BUDGET}

    limits = {**DEFAULT_RISK_BUDGET, **(budget or {})}
    try:
        open_df = pd.read_sql(text("SELECT * FROM paper_trading WHERE status = :status"), engine, params={"status": "OPEN"})
        sector_df = pd.read_sql(text("SELECT code, industry FROM stock_basic WHERE industry IS NOT NULL"), engine)
    except Exception as exc:
        return {"status": "error", "items": [], "warnings": [f"组合暴露查询失败: {str(exc)[:80]}"], "budget": limits}

    if open_df.empty:
        return {
            "status": "ok",
            "items": [],
            "warnings": [],
            "summary": {"open_positions": 0, "real_positions": 0, "total_plan_risk_pct": 0},
            "budget": limits,
        }

    sector_map = dict(zip(sector_df["code"].astype(str), sector_df["industry"])) if not sector_df.empty else {}
    open_df["industry"] = open_df["code"].astype(str).map(sector_map).fillna("未知")
    open_df["strategy_group"] = open_df.get("strategy_type", pd.Series(dtype=str)).fillna("unknown")
    open_df["risk_pct_num"] = pd.to_numeric(open_df.get("pa_risk_pct", pd.Series(dtype=float)), errors="coerce").fillna(0)

    def group_rows(column: str, kind: str, limit_key: str) -> list[Dict[str, Any]]:
        rows = []
        for value, group in open_df.groupby(column, dropna=False):
            count = int(len(group))
            risk_pct = round(float(group["risk_pct_num"].sum()), 2)
            limit = int(limits[limit_key])
            rows.append({
                "kind": kind,
                "name": str(value or "未知"),
                "count": count,
                "risk_pct": risk_pct,
                "limit": limit,
                "status": "warning" if count > limit else "ok",
            })
        rows.sort(key=lambda item: (item["status"] == "warning", item["count"], item["risk_pct"]), reverse=True)
        return rows

    real_positions = int((open_df.get("trade_mode", pd.Series(dtype=str)) == "REAL").sum())
    total_plan_risk = round(float(open_df["risk_pct_num"].sum()), 2)
    warnings = []
    if len(open_df) > int(limits["max_open_positions"]):
        warnings.append(f"总持仓 {len(open_df)} 个超过上限 {int(limits['max_open_positions'])}")
    if real_positions > int(limits["max_real_positions"]):
        warnings.append(f"实盘持仓 {real_positions} 个超过上限 {int(limits['max_real_positions'])}")
    if total_plan_risk > float(limits["max_total_plan_risk_pct"]):
        warnings.append(f"组合计划风险 {total_plan_risk:.1f}% 超过上限 {float(limits['max_total_plan_risk_pct']):.1f}%")

    items = group_rows("industry", "sector", "max_sector_positions") + group_rows("strategy_group", "strategy", "max_strategy_positions")
    warnings.extend(
        f"{'行业' if item['kind'] == 'sector' else '策略'} {item['name']} 暴露 {item['count']} 个超过上限 {item['limit']}"
        for item in items
        if item["status"] == "warning"
    )

    return {
        "status": "warning" if warnings else "ok",
        "items": items,
        "warnings": warnings,
        "summary": {
            "open_positions": int(len(open_df)),
            "real_positions": real_positions,
            "total_plan_risk_pct": total_plan_risk,
        },
        "budget": limits,
    }
