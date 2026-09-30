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
    # 改动 B5：组合浮亏熔断。所有 OPEN 持仓的浮亏（未实现）占初始总资金比例
    # 超过此值时，暂停加仓。与 daily_loss_limit_pct（已实现）互补——系统性下跌日
    # 持仓全部浮亏但未触发止损时，仍能阻止"越跌越加"。
    "floating_loss_limit_pct": 5.0,
    # Elder《以交易为生》6% 法则：当月已实现净亏损 + 当前持仓资金风险，
    # 占虚拟总资金比例达到此值时，本月剩余时间硬熔断新开仓。
    "monthly_risk_limit_pct": 6.0,
    # 修复#4: 总仓位上限。所有OPEN持仓的capital_used之和占虚拟总资金的比例上限。
    # 防止理论上满仓单票或过度集中。
    "max_total_capital_pct": 80.0,
    "virtual_total_capital": 1000000.0,  # 虚拟总资金100万（用于仓位占比计算）
}


def calculate_capital_risk(capital_used: float, risk_pct: float) -> float:
    """Money at risk if the planned stop fills, excluding overnight gap risk."""
    return round(max(0.0, float(capital_used or 0)) * max(0.0, float(risk_pct or 0)) / 100, 2)


def evaluate_floating_loss_circuit_breaker(
    engine: Optional[Engine],
    snapshot: Optional[pd.DataFrame] = None,
    budget: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """改动 B5：组合浮亏熔断。

    统计所有 OPEN 持仓的浮亏（用 snapshot 最新价计算未实现 PnL），若组合浮亏占
    初始总资金比例超过 floating_loss_limit_pct，返回 status="halt"（阻止加仓）。

    与 evaluate_daily_loss_circuit_breaker（已实现亏损）互补：
    - 系统性下跌日持仓全部浮亏 -6% 但未触发止损 → 无已实现亏损 → 旧熔断不触发
    - 本函数检测到浮亏超限 → halt → 阻止 ADD_REVIEW 和新开仓

    snapshot 需含 code/price 列；为空时返回 ok（不阻断）。
    """
    if engine is None:
        return {"status": "error", "halted": False, "floating_loss_pct": 0.0, "message": "数据库不可用"}

    limits = {**DEFAULT_RISK_BUDGET, **(budget or {})}
    loss_limit = float(limits.get("floating_loss_limit_pct", 5.0))

    if snapshot is None or snapshot.empty or "code" not in snapshot.columns or "price" not in snapshot.columns:
        return {"status": "ok", "halted": False, "floating_loss_pct": 0.0, "floating_loss_limit_pct": loss_limit, "message": "无快照数据，跳过浮亏检查"}

    try:
        df = pd.read_sql(
            text("""
                SELECT code, entry_price, shares
                FROM paper_trading
                WHERE status = 'OPEN' AND entry_price > 0 AND COALESCE(shares, 0) > 0
            """),
            engine,
        )
    except Exception as exc:
        return {"status": "error", "halted": False, "floating_loss_pct": 0.0, "floating_loss_limit_pct": loss_limit, "message": f"持仓查询失败: {str(exc)[:80]}"}

    if df.empty:
        return {"status": "ok", "halted": False, "floating_loss_pct": 0.0, "floating_loss_limit_pct": loss_limit, "message": "无 OPEN 持仓"}

    price_map = {}
    for _, row in snapshot.iterrows():
        try:
            price_map[str(row["code"])] = float(row["price"])
        except (TypeError, ValueError):
            continue

    df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0).astype(float)
    df["curr_price"] = df["code"].astype(str).map(price_map)
    # 无法匹配价格的持仓不计入（保守跳过）
    df_valid = df[df["curr_price"].notna()].copy()
    if df_valid.empty:
        return {"status": "ok", "halted": False, "floating_loss_pct": 0.0, "floating_loss_limit_pct": loss_limit, "message": "持仓无有效最新价"}

    # 浮亏金额 = (curr - entry) * shares；分母统一为虚拟总资金（与日内/月度
    # 熔断同口径、与 docstring 语义一致）。原 OPEN 持仓成本分母使浮亏线随
    # 仓位轻重浮动，与其他两条熔断线不可比。持仓总成本保留在返回值供核对。
    df_valid["unrealized_pnl"] = (df_valid["curr_price"].astype(float) - df_valid["entry_price"].astype(float)) * df_valid["shares"]
    df_valid["cost"] = df_valid["entry_price"].astype(float) * df_valid["shares"]
    total_unrealized = float(df_valid["unrealized_pnl"].sum())
    total_cost = float(df_valid["cost"].sum())
    virtual_cap = float(limits.get("virtual_total_capital", 1000000.0))

    floating_loss_pct = (total_unrealized / virtual_cap * 100.0) if virtual_cap > 0 else 0.0

    if floating_loss_pct < -abs(loss_limit):
        return {
            "status": "halt",
            "halted": True,
            "floating_loss_pct": round(floating_loss_pct, 2),
            "floating_loss_limit_pct": loss_limit,
            "open_cost": round(total_cost, 2),
            "denominator": virtual_cap,
            "message": f"组合浮亏 {floating_loss_pct:.2f}% 超过熔断线 -{loss_limit:.1f}%，暂停加仓",
        }
    return {
        "status": "ok",
        "halted": False,
        "floating_loss_pct": round(floating_loss_pct, 2),
        "floating_loss_limit_pct": loss_limit,
        "open_cost": round(total_cost, 2),
        "message": "组合浮亏在熔断线内",
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
    # 分母统一为虚拟总资金（与月度熔断/浮亏熔断同口径）：原"全表持仓初始投入"
    # 分母随历史 CLOSED 行无界增长，-5% 熔断线越来越钝（历史行越多越难触发）。
    virtual_cap = float(limits.get("virtual_total_capital", 1000000.0))
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

    # 批2-4：分母统一为虚拟总资金（原 R3-3 的"全表初始投入"分母随历史 CLOSED 行
    # 无界增长，熔断线越来越钝；三套分母互斥导致 -5%/-5%/-6% 三条熔断线不可比）。
    daily_loss_pct = (realized_pnl / virtual_cap * 100.0) if virtual_cap > 0 else 0.0

    if daily_loss_pct < -abs(loss_limit):
        return {
            "status": "halt",
            "halted": True,
            "daily_loss_pct": round(daily_loss_pct, 2),
            "daily_loss_limit_pct": loss_limit,
            "denominator": virtual_cap,
            "message": f"日内已实现亏损 {daily_loss_pct:.2f}% 超过熔断线 -{loss_limit:.1f}%，暂停当日新开仓",
        }
    return {
        "status": "ok",
        "halted": False,
        "daily_loss_pct": round(daily_loss_pct, 2),
        "daily_loss_limit_pct": loss_limit,
        "message": "日内亏损在熔断线内",
    }


def evaluate_monthly_risk_budget(
    engine: Optional[Engine],
    now: Optional[pd.Timestamp] = None,
    budget: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Elder《以交易为生》6% 法则：月度风险熔断。

    月度风险用量 = max(0, -当月已实现净盈亏) + 当前 OPEN 持仓资金风险，
    除以虚拟总资金（virtual_total_capital，与 evaluate_portfolio_risk_budget
    的资本风险口径一致）。达到 monthly_risk_limit_pct 时返回 status="halt"，
    本月剩余时间阻止新开仓（与日内熔断同为硬限制，不可被 force 绕过）。

    与 evaluate_daily_loss_circuit_breaker（"今天"）互补，本函数管"这个月"，
    防止月度累积亏损失控后继续加大头寸"救交易"。

    已实现盈亏按 (close_price - entry_price) * shares 近似（paper_trading 无资金
    字段的既有口径）；当月盈利与亏损相互抵扣（净额口径）。
    """
    from core.risk_constants import MONTHLY_RISK_BREAKER_ENABLED, MONTHLY_RISK_POLICY_VERSION

    if engine is None:
        return {"status": "error", "halted": False, "monthly_risk_used_pct": 0.0, "message": "数据库不可用"}

    limits = {**DEFAULT_RISK_BUDGET, **(budget or {})}
    loss_limit = float(limits.get("monthly_risk_limit_pct", 6.0))
    virtual_cap = float(limits.get("virtual_total_capital", 1000000.0))
    current = pd.Timestamp(now) if now is not None else pd.Timestamp.now()
    month_start = current.strftime("%Y-%m-01")
    base = {
        "status": "ok",
        "halted": False,
        "monthly_risk_used_pct": 0.0,
        "monthly_risk_limit_pct": loss_limit,
        "policy_version": MONTHLY_RISK_POLICY_VERSION,
    }
    if not MONTHLY_RISK_BREAKER_ENABLED:
        return {**base, "message": "月度风险熔断已禁用"}

    try:
        closed_df = pd.read_sql(
            text("""
                SELECT entry_price, close_price, shares
                FROM paper_trading
                WHERE status = 'CLOSED' AND close_date >= :month_start
                  AND close_price > 0 AND entry_price > 0
            """),
            engine,
            params={"month_start": month_start},
        )
        open_df = pd.read_sql(
            text("""
                SELECT capital_used, pa_risk_pct
                FROM paper_trading
                WHERE status = 'OPEN' AND COALESCE(pa_risk_pct, 0) > 0
            """),
            engine,
        )
    except Exception as exc:
        return {**base, "status": "error", "message": f"月度风险查询失败: {str(exc)[:80]}"}

    mtd_realized_pnl = 0.0
    if not closed_df.empty:
        shares = pd.to_numeric(closed_df.get("shares"), errors="coerce").fillna(0).astype(float)
        mtd_realized_pnl = float(
            ((closed_df["close_price"].astype(float) - closed_df["entry_price"].astype(float)) * shares).sum()
        )

    open_capital_risk = 0.0
    if not open_df.empty:
        capital = pd.to_numeric(open_df.get("capital_used"), errors="coerce").fillna(0).astype(float)
        risk_pct = pd.to_numeric(open_df.get("pa_risk_pct"), errors="coerce").fillna(0).astype(float)
        open_capital_risk = float((capital * risk_pct / 100).sum())

    monthly_loss = max(0.0, -mtd_realized_pnl)
    risk_used_pct = ((monthly_loss + open_capital_risk) / virtual_cap * 100.0) if virtual_cap > 0 else 0.0

    result = {
        **base,
        "monthly_risk_used_pct": round(risk_used_pct, 3),
        "mtd_realized_pnl": round(mtd_realized_pnl, 2),
        "monthly_realized_loss": round(monthly_loss, 2),
        "open_capital_risk": round(open_capital_risk, 2),
        "virtual_total_capital": virtual_cap,
    }
    if risk_used_pct >= loss_limit:
        return {
            **result,
            "status": "halt",
            "halted": True,
            "message": (
                f"月度风险用量 {risk_used_pct:.2f}%（当月已实现亏损 {monthly_loss:.0f} 元"
                f" + 持仓风险 {open_capital_risk:.0f} 元）达到熔断线 {loss_limit:.1f}%，"
                f"本月剩余时间暂停新开仓"
            ),
        }
    return {**result, "message": "月度风险用量在熔断线内"}


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
    new_capital_used = float(new_trade.get("capital_used") or 0)
    virtual_cap = float(limits.get("virtual_total_capital", 1000000))
    new_sector = sector_map.get(new_code, "未知")

    open_count = len(open_df)
    real_count = int((open_df.get("trade_mode", pd.Series(dtype=str)) == "REAL").sum()) if not open_df.empty else 0
    existing_capital = pd.to_numeric(open_df.get("capital_used", pd.Series(dtype=float)), errors="coerce").fillna(0)
    existing_risk = pd.to_numeric(open_df.get("pa_risk_pct", pd.Series(dtype=float)), errors="coerce").fillna(0)
    current_capital_risk = float((existing_capital * existing_risk / 100).sum())
    current_plan_risk = current_capital_risk / virtual_cap * 100 if virtual_cap > 0 else 0.0

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
    # Exposure and risk are both measured against the same account-capital denominator.
    try:
        existing_capital = float(pd.to_numeric(
            open_df.get("capital_used", pd.Series(dtype=float)), errors="coerce",
        ).fillna(0).sum())
    except Exception:
        existing_capital = 0.0
    position_pct = float(new_trade.get("position_pct") or 0)
    new_capital = new_capital_used or (virtual_cap * position_pct / 100 if virtual_cap > 0 else 0)
    new_capital_risk = calculate_capital_risk(new_capital, new_risk)
    total_capital = existing_capital + new_capital
    capital_pct = total_capital / virtual_cap * 100 if virtual_cap > 0 else 0
    max_cap_pct = float(limits.get("max_total_capital_pct", 80.0))
    if capital_pct > max_cap_pct:
        warnings.append(f"总仓位占比将达到 {capital_pct:.1f}%，超过上限 {max_cap_pct:.0f}%（虚拟资金¥{virtual_cap/10000:.0f}万）")
    projected_capital_risk_pct = (current_capital_risk + new_capital_risk) / virtual_cap * 100 if virtual_cap > 0 else 0
    new_single_risk_pct = new_capital_risk / virtual_cap * 100 if virtual_cap > 0 else 0
    if new_single_risk_pct > float(limits["max_single_risk_pct"]):
        warnings.append(
            f"单笔账户风险 {new_single_risk_pct:.2f}% 超过上限 "
            f"{float(limits['max_single_risk_pct']):.1f}%"
        )
    if projected_capital_risk_pct > float(limits["max_total_plan_risk_pct"]):
        warnings.append(
            f"组合资本风险将达到 {projected_capital_risk_pct:.2f}%，超过上限 "
            f"{float(limits['max_total_plan_risk_pct']):.1f}%"
        )

    summary.update({
        "open_positions": open_count,
        "real_positions": real_count,
        "new_sector": new_sector,
        "sector_positions": sector_count,
        "strategy_positions": strategy_count,
        "current_plan_risk_pct": round(current_plan_risk, 3),
        "projected_plan_risk_pct": round(projected_capital_risk_pct, 3),
        "current_capital_risk_amount": round(current_capital_risk, 2),
        "new_capital_risk_amount": new_capital_risk,
        "projected_capital_risk_pct": round(projected_capital_risk_pct, 3),
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
    capital = pd.to_numeric(open_df.get("capital_used", pd.Series(dtype=float)), errors="coerce").fillna(0)
    stop_distance_pct = pd.to_numeric(open_df.get("pa_risk_pct", pd.Series(dtype=float)), errors="coerce").fillna(0)
    virtual_cap = float(limits.get("virtual_total_capital", 1000000))
    open_df["risk_pct_num"] = (
        capital * stop_distance_pct / 100 / virtual_cap * 100 if virtual_cap > 0 else 0
    )

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
        warnings.append(f"组合账户风险 {total_plan_risk:.2f}% 超过上限 {float(limits['max_total_plan_risk_pct']):.1f}%")

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


def build_portfolio_stress(engine: Optional[Engine], lookback: int = 60) -> Dict[str, Any]:
    """Estimate hidden correlation, liquidity capacity, and two-limit-down stress."""
    if engine is None:
        return {"status": "error", "positions": [], "correlation_clusters": []}
    positions = pd.read_sql(text("SELECT code, name, capital_used FROM paper_trading WHERE status='OPEN'"), engine)
    if positions.empty:
        return {"status": "ok", "positions": [], "correlation_clusters": [], "two_limit_down_loss": 0}
    codes = positions["code"].astype(str).tolist()
    bars = pd.read_sql(text("""
        SELECT code, date, close, vol FROM daily_k
        WHERE code = ANY(:codes) AND date >= CURRENT_DATE - (:days || ' days')::interval
        ORDER BY code, date
    """), engine, params={"codes": codes, "days": max(20, min(int(lookback), 250)) * 2})
    if bars.empty:
        return {"status": "warn", "positions": [], "correlation_clusters": [], "warnings": ["持仓历史行情不足"]}
    bars["amount_proxy"] = pd.to_numeric(bars["close"], errors="coerce") * pd.to_numeric(bars["vol"], errors="coerce")
    close = bars.pivot(index="date", columns="code", values="close").tail(lookback)
    corr = close.pct_change().corr(min_periods=20)
    clusters = []
    for i, code_a in enumerate(codes):
        for code_b in codes[i + 1:]:
            value = corr.loc[code_a, code_b] if code_a in corr.index and code_b in corr.columns else None
            if pd.notna(value) and float(value) >= 0.75:
                clusters.append({"codes": [code_a, code_b], "correlation": round(float(value), 2), "status": "warning"})
    liquidity = bars.groupby("code")["amount_proxy"].mean().to_dict()
    position_rows = []
    for _, row in positions.iterrows():
        capital = float(row.get("capital_used") or 0)
        avg_amount = float(liquidity.get(str(row["code"])) or 0)
        capacity = capital / avg_amount * 100 if avg_amount > 0 else None
        position_rows.append({
            "code": str(row["code"]), "name": row.get("name"),
            "avg_amount_proxy": round(avg_amount, 2),
            "position_to_avg_amount_pct": round(capacity, 3) if capacity is not None else None,
            "liquidity_status": "warning" if capacity is not None and capacity > 1 else "ok",
        })
    two_limit_loss = float(pd.to_numeric(positions["capital_used"], errors="coerce").fillna(0).sum()) * 0.19
    return {
        "status": "warning" if clusters or any(item["liquidity_status"] == "warning" for item in position_rows) else "ok",
        "positions": position_rows, "correlation_clusters": clusters,
        "two_limit_down_loss": round(two_limit_loss, 2),
        "assumption": "连续两日各下跌10%的近似压力场景",
    }
