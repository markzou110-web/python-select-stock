import sys
import os
import pandas as pd
from datetime import datetime, timedelta

# 修正 Python 路径，确保能导入后端模块
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from core.indicators import calculate_indicators, get_weekly_indicators
from core.strategy import check_strategy
from core.db import get_db_engine
import akshare as ak

def debug_stock(code):
    print(f"\n🔍 --- 开始深入分析股票: {code} ---")
    
    # 1. 获取数据
    target_date = datetime.now()
    start_date = (target_date - timedelta(days=365)).strftime("%Y%m%d")
    
    try:
        print("📊 正在抓取前复权日线历史数据...")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, adjust="qfq")
        if df.empty:
            print("❌ 未在网络获取到数据。")
            return
    except Exception as e:
        print(f"❌ 数据拉取失败: {e}")
        return

    # 2. 计算指标
    print("🧪 正在计算技术指标 (EMA, RSI, MACD, BB, RS)...")
    df = calculate_indicators(df)
    
    # 3. 检查周线趋势 (TV 逻辑: 上周 EMA10 > EMA30)
    print("\n📅 [周线趋势检查]")
    weekly_ok = get_weekly_indicators(code, df=df, local_only=False)
    print(f"   - 结果: {'✅ 通关' if weekly_ok else '❌ 阻塞 (上周 EMA10w <= EMA30w)'}")

    # 4. 详细策略检查
    print("\n⚙️ [核心策略因子检查]")
    match, debug_info = check_strategy(df, use_bb_sqz=False) # 默认关闭 BB 压缩以更贴合 TV
    
    factors = {
        "was_sqz_recent": "均线近期粘合度 < 0.12",
        "is_breakout": "收盘价突破均线簇 (Close > Max_MA)",
        "is_volume": "当日放量 > 1.5倍 (20日均权)",
        "is_rsi_ok": "RSI 强度 >= 55",
        "is_macd_ok": "MACD 金叉状态 (DIF > DEA)",
        "is_rs_ok": "个股强于大盘 (RS > RS_MA50)"
    }
    
    for key, desc in factors.items():
        val = debug_info.get(key)
        status = "✅" if val else "❌"
        # 补充具体数值
        detail = ""
        if key == "squeeze": detail = f" (当前: {debug_info.get('squeeze')})"
        elif key == "vol_ratio": detail = f" (当前: {debug_info.get('vol_ratio')})"
        elif key == "rsi": detail = f" (当前: {debug_info.get('rsi')})"
        
        print(f"   {status} {desc}{detail}")

    print(f"\n📢 最终结论: {'🚀 触发买入共振！' if (match and weekly_ok) else '⚠️ 未满足全共振条件'}")
    if not match or not weekly_ok:
        reason = debug_info.get('reason', '未知')
        if not weekly_ok: reason = "周线趋势未走好," + reason
        print(f"   原因: {reason}")
    
    if match:
        print(f"   综合评分: {debug_info.get('Score')}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("请提供股票代码，例如: python debug_stock.py 600519")
    else:
        debug_stock(sys.argv[1])
