"""买入时点周报：对比"信号日尾盘买入" vs "次日开盘买入"两种策略的胜率。

复用 backtest_lab.run_single_stock_backtest 的两种 entry_mode：
  - signal_close:      信号日收盘价买入（≈ 尾盘买入）
  - next_open_confirm: 次日开盘买入（≈ 提示后次日即买）

每周一 09:00 由 celery beat 触发，汇总近 30 天推送过的票，跑双臂回测，
推送 bark 报告。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text

from core.analytics import compute_profit_factor, compute_win_rate
from core.backtest_lab import run_single_stock_backtest
from core.db import get_db_engine, load_from_db
from core.indicators import calculate_indicators, calculate_pine_indicators

logger = logging.getLogger(__name__)

# 默认回测参数（与 batch_experiment 保持一致）
DEFAULT_PARAMS = {
    "max_hold_days": 10,
    "stop_loss_pct": -8.0,
    "slippage_bps": 5.0,
    "position_pct": 1.0,
}
# 两种 entry_mode 对应的策略标签
ENTRY_MODES = {
    "signal_close": "尾盘买",
    "next_open_confirm": "次日买",
}
# 策略 → 是否需要 pine 指标
_PINE_STRATEGIES = {"pine", "tv_zp"}


def _load_universe(engine, days: int = 30, limit: int = 60) -> List[Dict[str, str]]:
    """取近 days 天推送过的票（scan_history 去重），按 strategy_type 分组。

    返回 [{code, strategy_type}, ...]
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT code, strategy_type
                FROM scan_history
                WHERE COALESCE(data_date, date) >= :cutoff
                  AND strategy_type IN ('pine', 'squeeze', 'consensus', 'tv_zp')
                ORDER BY code
                LIMIT :limit
            """), {"cutoff": cutoff, "limit": limit}).fetchall()
        return [{"code": r[0], "strategy_type": r[1]} for r in rows]
    except Exception as exc:
        logger.warning(f"_load_universe failed: {exc}")
        return []


def _prepare_df(code: str, strategy_type: str, engine, lookback_days: int = 500) -> Optional[pd.DataFrame]:
    """加载 K线 + 补全指标列。返回 None 表示数据不足。"""
    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    df = load_from_db(code, start, engine)
    if len(df) < 120:
        return None
    enable_pine = strategy_type in _PINE_STRATEGIES
    df = calculate_indicators(df, enable_pine_indicators=enable_pine)
    if enable_pine and "RF_Upward" not in df.columns:
        df = calculate_pine_indicators(df)
    return df


def _backtest_both_arms(df: pd.DataFrame, strategy_type: str, params: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """对同一只票跑两种 entry_mode，返回 {mode: summary_metrics}。"""
    result = {}
    for mode in ENTRY_MODES:
        try:
            bt = run_single_stock_backtest(df, strategy_type=strategy_type, params={**params, "entry_mode": mode})
            s = bt["summary"]
            trades = bt.get("trades", [])
            pl_pcts = [float(t.get("return_pct", 0)) for t in trades]
            result[mode] = {
                "signal_count": s.get("signal_count", 0),
                "win_rate": s.get("win_rate", 0),
                "avg_return": s.get("avg_return", 0),
                "profit_factor": s.get("profit_factor", 0),
                "trade_count": len(trades),
                "pl_pcts": pl_pcts,
            }
        except Exception as exc:
            logger.debug(f"backtest {strategy_type} {mode} failed: {exc}")
            result[mode] = {"signal_count": 0, "win_rate": 0, "avg_return": 0,
                            "profit_factor": 0, "trade_count": 0, "pl_pcts": []}
    return result


def build_entry_timing_report(days: int = 30, max_codes: int = 50) -> Dict[str, Any]:
    """生成买入时点周报。

    Args:
        days: 回溯天数（取近 N 天推送过的票）
        max_codes: 最多回测多少只票（控制耗时）

    Returns:
        {
            "meta": {generated_at, days, universe_size, effective_size},
            "aggregate": {mode: {win_rate, avg_return, profit_factor, total_signals}},
            "per_stock": [{code, strategy_type, close: {...}, open: {...}, winner}],
            "body": "多行报告文本（供 bark 推送）",
        }
    """
    engine = get_db_engine()
    if not engine:
        return {"meta": {"error": "no db engine"}, "body": "买入时点周报：数据库未连接"}

    universe = _load_universe(engine, days=days, limit=max_codes)
    effective: List[Dict[str, Any]] = []
    all_pl: Dict[str, List[float]] = {"signal_close": [], "next_open_confirm": []}

    for item in universe:
        code, strat = item["code"], item["strategy_type"]
        df = _prepare_df(code, strat, engine)
        if df is None:
            continue
        arms = _backtest_both_arms(df, strat, DEFAULT_PARAMS)
        close_arm = arms.get("signal_close", {})
        open_arm = arms.get("next_open_confirm", {})
        # 只统计有信号的票
        if close_arm.get("signal_count", 0) == 0 and open_arm.get("signal_count", 0) == 0:
            continue

        # 判定单只票赢家
        close_score = close_arm.get("avg_return", 0)
        open_score = open_arm.get("avg_return", 0)
        if abs(close_score - open_score) < 0.5:
            winner = "持平"
        elif close_score > open_score:
            winner = "尾盘买"
        else:
            winner = "次日买"

        effective.append({
            "code": code,
            "strategy_type": strat,
            "close": {k: close_arm.get(k) for k in ("signal_count", "win_rate", "avg_return", "profit_factor")},
            "open": {k: open_arm.get(k) for k in ("signal_count", "win_rate", "avg_return", "profit_factor")},
            "winner": winner,
        })
        all_pl["signal_close"].extend(close_arm.get("pl_pcts", []))
        all_pl["next_open_confirm"].extend(open_arm.get("pl_pcts", []))

    # 聚合两种策略的全局指标
    aggregate = {}
    for mode, label in ENTRY_MODES.items():
        pl = all_pl[mode]
        aggregate[mode] = {
            "label": label,
            "total_signals": len(pl),
            "win_rate": compute_win_rate(pl),
            "avg_return": round(float(pd.Series(pl).mean()) if pl else 0, 2),
            "profit_factor": compute_profit_factor(pl),
        }

    body = _build_report_body(aggregate, effective)
    return {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "days": days,
            "universe_size": len(universe),
            "effective_size": len(effective),
        },
        "aggregate": aggregate,
        "per_stock": effective,
        "body": body,
    }


def _build_report_body(aggregate: Dict[str, Dict[str, Any]], per_stock: List[Dict[str, Any]]) -> str:
    """构建多行 bark 报告文本。"""
    if not per_stock:
        return "买入时点周报：本周无有效回测样本（推送票均无策略信号）。"

    close_agg = aggregate["signal_close"]
    open_agg = aggregate["next_open_confirm"]
    # 全局赢家
    if abs(close_agg["avg_return"] - open_agg["avg_return"]) < 0.5:
        global_winner = "两种策略持平"
    elif close_agg["avg_return"] > open_agg["avg_return"]:
        global_winner = "尾盘买入更优"
    else:
        global_winner = "次日开盘买更优"

    lines = [
        f"买入时点周报 | {datetime.now().strftime('%Y-%m-%d')}",
        f"样本：{len(per_stock)} 只票，{close_agg['total_signals']} 个信号",
        "",
        "【整体对比】",
        f"尾盘买：胜率 {close_agg['win_rate']}% | 平均 {close_agg['avg_return']:+.2f}% | 盈亏比 {close_agg['profit_factor']}",
        f"次日买：胜率 {open_agg['win_rate']}% | 平均 {open_agg['avg_return']:+.2f}% | 盈亏比 {open_agg['profit_factor']}",
        f"结论：{global_winner}",
        "",
        "【个股明细 Top 5（按收益差排序）】",
    ]

    # 按两种策略收益差排序，展示差异最大的 5 只
    sorted_stocks = sorted(
        per_stock,
        key=lambda x: abs(x["close"]["avg_return"] - x["open"]["avg_return"]),
        reverse=True,
    )[:5]
    for s in sorted_stocks:
        c, o = s["close"], s["open"]
        lines.append(
            f"{s['code']}({s['winner']}): "
            f"尾盘 win{c['win_rate']}% avg{c['avg_return']:+.1f}% | "
            f"次日 win{o['win_rate']}% avg{o['avg_return']:+.1f}%"
        )

    # 统计个股偏好分布
    close_wins = sum(1 for s in per_stock if s["winner"] == "尾盘买")
    open_wins = sum(1 for s in per_stock if s["winner"] == "次日买")
    ties = sum(1 for s in per_stock if s["winner"] == "持平")
    lines.append("")
    lines.append(f"个股偏好：尾盘更优 {close_wins} | 次日更优 {open_wins} | 持平 {ties}")

    return "\n".join(lines)
