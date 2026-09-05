import numpy as np
import pandas as pd
from typing import List, Dict, Any, Sequence, Tuple


# ── 改动 #13：度量统一规范 helper ──
# 全仓库唯一的回撤/profit_factor/win_rate 计算口径。各模块（backtest_lab、
# paper_trade、analytics）应调用这些 helper，保留各自的字段名/符号/上限契约。
# 复利权益（起 100）高水位回撤，避免 paper_trade 旧实现用 pl_pct 累加和的数学错误。

def compute_equity_curve_drawdown(pl_pcts: Sequence[float]) -> Tuple[float, List[Dict[str, Any]]]:
    """复利权益曲线最大回撤。

    Args:
        pl_pcts: 每笔交易的盈亏百分比（如 5.0 表示 +5%，-3.0 表示 -3%），按时间顺序。

    Returns:
        (max_drawdown_positive, equity_curve)
        - max_drawdown_positive: 最大回撤（**正值**百分比，如 12.3 表示 -12.3%）
        - equity_curve: [{date, equity}]（date 为空，供调用方附加）
    """
    equity = 100.0
    peak = 100.0
    max_dd = 0.0
    curve: List[Dict[str, Any]] = [{"date": "", "equity": round(equity, 2)}]
    for pct in pl_pcts:
        equity *= (1 + float(pct) / 100.0)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        curve.append({"date": "", "equity": round(equity, 2)})
    return round(max_dd, 2), curve


def compute_profit_factor(pl_pcts: Sequence[float], cap: float = 99.0) -> float:
    """毛额 profit_factor = 总盈利 / |总亏损|，可配上限 cap。

    无亏损时：有盈利返回 cap，无盈利返回 0。
    """
    gains = [p for p in pl_pcts if p > 0]
    losses = [p for p in pl_pcts if p < 0]
    gross_profit = float(sum(gains))
    gross_loss = abs(float(sum(losses)))
    if gross_loss > 0:
        return round(min(gross_profit / gross_loss, cap), 2)
    return cap if gross_profit > 0 else 0.0


def compute_win_rate(pl_pcts: Sequence[float], ndigits: int = 1) -> float:
    """胜率 = 盈利笔数 / 总笔数（>0 计盈，0% 计非盈）。"""
    total = len(pl_pcts)
    if total == 0:
        return 0.0
    wins = sum(1 for p in pl_pcts if p > 0)
    return round(wins / total * 100.0, ndigits)


def run_monte_carlo(
    returns: List[float], iterations: int = 1000, trade_count: int = 50,
    position_pct: float = 5.0,
) -> Dict[str, Any]:
    """
    基于历史收益率进行蒙特卡洛模拟，预测未来组合表现分布。
    """
    if not returns:
        return {"distribution": [], "p90": 0, "p50": 0, "p10": 0}
    
    # 转换为百分比
    position_pct = max(0.0, min(float(position_pct), 100.0))
    rets = np.array(returns) / 100.0 * position_pct / 100.0
    
    simulations = []
    for _ in range(iterations):
        # 有放回抽样
        sample = np.random.choice(rets, size=trade_count, replace=True)
        # 计算累计收益 (复利模型)
        final_return = (np.prod(1 + sample) - 1) * 100
        simulations.append(final_return)
    
    simulations = np.array(simulations)
    
    # 计算分布统计
    hist, bin_edges = np.histogram(simulations, bins=20)
    distribution = []
    for i in range(len(hist)):
        distribution.append({
            "range": f"{round(bin_edges[i])}% ~ {round(bin_edges[i+1])}%",
            "count": int(hist[i]),
            "value": round(float(bin_edges[i]), 1)
        })
        
    return {
        "distribution": distribution,
        "p90": round(float(np.percentile(simulations, 90)), 2),
        "p50": round(float(np.percentile(simulations, 50)), 2),
        "p10": round(float(np.percentile(simulations, 10)), 2),
        "max": round(float(np.max(simulations)), 2),
        "min": round(float(np.min(simulations)), 2),
        "assumptions": {"position_pct": position_pct, "trade_count": trade_count},
    }

