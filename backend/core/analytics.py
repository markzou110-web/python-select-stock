import numpy as np
import pandas as pd
from typing import List, Dict, Any

def run_monte_carlo(returns: List[float], iterations: int = 1000, trade_count: int = 50) -> Dict[str, Any]:
    """
    基于历史收益率进行蒙特卡洛模拟，预测未来组合表现分布。
    """
    if not returns:
        return {"distribution": [], "p90": 0, "p50": 0, "p10": 0}
    
    # 转换为百分比
    rets = np.array(returns) / 100.0
    
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
        "min": round(float(np.min(simulations)), 2)
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


def calculate_risk_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
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
    
    returns = [t['pl_pct'] / 100 for t in closed]
    
    # --- 夏普比率 (Sharpe Ratio) ---
    # 假设无风险利率 2.5% 年化, 每笔交易约 5 天
    trades_per_year = 252 / 5  # ≈ 50 笔
    rf_per_trade = 0.025 / trades_per_year
    
    avg_ret = np.mean(returns)
    std_ret = np.std(returns) if len(returns) > 1 else 0.001
    sharpe = round((avg_ret - rf_per_trade) / std_ret * np.sqrt(trades_per_year), 2) if std_ret > 0 else 0
    
    # --- 权益曲线 (Equity Curve) ---
    sorted_trades = sorted(closed, key=lambda t: t.get('entry_date', ''))
    equity = 100.0  # 起始 100
    equity_curve = [{"date": "", "equity": 100.0}]
    peak = 100.0
    max_dd = 0.0
    
    for t in sorted_trades:
        pct = t['pl_pct'] / 100
        equity *= (1 + pct)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd
        
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
        "expectancy": expectancy
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
        "squeeze": "均线粘合",
        "pine": "Pine共振",
        "tv_zp": "TV-ZP",
        "both": "双重共振",
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
