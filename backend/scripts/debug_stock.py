"""诊断脚本：检查指定股票的 Pine 策略指标计算结果"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.db import get_db_engine, load_from_db
from core.indicators import calculate_indicators, calculate_pine_indicators
from core.strategy import check_pine_strategy, check_strategy
from datetime import datetime, timedelta
import pandas as pd

code = "300598"  # 诚迈科技
print(f"\n{'='*60}")
print(f"  诊断股票: {code} (诚迈科技)")
print(f"{'='*60}")

# 1. 加载数据
engine = get_db_engine()
df = load_from_db(code, (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d"), engine)
print(f"\n[1] 数据加载: {len(df)} 条记录")
print(f"    日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")

# 2. 计算指标
df = calculate_indicators(df, enable_pine_indicators=True)
curr = df.iloc[-1]

print(f"\n[2] 最新一行数据 ({df['日期'].iloc[-1]}):")
print(f"    收盘: {curr['收盘']}")
print(f"    开盘: {curr['开盘']}")
print(f"    最高: {curr['最高']}")
print(f"    最低: {curr['最低']}")
print(f"    成交量: {curr['成交量']}")

# 3. Range Filter 诊断
print(f"\n[3] Range Filter 诊断:")
print(f"    RF_Filter (过滤线值): {curr.get('RF_Filter', 'N/A')}")
print(f"    RF_Upward: {curr.get('RF_Upward', 'N/A')}")
print(f"    RF_Downward: {curr.get('RF_Downward', 'N/A')}")
print(f"    收盘 vs RF_Filter: {'收盘 > RF' if curr['收盘'] > curr.get('RF_Filter', 0) else '收盘 <= RF'}")

# 查看最近 5 天的 RF 变化
print(f"\n    最近5天 RF_Filter 趋势:")
for i in range(-5, 0):
    row = df.iloc[i]
    rf = row.get('RF_Filter', 0)
    up = row.get('RF_Upward', False)
    dn = row.get('RF_Downward', False)
    print(f"      {row['日期']}: 收盘={row['收盘']:.2f}, RF={rf:.2f}, Up={up}, Down={dn}")

# 4. QQE 诊断
print(f"\n[4] QQE Mod 诊断:")
print(f"    QQE_Long: {curr.get('QQE_Long', 'N/A')}")

# 5. Volume 诊断
vol_ratio = curr['成交量'] / curr['Vol_MA20'] if curr['Vol_MA20'] > 0 else 0
print(f"\n[5] Volume 诊断:")
print(f"    当日成交量: {curr['成交量']:.0f}")
print(f"    Vol_MA20: {curr['Vol_MA20']:.0f}")
print(f"    量比: {vol_ratio:.2f}")
print(f"    放量(>1.2): {'✅' if vol_ratio > 1.2 else '❌'}")

# 6. 阳线 & 影线
body = abs(curr['收盘'] - curr['开盘'])
upper_shadow = curr['最高'] - max(curr['收盘'], curr['开盘'])
shadow_ratio = round(upper_shadow / body, 2) if body > 0 else 0
print(f"\n[6] K线形态:")
print(f"    阳线: {'✅' if curr['收盘'] > curr['开盘'] else '❌'}")
print(f"    影线比: {shadow_ratio} ({'✅ OK' if shadow_ratio < 0.5 else '❌ 过长'})")

# 7. 执行策略判断
print(f"\n[7] 策略判断结果:")
match_pine, stats_pine = check_pine_strategy(df, min_signals=3)
print(f"    Pine策略: {'✅ 通过' if match_pine else '❌ 未通过'}")
print(f"    详情: {stats_pine}")

match_sqz, stats_sqz = check_strategy(df)
print(f"\n    Squeeze策略: {'✅ 通过' if match_sqz else '❌ 未通过'}")
if not match_sqz:
    print(f"    原因: {stats_sqz.get('reason', 'N/A')}")

print(f"\n[8] 最终结论:")
if match_pine:
    print(f"    ⚠️ Pine 策略仍然通过 - 需要进一步检查指标逻辑")
else:
    print(f"    ✅ Pine 策略已正确拒绝此股票")
