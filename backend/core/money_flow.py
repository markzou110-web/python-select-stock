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
from .db import get_db_engine
from sqlalchemy import text


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


def save_money_flow_to_db(df_flow: pd.DataFrame, code: str, engine):
    """
    将资金流数据保存到数据库

    使用 UPSERT 机制：如果记录已存在则更新，否则插入

    Args:
        df_flow: 资金流数据 DataFrame
        code: 股票代码
        engine: 数据库引擎
    """
    if df_flow.empty:
        return

    try:
        with engine.connect() as conn:
            for _, row in df_flow.iterrows():
                conn.execute(text("""
                    INSERT INTO money_flow_daily
                    (code, date, main_net_inflow, super_large_net, large_net, medium_net, small_net)
                    VALUES (:code, :date, :main, :super, :large, :medium, :small)
                    ON CONFLICT (code, date) DO UPDATE SET
                        main_net_inflow = EXCLUDED.main_net_inflow,
                        super_large_net = EXCLUDED.super_large_net,
                        large_net = EXCLUDED.large_net,
                        medium_net = EXCLUDED.medium_net,
                        small_net = EXCLUDED.small_net
                """), {
                    'code': code,
                    'date': row['date'].date() if hasattr(row['date'], 'date') else pd.to_datetime(row['date']).date(),
                    'main': float(row.get('main_net_inflow', 0)),
                    'super': float(row.get('super_large_net', 0)),
                    'large': float(row.get('large_net', 0)),
                    'medium': float(row.get('medium_net', 0)),
                    'small': float(row.get('small_net', 0))
                })
            conn.commit()
            print(f"✅ {code} 资金流数据已保存到数据库 ({len(df_flow)} 条记录)")
    except Exception as e:
        print(f"❌ 保存 {code} 资金流数据失败: {e}")
        raise


def sync_stock_money_flow(code: str, engine=None) -> bool:
    """
    同步单只股票的资金流数据到数据库

    Args:
        code: 股票代码
        engine: 数据库引擎（可选，默认使用全局引擎）

    Returns:
        bool: 是否成功
    """
    if engine is None:
        engine = get_db_engine()
    if not engine:
        print("❌ 无法获取数据库引擎")
        return False

    try:
        # 获取最新资金流日期
        with engine.connect() as conn:
            result = conn.execute(
                text(f"SELECT MAX(date) FROM money_flow_daily WHERE code='{code}'")
            )
            last_date = result.fetchone()[0]

        # 确定需要同步的日期范围
        if last_date:
            fetch_start = (last_date + timedelta(days=1)).strftime("%Y%m%d")
            days_to_sync = (datetime.now().date() - last_date).days
        else:
            fetch_start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
            days_to_sync = 90

        today_str = datetime.now().strftime("%Y%m%d")
        if last_date and last_date.strftime("%Y%m%d") >= today_str:
            # 已经是最新数据
            return True

        # 调用获取资金流数据
        df_flow = get_individual_fund_flow(code, days=days_to_sync)

        if df_flow.empty:
            print(f"⚠️ {code} 无资金流数据")
            return True  # 无数据不算失败

        # 保存到数据库
        save_money_flow_to_db(df_flow, code, engine)
        return True

    except Exception as e:
        print(f"❌ 同步 {code} 资金流数据失败: {e}")
        return False


def ensure_money_flow_available(code: str, days: int = 5) -> bool:
    """
    确保指定股票的资金流数据可用

    如果数据缺失或过期（> 24 小时），触发快速同步（最近 N 天）

    Args:
        code: 股票代码
        days: 同步天数

    Returns:
        bool: 是否数据可用
    """
    engine = get_db_engine()
    if not engine:
        return False

    try:
        # 检查数据库中最新的资金流数据日期
        with engine.connect() as conn:
            result = conn.execute(text(
                f"SELECT MAX(date) FROM money_flow_daily WHERE code='{code}'"
            ))
            last_date = result.fetchone()[0]

        # 判断是否需要同步
        need_sync = False
        if last_date is None:
            # 无历史数据，需要同步
            need_sync = True
            print(f"🔄 {code} 无资金流历史数据")
        else:
            days_diff = (datetime.now().date() - last_date).days
            if days_diff > 1:  # 超过 1 天未更新
                need_sync = True
                print(f"🔄 {code} 资金流数据过期 ({days_diff} 天前)")

        if need_sync:
            sync_stock_money_flow(code, engine)

        return True

    except Exception as e:
        print(f"❌ 检查 {code} 资金流数据状态失败: {e}")
        return False
