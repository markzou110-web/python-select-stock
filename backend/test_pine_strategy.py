#!/usr/bin/env python3
"""
测试 Pine Script 策略实现
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 模拟测试数据
def generate_test_data(days=100):
    """生成模拟股票数据"""
    dates = [datetime.now() - timedelta(days=i) for i in range(days, 0, -1)]

    # 生成模拟价格数据 (带趋势)
    trend = np.linspace(100, 120, days)
    noise = np.random.normal(0, 1, days)
    close = trend + noise

    data = {
        '日期': dates,
        '开盘': close * (1 + np.random.uniform(-0.02, 0, days)),
        '最高': close * (1 + np.random.uniform(0, 0.03, days)),
        '最低': close * (1 + np.random.uniform(-0.03, 0, days)),
        '收盘': close,
        '成交量': np.random.randint(1000000, 10000000, days)
    }
    return pd.DataFrame(data)


def test_pine_indicators():
    """测试 Pine Script 指标计算"""
    print("=" * 50)
    print("测试 Pine Script 指标计算")
    print("=" * 50)

    # 模拟导入
    import sys
    sys.path.insert(0, '/Users/liangzou/Desktop/AI_Tools/python-select-stock/backend')

    from core.indicators import calculate_pine_indicators

    # 生成测试数据
    df = generate_test_data(100)
    print(f"原始数据: {len(df)} 行")

    # 计算基础指标
    df['EMA5'] = df['收盘'].ewm(span=5, adjust=False).mean()
    df['EMA10'] = df['收盘'].ewm(span=10, adjust=False).mean()
    df['EMA20'] = df['收盘'].ewm(span=20, adjust=False).mean()
    df['EMA60'] = df['收盘'].ewm(span=60, adjust=False).mean()
    df['RSI'] = 50 + np.random.randint(-10, 30, len(df))
    df['MACD_DIF'] = np.random.uniform(-0.5, 0.5, len(df))
    df['MACD_DEA'] = np.random.uniform(-0.5, 0.5, len(df))
    df['BB_Mid'] = df['收盘'].rolling(window=20).mean()
    std = df['收盘'].rolling(window=20).std()
    df['BB_Upper'] = df['BB_Mid'] + 2 * std
    df['BB_Lower'] = df['BB_Mid'] - 2 * std
    df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid'].replace(0, np.nan)
    df['Vol_MA20'] = df['成交量'].rolling(window=20).mean()

    # 计算 Pine 指标
    df = calculate_pine_indicators(df)

    # 验证结果
    required_columns = [
        'RF_Filter', 'RF_Upward', 'RF_Downward',
        'ST_Basic_Upper', 'ST_Basic_Lower', 'ST_Trend', 'ST_Signal',
        'RQK_Value', 'RQK_Up', 'RQK_Down',
        'HT_High', 'HT_Low', 'HT_Long', 'HT_Short',
        'QQE_LongBand', 'QQE_ShortBand', 'QQE_Long', 'QQE_Short'
    ]

    print("\n检查生成的 Pine 指标列:")
    for col in required_columns:
        exists = col in df.columns
        print(f"  {col}: {'✓' if exists else '✗'}")
        if not exists:
            print(f"    错误: 缺少列 {col}")
            return False

    print("\n数据样本 (最新 5 行):")
    print(df[['日期', '收盘', 'RF_Upward', 'ST_Signal', 'HT_Long', 'QQE_Long']].tail())

    return True


def test_pine_strategy():
    """测试 Pine Script 策略检查"""
    print("\n" + "=" * 50)
    print("测试 Pine Script 策略检查")
    print("=" * 50)

    import sys
    sys.path.insert(0, '/Users/liangzou/Desktop/AI_Tools/python-select-stock/backend')

    from core.strategy import check_pine_strategy

    # 生成测试数据并计算指标
    df = generate_test_data(100)

    # 手动构造测试信号 (确保有共振)
    # 让最新 5 行的所有指标都看涨
    df['RF_Upward'] = False
    df['RF_Downward'] = False
    df['ST_Signal'] = False
    df['RQK_Up'] = False
    df['RQK_Down'] = False
    df['HT_Long'] = False
    df['HT_Short'] = False
    df['QQE_Long'] = False
    df['QQE_Short'] = False

    # 设置最新行为看涨
    df.loc[df.index[-1], 'RF_Upward'] = True
    df.loc[df.index[-1], 'ST_Signal'] = True
    df.loc[df.index[-1], 'RQK_Up'] = True
    df.loc[df.index[-1], 'HT_Long'] = True
    df.loc[df.index[-1], 'QQE_Long'] = True
    df.loc[df.index[-1], 'code'] = '600000'
    df.loc[df.index[-1], 'name'] = '测试股票'

    # 测试策略检查
    match, result = check_pine_strategy(df, min_signals=3)

    print(f"\n策略匹配结果: {match}")
    print(f"返回结果:")
    for key, value in result.items():
        print(f"  {key}: {value}")

    if match:
        print("\n✓ Pine Script 策略测试通过!")
        return True
    else:
        print(f"\n✗ Pine Script 策略测试失败: {result.get('reason', 'Unknown')}")
        return False


if __name__ == "__main__":
    try:
        # 测试 1: 指标计算
        indicators_ok = test_pine_indicators()

        # 测试 2: 策略检查
        strategy_ok = test_pine_strategy()

        if indicators_ok and strategy_ok:
            print("\n" + "=" * 50)
            print("所有测试通过! ✓")
            print("=" * 50)
        else:
            print("\n" + "=" * 50)
            print("部分测试失败! ✗")
            print("=" * 50)
    except Exception as e:
        print(f"\n测试出错: {e}")
        import traceback
        traceback.print_exc()
