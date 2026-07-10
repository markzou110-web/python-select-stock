from datetime import date, timedelta
import json
from typing import Any, Dict

import pandas as pd
from sqlalchemy import text

from core.performance_metrics import return_metrics
from core.pro_workflow import classify_strategy_health
from core.risk_constants import FIXED_STOP_LOSS_PCT, TAKE_PROFIT_PCT


def _apply_stop_take_model(price: float, future_bars: list[tuple[float, float, float]]) -> float:
    """简化止损/止盈模型：返回受保护后的 5 日收益（百分比）。

    - 按交易日顺序，用最低价/最高价判断止损和止盈。
    - 同一交易日同时触发止损和止盈时按保守原则计为止损。
    - 否则取第 5 日实际收益。
    这样熔断指标反映"止损保护的经济性"，而非裸价格波动。
    """
    if not future_bars or price <= 0:
        return 0.0
    stop_line = price * (1.0 + FIXED_STOP_LOSS_PCT / 100.0)   # e.g. 0.91 * price
    take_line = price * (1.0 + TAKE_PROFIT_PCT / 100.0)       # e.g. 1.15 * price
    for low, high, _close in future_bars:
        if low <= stop_line:
            return float(FIXED_STOP_LOSS_PCT)
        if high >= take_line:
            return float(TAKE_PROFIT_PCT)
    last = future_bars[-1][2]
    return (last - price) / price * 100.0


def _detail_value(value: Any, key: str, default: str = "UNKNOWN") -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return str((value or {}).get(key) or default)


def _segment_key(strategy: str, regime: str, sector_phase: str) -> str:
    return f"{strategy}|{regime}|{sector_phase}"


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
            future_subquery = """(SELECT GROUP_CONCAT(
                        printf('%f:%f:%f', sub.low, sub.high, sub.close), ','
                    ) FROM (
                        SELECT d.low, d.high, d.close
                        FROM daily_k d
                        WHERE d.code = s.code AND d.date > s.date
                        ORDER BY d.date ASC LIMIT 5
                    ) sub) AS future_csv"""
        else:
            # PG: 先取5行（有序），再 string_agg
            future_subquery = """(SELECT string_agg(
                        CONCAT_WS(':', sub.low::text, sub.high::text, sub.close::text), ','
                    )
                    FROM (
                        SELECT d2.low, d2.high, d2.close
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
            SELECT s.strategy_type, s.code, s.date, s.price, s.price_action_detail,
                   {future_subquery}
            FROM scan_history s
            WHERE s.date >= :cutoff
              AND s.price > 0
              {eligible_filter}
        """), engine, params={"cutoff": cutoff})
    except Exception as exc:
        return {"status": "error", "strategies": {}, "error": str(exc)}

    # 在 Python 端应用止损/止盈模型（跨库一致、易测试）
    future_bars = [
        [tuple(float(value) for value in bar.split(":")) for bar in csv.split(",")]
        if csv else []
        for csv in df.get("future_csv", [])
    ]
    # 未满 5 个未来交易日的信号不参与健康判定，避免把未成熟样本记成 0% 收益。
    df["ret_5d"] = [
        _apply_stop_take_model(float(price), bars) if len(bars) >= 5 else None
        for price, bars in zip(df["price"], future_bars)
    ]
    df["market_regime"] = df["price_action_detail"].apply(lambda value: _detail_value(value, "market_regime"))
    df["sector_phase"] = df["price_action_detail"].apply(lambda value: _detail_value(value, "sector_phase"))

    strategies = {}
    for strategy, group in df.groupby("strategy_type", dropna=False):
        metrics = return_metrics(group["ret_5d"])
        strategies[str(strategy or "unknown")] = {**metrics, **classify_strategy_health(metrics)}
    segments = {}
    for (strategy, regime, sector_phase), group in df.groupby(
        ["strategy_type", "market_regime", "sector_phase"], dropna=False
    ):
        metrics = return_metrics(group["ret_5d"])
        strategy_name = str(strategy or "unknown")
        regime_name = str(regime or "UNKNOWN")
        phase_name = str(sector_phase or "UNKNOWN")
        segments[_segment_key(strategy_name, regime_name, phase_name)] = {
            "strategy_type": strategy_name,
            "market_regime": regime_name,
            "sector_phase": phase_name,
            **metrics,
            **classify_strategy_health(metrics),
        }
    return {"status": "ok", "strategies": strategies, "segments": segments}


def apply_strategy_health_controls(results: list[dict], health: Dict[str, Any]) -> None:
    strategies = health.get("strategies") or {}
    segments = health.get("segments") or {}
    for row in results:
        strategy = str(row.get("strategy_type") or "unknown")
        segment_key = _segment_key(
            strategy,
            str(row.get("market_regime") or "UNKNOWN"),
            str(row.get("sector_phase") or "UNKNOWN"),
        )
        segment_health = segments.get(segment_key)
        strategy_health = segment_health if int((segment_health or {}).get("signals") or 0) >= 20 else strategies.get(strategy)
        if not strategy_health:
            continue
        row["strategy_health"] = strategy_health
        row["strategy_health_segment"] = segment_key if strategy_health is segment_health else None
        row["strategy_health_scope"] = "segment" if strategy_health is segment_health else "strategy"
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
