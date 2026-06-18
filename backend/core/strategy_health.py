from datetime import date, timedelta
from typing import Any, Dict

import pandas as pd
from sqlalchemy import text

from core.performance_metrics import return_metrics
from core.pro_workflow import classify_strategy_health
from core.risk_constants import FIXED_STOP_LOSS_PCT, TAKE_PROFIT_PCT


def _apply_stop_take_model(price: float, future_closes: list[float]) -> float:
    """简化止损/止盈模型：返回受保护后的 5 日收益（百分比）。

    - 若 5 日内任一日收盘 ≤ 止损线（price * (1 + FIXED_STOP_LOSS_PCT/100)）→ 计为止损。
    - 否则若 5 日内任一日收盘 ≥ 止盈线（price * (1 + TAKE_PROFIT_PCT/100)）→ 计为止盈。
    - 否则取第 5 日实际收益。
    这样熔断指标反映"止损保护的经济性"，而非裸价格波动。
    """
    if not future_closes or price <= 0:
        return 0.0
    stop_line = price * (1.0 + FIXED_STOP_LOSS_PCT / 100.0)   # e.g. 0.91 * price
    take_line = price * (1.0 + TAKE_PROFIT_PCT / 100.0)       # e.g. 1.15 * price
    if any(c <= stop_line for c in future_closes):
        return float(FIXED_STOP_LOSS_PCT)
    if any(c >= take_line for c in future_closes):
        return float(TAKE_PROFIT_PCT)
    last = future_closes[-1]
    return (last - price) / price * 100.0


def build_strategy_health(engine, days: int = 120) -> Dict[str, Any]:
    """Evaluate recent verified 5-day returns for automatic strategy controls.

    收益口径采用"简化止损模型"（见 _apply_stop_take_model），反映止损保护的经济性，
    而非裸价格波动——一个实际被 -9% 止损出场的信号不会因后续反弹而显示为盈利。
    计算在 Python 端完成（跨库兼容 PG/SQLite），SQL 只负责取信号与未来5日收盘。
    """
    if engine is None:
        return {"status": "error", "strategies": {}}
    try:
        # 在 Python 端算截止日，避免 PG/SQLite 的 interval 语法差异（跨库兼容）。
        cutoff = (date.today() - timedelta(days=int(days))).isoformat()
        # 按方言选择"未来5日收盘聚合"：SQLite 用 GROUP_CONCAT（直接在标量子查询中工作），
        # PG 用嵌套子查询 + string_agg（PG 不允许聚合与 ORDER BY/LIMIT 同级）。
        # 止损/止盈模型在 Python 端计算（_apply_stop_take_model），SQL 只取原始收盘序列。
        dialect = getattr(engine, "dialect", None)
        is_sqlite = bool(dialect) and dialect.name == "sqlite"
        if is_sqlite:
            future_subquery = """(SELECT GROUP_CONCAT(d.close, ',')
                    FROM daily_k d
                    WHERE d.code = s.code AND d.date > s.date
                    ORDER BY d.date ASC LIMIT 5) AS future_csv"""
        else:
            # PG: 先取5行（有序），再 string_agg
            future_subquery = """(SELECT string_agg(sub.close::text, ',')
                    FROM (
                        SELECT d2.close
                        FROM daily_k d2
                        WHERE d2.code = s.code AND d2.date > s.date
                        ORDER BY d2.date ASC LIMIT 5
                    ) sub) AS future_csv"""
        # 修复 BUG-A: PG 的 JSONB 列不支持 LIKE 操作符。
        # PG 用 JSONB 路径操作符 ->>；SQLite（JSON 存为 TEXT）用 LIKE。
        eligible_filter = (
            'AND s.price_action_detail LIKE \'%"research_eligible": true%\''
            if is_sqlite
            else "AND COALESCE((s.price_action_detail->>'research_eligible')::boolean, false) = true"
        )
        df = pd.read_sql(text(f"""
            SELECT s.strategy_type, s.code, s.date, s.price,
                   {future_subquery}
            FROM scan_history s
            WHERE s.date >= :cutoff
              AND s.price > 0
              {eligible_filter}
        """), engine, params={"cutoff": cutoff})
    except Exception as exc:
        return {"status": "error", "strategies": {}, "error": str(exc)}

    # 在 Python 端应用止损/止盈模型（跨库一致、易测试）
    df["ret_5d"] = [
        _apply_stop_take_model(float(p), [float(x) for x in csv.split(",")] if csv else [])
        for p, csv in zip(df["price"], df.get("future_csv", []))
    ]

    strategies = {}
    for strategy, group in df.groupby("strategy_type", dropna=False):
        metrics = return_metrics(group["ret_5d"])
        strategies[str(strategy or "unknown")] = {**metrics, **classify_strategy_health(metrics)}
    return {"status": "ok", "strategies": strategies}


def apply_strategy_health_controls(results: list[dict], health: Dict[str, Any]) -> None:
    strategies = health.get("strategies") or {}
    for row in results:
        strategy = str(row.get("strategy_type") or "unknown")
        strategy_health = strategies.get(strategy)
        if not strategy_health:
            continue
        row["strategy_health"] = strategy_health
        status = strategy_health.get("status")
        if status == "PAUSED":
            row["trade_eligible"] = False
            row["trade_bucket"] = "OBSERVE"
            blockers = list(row.get("trade_blockers") or [])
            blockers.append(f"策略近期负期望，自动暂停：{strategy_health.get('reason')}")
            row["trade_blockers"] = list(dict.fromkeys(blockers))
        elif status == "DOWNWEIGHT":
            row["calibrated_score"] = round(max(0, float(row.get("calibrated_score") or 0) - 10), 1)
            row["Score"] = row["calibrated_score"]
