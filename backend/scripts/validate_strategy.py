import pandas as pd
import numpy as np
import sys
import os
from datetime import datetime, timedelta

# 加入后端路径
sys.path.append(os.path.join(os.getcwd(), "backend"))

from core.db import get_db_engine
from core.indicators import calculate_indicators
from core.strategy import check_consensus_strategy, evaluate_exit_signals
from core.analytics import calculate_risk_metrics

def run_strategy_validation():
    engine = get_db_engine()
    # 选取一些活跃度高的标的进行回测
    test_codes = ['603990', '300270', '002759', '600166', '300672', '601179', '300260', '001208', '601318', '000001']
    
    results = {
        "baseline": [],
        "optimized": []
    }
    
    print(f"Starting backtest for {len(test_codes)} stocks...")

    for code in test_codes:
        # 1. 加载数据 (使用别名映射到中文，以适配 calculate_indicators)
        query = f"""
            SELECT code, date as "日期", close as "收盘", open as "开盘", 
                   high as "最高", low as "最低", vol as "成交量"
            FROM daily_k 
            WHERE code = '{code}' 
            ORDER BY date ASC
        """
        df = pd.read_sql(query, engine)
        if len(df) < 150: continue
        
        df = calculate_indicators(df)
        
        # 2. 寻找买入点 (Consensus 策略)
        # 模拟逐日扫描
        for i in range(100, len(df) - 5):
            window = df.iloc[:i+1]
            match, stats = check_consensus_strategy(window)
            
            if match:
                entry_price = float(df.iloc[i]['收盘'])
                entry_date = df.iloc[i]['日期']
                
                # --- A. 运行基准策略卖出 (Baseline) ---
                baseline_exit_price = entry_price
                for j in range(i+1, min(i+11, len(df))): # 最多持仓10天
                    curr_low = float(df.iloc[j]['最低'])
                    curr_close = float(df.iloc[j]['收盘'])
                    # 固定止损
                    if (curr_low - entry_price) / entry_price <= -0.08:
                        baseline_exit_price = entry_price * 0.92
                        break
                    baseline_exit_price = curr_close
                
                results["baseline"].append({
                    "pl_pct": (baseline_exit_price - entry_price) / entry_price * 100,
                    "status": "CLOSED",
                    "entry_date": entry_date
                })
                
                # --- B. 运行优化策略卖出 (Optimized) ---
                opt_exit_price = entry_price
                high_since_entry = entry_price
                for j in range(i+1, min(i+21, len(df))): # 优化后允许持仓更久以抱住利润
                    curr_data = df.iloc[:j+1]
                    high_since_entry = max(high_since_entry, float(df.iloc[j]['最高']))
                    
                    # 调用新引擎
                    signals = evaluate_exit_signals(curr_data, entry_price, high_since_entry)
                    if any(s['level'] == 'critical' for s in signals):
                        opt_exit_price = float(df.iloc[j]['收盘'])
                        break
                    opt_exit_price = float(df.iloc[j]['收盘'])
                
                results["optimized"].append({
                    "pl_pct": (opt_exit_price - entry_price) / entry_price * 100,
                    "status": "CLOSED",
                    "entry_date": entry_date
                })

    # 3. 计算汇总指标
    base_metrics = calculate_risk_metrics(results["baseline"])
    opt_metrics = calculate_risk_metrics(results["optimized"])
    
    print("\n" + "="*40)
    print("STRATEGY VALIDATION REPORT")
    print("="*40)
    print(f"Total Signals: {len(results['baseline'])}")
    print("-" * 20)
    print(f"BASELINE (Old):")
    print(f"  Win Rate: {base_metrics.get('win_rate', 0)}%")
    print(f"  Avg Win: {base_metrics.get('avg_win', 0)}%")
    print(f"  Avg Loss: {base_metrics.get('avg_loss', 0)}%")
    print(f"  Profit Factor: {round(sum(x['pl_pct'] for x in results['baseline'] if x['pl_pct']>0) / abs(sum(x['pl_pct'] for x in results['baseline'] if x['pl_pct']<0)), 2)}")
    print(f"  Expectancy: {base_metrics.get('expectancy', 0)}%")
    
    print("-" * 20)
    print(f"OPTIMIZED (New):")
    print(f"  Win Rate: {opt_metrics.get('win_rate', 0)}%")
    print(f"  Avg Win: {opt_metrics.get('avg_win', 0)}%")
    print(f"  Avg Loss: {opt_metrics.get('avg_loss', 0)}%")
    # Manual calculate profit factor to avoid div zero
    pos = sum(x['pl_pct'] for x in results['optimized'] if x['pl_pct']>0)
    neg = abs(sum(x['pl_pct'] for x in results['optimized'] if x['pl_pct']<0))
    print(f"  Profit Factor: {round(pos/neg, 2) if neg > 0 else 9.9}")
    print(f"  Expectancy: {opt_metrics.get('expectancy', 0)}%")
    print(f"  Sharpe Ratio: {opt_metrics.get('sharpe_ratio', 0)}")
    print("="*40)

if __name__ == "__main__":
    run_strategy_validation()
