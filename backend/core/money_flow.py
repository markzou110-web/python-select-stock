"""
资金流向分析模块

提供个股资金流向数据获取、计算和存储功能
"""

import akshare as ak
import pandas as pd
import time
import random
from datetime import datetime, timedelta
from functools import lru_cache
from .data import get_cached_data, set_cached_data


def get_individual_fund_flow(code: str, days: int = 5) -> pd.DataFrame:
    """
    获取个股最近 N 天的资金流向数据

    Args:
        code: 股票代码（如 '000001'）
        days: 查询天数

    Returns:
        DataFrame with columns:
        - date: 日期
        - main_net_inflow: 主力净流入（万元）
        - super_large_net: 超大单净流入
        - large_net: 大单净流入
        - medium_net: 中单净流入
        - small_net: 小单净流入

        如果获取失败返回空 DataFrame
    """
    cache_key = f'money_flow_{code}_{days}'
    cached = get_cached_data(cache_key, 600)  # 10分钟缓存
    if cached is not None:
        return cached

    max_retries = 2
    for attempt in range(max_retries):
        try:
            # 添加随机延迟避免请求过于集中
            time.sleep(random.uniform(0.3, 0.8))

            # 确定 market 参数（上海/深圳）
            market = 'sz' if code.startswith('0') or code.startswith('3') else 'sh'

            df = ak.stock_individual_fund_flow(stock=code, market=market)

            if df.empty:
                return pd.DataFrame()

            # 数据清洗和重命名
            df = df.head(days)

            # 标准化列名
            column_mapping = {
                '日期': 'date',
                '主力净流入-净额': 'main_net_inflow',
                '超大单净流入-净额': 'super_large_net',
                '大单净流入-净额': 'large_net',
                '中单净流入-净额': 'medium_net',
                '小单净流入-净额': 'small_net'
            }

            # 只保留需要的列
            available_columns = {k: v for k, v in column_mapping.items() if k in df.columns}
            df = df[list(available_columns.keys())].rename(columns=available_columns)

            # 转换日期格式
            df['date'] = pd.to_datetime(df['date'])

            # 确保数值类型
            numeric_columns = ['main_net_inflow', 'super_large_net', 'large_net', 'medium_net', 'small_net']
            for col in numeric_columns:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            # 缓存结果
            set_cached_data(cache_key, df)

            return df

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            print(f"❌ 获取 {code} 资金流数据失败: {e}")
            return pd.DataFrame()

    return pd.DataFrame()


def calculate_money_flow_score(df_flow: pd.DataFrame) -> float:
    """
    计算资金流评分

    评分规则：3日主力净流入每1亿得20分，封顶20分

    Args:
        df_flow: 资金流数据 DataFrame

    Returns:
        float: 评分 (0-20)
    """
    if df_flow.empty or 'main_net_inflow' not in df_flow.columns:
        return 0.0

    # 最近 3 日主力净流入（万元）
    recent_main_flow = df_flow['main_net_inflow'].head(3).sum()

    # 评分：每 1 亿流入得 20 分，封顶 20 分
    score = min(abs(recent_main_flow) / 10000 * 20, 20)

    return round(score, 2)
