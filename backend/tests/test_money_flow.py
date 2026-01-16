"""
资金流模块单元测试
"""

import pytest
import pandas as pd
import sys
sys.path.insert(0, '.')

from core.money_flow import (
    get_individual_fund_flow,
    calculate_money_flow_score,
    save_money_flow_to_db
)
from core.db import get_db_engine
from sqlalchemy import text


def test_calculate_money_flow_score_empty_dataframe():
    """测试空 DataFrame 的评分计算"""
    df = pd.DataFrame()
    score = calculate_money_flow_score(df)
    assert score == 0.0


def test_calculate_money_flow_score_positive_flow():
    """测试正向资金流的评分计算"""
    df = pd.DataFrame({
        'main_net_inflow': [5000, 3000, 2000]  # 总计 1 亿
    })
    score = calculate_money_flow_score(df)
    assert score == 20.0  # 1 亿 = 20 分（封顶）


def test_calculate_money_flow_score_negative_flow():
    """测试负向资金流的评分计算"""
    df = pd.DataFrame({
        'main_net_inflow': [-5000, -3000, -2000]  # 总计 -1 亿
    })
    score = calculate_money_flow_score(df)
    assert score == 20.0  # 绝对值 1 亿 = 20 分


def test_calculate_money_flow_score_partial():
    """测试部分流入的评分计算"""
    df = pd.DataFrame({
        'main_net_inflow': [3000, 2000, 1000]  # 总计 6000 万
    })
    score = calculate_money_flow_score(df)
    assert score == 12.0  # 6000 万 / 10000 * 20 = 12 分


@pytest.mark.integration
def test_save_money_flow_to_db():
    """测试数据库保存功能（集成测试）"""
    engine = get_db_engine()
    if not engine:
        pytest.skip("无法连接数据库")

    # 准备测试数据
    df = pd.DataFrame({
        'date': [pd.Timestamp('2025-01-15')],
        'main_net_inflow': [5000.0],
        'super_large_net': [3000.0],
        'large_net': [2000.0],
        'medium_net': [-1000.0],
        'small_net': [-500.0]
    })

    # 保存到数据库
    save_money_flow_to_db(df, '999999', engine)

    # 验证数据
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT main_net_inflow FROM money_flow_daily
            WHERE code='999999' AND date='2025-01-15'
        """))
        row = result.fetchone()
        assert row is not None
        assert row[0] == 5000.0

    # 清理测试数据
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM money_flow_daily WHERE code='999999'"))
        conn.commit()


@pytest.mark.external
def test_get_individual_fund_flow_real():
    """测试真实数据获取（外部 API 测试）"""
    df = get_individual_fund_flow('000001', days=3)

    # 验证返回结构
    assert isinstance(df, pd.DataFrame)
    if not df.empty:
        assert 'date' in df.columns
        assert 'main_net_inflow' in df.columns
        assert len(df) <= 3


if __name__ == "__main__":
    # 运行单元测试
    test_calculate_money_flow_score_empty_dataframe()
    test_calculate_money_flow_score_positive_flow()
    test_calculate_money_flow_score_negative_flow()
    test_calculate_money_flow_score_partial()
    print("✅ 单元测试通过")
