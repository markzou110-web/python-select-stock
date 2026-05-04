import sys
import os
import pandas as pd
from datetime import datetime

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from core.db import get_db_engine
from core.data import get_market_regime, get_market_snapshot
from core.strategy import get_signal_details

def run_deep_dive_report():
    engine = get_db_engine()
    if not engine:
        print("Error: Database engine not available. Check db_config.json.")
        return

    # 1. Market Regime
    regime = get_market_regime()
    print(f"=== 市场环境报告 ({regime.get('updated_at')}) ===")
    print(f"当前状态: {regime.get('status')} - {regime.get('desc')}")
    for name, data in regime.get('indices', {}).items():
        print(f"  {name}: 指数 {data['close']} / EMA20 {data['ema20']} ({data['trend']})")
    print("-" * 40)

    # 2. Analyze Open Positions
    try:
        query = "SELECT * FROM paper_trading WHERE status = 'OPEN'"
        trades = pd.read_sql(query, engine)
        
        if trades.empty:
            print("目前暂无持仓中的模拟盘记录。")
            # Suggest running a full scan
        else:
            print(f"发现 {len(trades)} 只持仓标的，正在进行深度扫描...")
            snapshot = get_market_snapshot()
            
            for _, trade in trades.iterrows():
                code = trade['code']
                name = trade['name']
                entry_price = float(trade['entry_price'])
                
                # Fetch detailed signal/trailing stop
                details = get_signal_details(code, name)
                
                # Get current price
                curr_price = entry_price
                if not snapshot.empty:
                    match = snapshot[snapshot['code'] == code]
                    if not match.empty:
                        curr_price = float(match.iloc[0]['price'])
                
                pl_pct = (curr_price - entry_price) / entry_price * 100
                
                print(f"[标的: {name} ({code})]")
                print(f"  入场价: {entry_price:.2f} | 当前价: {curr_price:.2f} | 浮盈: {pl_pct:+.2f}%")
                
                if 'trailing_stops' in details and details['trailing_stops']:
                    latest_stop = details['trailing_stops'][-1]['value']
                    dist_to_stop = (curr_price - latest_stop) / curr_price * 100
                    print(f"  当前追踪止损价: {latest_stop:.2f} (距离当前价 {dist_to_stop:.2f}%)")
                    
                    if curr_price <= latest_stop:
                        print("  ⚠️ 警报: 价格已激活移动止损，建议择机离场！")
                    elif pl_pct < -8:
                        print("  ⚠️ 警报: 已触及固定止损位 (-8%)")
                else:
                    print("  无法获取动态追踪止损数据。")
                print("-" * 20)
    except Exception as e:
        print(f"分析过程中出现错误: {e}")

if __name__ == "__main__":
    run_deep_dive_report()