def calculate_rolling_performance(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    计算滚动收益率。
    """
    if not trades: return []
    
    df = pd.DataFrame(trades)
    df['entry_date'] = pd.to_datetime(df['entry_date'])
    df = df.sort_values('entry_date')
    
    # 以每 5 笔交易为一个小波段查看胜率和收益
    window = 5
    rolling_data = []
    
    for i in range(window, len(df) + 1):
        subset = df.iloc[i-window : i]
        avg_ret = subset['pl_pct'].mean()
        win_rate = (subset['pl_pct'] > 0).sum() / window * 100
        
        rolling_data.append({
            "date": str(subset['entry_date'].iloc[-1].date()),
            "avg_return": round(float(avg_ret), 2),
            "win_rate": round(float(win_rate), 1)
        })
        
    return rolling_data


def calculate_risk_metrics(
    trades: List[Dict[str, Any]], default_position_pct: float = 100.0,
) -> Dict[str, Any]:
    """
    计算高级风险指标：夏普比率、卡尔马比率、权益曲线、连续亏损等。
    
    Args:
        trades: 交易记录列表 (需包含 pl_pct, entry_date, status 字段)
    
    Returns:
        包含多维风险指标的字典
    """
    if not trades:
        return {
            "sharpe_ratio": 0, "calmar_ratio": 0,
            "equity_curve": [], "max_consecutive_losses": 0,
            "avg_win": 0, "avg_loss": 0, "expectancy": 0
        }
    
    # 只计算已平仓的交易
    closed = [t for t in trades if t.get('status') == 'CLOSED']
    if not closed:
        return {
            "sharpe_ratio": 0, "calmar_ratio": 0,
            "equity_curve": [], "max_consecutive_losses": 0,
            "avg_win": 0, "avg_loss": 0, "expectancy": 0
        }
    
    def portfolio_return_pct(trade: Dict[str, Any]) -> float:
        raw_position = trade.get("position_pct")
        try:
            position = float(raw_position) if raw_position is not None and not pd.isna(raw_position) else float(default_position_pct)
        except (TypeError, ValueError):
            position = float(default_position_pct)
        return float(trade['pl_pct']) * max(0.0, min(position, 100.0)) / 100.0

    returns = [portfolio_return_pct(t) / 100 for t in closed]
    
    # --- 夏普比率 (Sharpe Ratio) ---
    # 假设无风险利率 2.5% 年化, 每笔交易约 5 天
    trades_per_year = 252 / 5  # ≈ 50 笔
    rf_per_trade = 0.025 / trades_per_year
    
    avg_ret = np.mean(returns)
    std_ret = np.std(returns) if len(returns) > 1 else 0.001
    sharpe = round((avg_ret - rf_per_trade) / std_ret * np.sqrt(trades_per_year), 2) if std_ret > 0 else 0
    
    # --- 权益曲线 (Equity Curve) — 改动 #13：复用规范 helper（复利权益高水位回撤） ---
    sorted_trades = sorted(closed, key=lambda t: t.get('entry_date', ''))
    trade_pl_pcts = [portfolio_return_pct(t) for t in sorted_trades]
    max_dd, equity_curve_skeleton = compute_equity_curve_drawdown(trade_pl_pcts)
    # 重新附加真实日期到 equity_curve（helper 返回的 date 为占位空串）
    equity_curve = [{"date": "", "equity": 100.0}]
    equity = 100.0
    for t, _skel in zip(sorted_trades, equity_curve_skeleton[1:]):
        equity *= (1 + portfolio_return_pct(t) / 100)
        equity_curve.append({
            "date": str(t.get('entry_date', ''))[:10],
            "equity": round(equity, 2)
        })
    
    # --- 卡尔马比率 (Calmar Ratio) ---
    total_return = (equity - 100) / 100 * 100  # 百分比
    calmar = round(total_return / max_dd, 2) if max_dd > 0 else (9.9 if total_return > 0 else 0)
    
    # --- 最大连续亏损次数 ---
    max_consecutive_losses = 0
    current_losses = 0
    for r in returns:
        if r < 0:
            current_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_losses)
        else:
            current_losses = 0
    
    # --- 平均盈亏 ---
    wins = [r * 100 for r in returns if r > 0]
    losses = [r * 100 for r in returns if r < 0]
    avg_win = round(np.mean(wins), 2) if wins else 0
    avg_loss = round(np.mean(losses), 2) if losses else 0
    
    # --- 期望值 (Expectancy) ---
    win_count = len(wins)
    total_count = len(returns)
    win_rate = round(win_count / total_count * 100, 1) if total_count > 0 else 0
    loss_rate = 1 - (win_rate / 100)
    expectancy = round((win_rate / 100) * avg_win + loss_rate * avg_loss, 2)
    
    return {
        "sharpe_ratio": sharpe,
        "calmar_ratio": calmar,
        "win_rate": win_rate,
        "equity_curve": equity_curve,
        "max_consecutive_losses": max_consecutive_losses,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "portfolio_return_pct": round(total_return, 2),
        "assumptions": {"default_position_pct": float(default_position_pct)},
    }


def calculate_pnl_attribution(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    按维度归因盈亏：行业、策略类型。
    
    Args:
        trades: 交易记录列表 (需包含 pl_pct, industry, strategy_type 等字段)
    
    Returns:
        按行业和策略分组的盈亏归因数据
    """
    if not trades:
        return {"by_industry": [], "by_strategy": []}
    
    closed = [t for t in trades if t.get('status') == 'CLOSED']
    if not closed:
        return {"by_industry": [], "by_strategy": []}
    
    # --- 1. 按行业归因 ---
    by_industry: Dict[str, List[float]] = {}
    for t in closed:
        ind = t.get('industry', '未知')
        by_industry.setdefault(ind, []).append(t['pl_pct'])
    
    industry_attribution = [
        {
            "name": k,
            "total_pnl": round(sum(v), 2),
            "count": len(v),
            "avg_pnl": round(float(np.mean(v)), 2),
            "win_rate": round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else 0
        }
        for k, v in by_industry.items()
    ]
    industry_attribution.sort(key=lambda x: x["total_pnl"], reverse=True)
    
    # --- 2. 按策略类型归因 ---
    by_strategy: Dict[str, List[float]] = {}
    for t in closed:
        st = t.get('strategy_type', 'unknown') or 'unknown'
        by_strategy.setdefault(st, []).append(t['pl_pct'])
    
    strategy_labels = {
        "tv_dual_strict": "TV强共振",
        "tv_dual": "TV双策略",
        "squeeze": "均线粘合（单策略）",
        "pine": "五指标投票共振",
        "tv_zp": "TV-ZP趋势信号",
        "both": "均线+五指标共振",
        "consensus": "共识策略",
        "unknown": "未知"
    }
    
    strategy_attribution = [
        {
            "name": strategy_labels.get(k, k),
            "total_pnl": round(sum(v), 2),
            "count": len(v),
            "avg_pnl": round(float(np.mean(v)), 2),
            "win_rate": round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else 0
        }
        for k, v in by_strategy.items()
    ]
    strategy_attribution.sort(key=lambda x: x["total_pnl"], reverse=True)
    
    return {
        "by_industry": industry_attribution,
        "by_strategy": strategy_attribution
    }
