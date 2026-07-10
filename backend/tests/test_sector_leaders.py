"""Tests for core/sector_strength.py — build_sector_leaders 多日龙头识别。

验证龙头识别不是简单按"当日涨幅"排序，而是结合多日相对板块强度 + 领涨稳定性：
- 持续跑赢板块的票应排为 LEADER，且 leader_score 高于后排跟风票。
- 名称缺失实时快照时，回退 stock_basic.name。
"""
import os
import sys
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.sector_strength import build_sector_leaders, classify_sector_role, build_sector_strength


def _setup_engine():
    """内存 SQLite，建表。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return engine


def _insert_series(engine, code, name, base_closes):
    """写入一只票最近 N 日的 daily_k + stock_basic。

    base_closes: 最近 N 日（旧→新）的收盘价列表。
    """
    today = date.today()
    n = len(base_closes)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO stock_basic (code, name) VALUES (:code, :name)"
        ), {"code": code, "name": name})
        for i, close in enumerate(base_closes):
            d = today - timedelta(days=n - 1 - i)
            conn.execute(text("""
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                VALUES (:code, :date, :open, :high, :low, :close, :vol)
            """), {
                "code": code, "date": d,
                "open": close, "high": close, "low": close, "close": close, "vol": 100000.0,
            })


def test_leader_ranked_by_multi_day_relative_strength():
    """同板块两只票：A 持续跑赢板块，B 仅今日大涨 → A 应为 LEADER 且得分更高。

    龙头判定不应只看当日涨幅（B 今日涨更多），而应看多日相对强度与领涨稳定性。
    数据为贴近 A 股真实波动的温和上涨（单日 +5% 以内）。
    """
    engine = _setup_engine()
    # A：每日稳定跑赢板块（板块均涨 ~1%，A 每日涨 ~3%）
    a_closes = [10 * (1.03 ** i) for i in range(11)]
    # B：前 10 日横盘，最后一日 +5%（仅今日略强）
    b_closes = [10.0] * 10 + [10.5]
    # C：板块基准，每日涨 ~1%
    c_closes = [10 * (1.01 ** i) for i in range(11)]

    _insert_series(engine, "000001", "龙头A", a_closes)
    _insert_series(engine, "000002", "跟风B", b_closes)
    _insert_series(engine, "000003", "基准C", c_closes)

    sector_map = {"000001": "测试板块", "000002": "测试板块", "000003": "测试板块"}
    leaders = build_sector_leaders(engine, None, sector_map, {}, lookback=10, top_n=3)

    assert "测试板块" in leaders
    ranked = leaders["测试板块"]
    assert len(ranked) == 3

    # A 的 leader_score 应高于 B（多日持续跑赢 + 高领涨稳定性 > 单日略强）
    by_code = {item["code"]: item for item in ranked}
    assert by_code["000001"]["leader_score"] > by_code["000002"]["leader_score"]
    # 名称从 stock_basic 回退
    assert by_code["000001"]["name"] == "龙头A"
    # 相对强度字段存在
    assert "relative_strength_5d" in by_code["000001"]
    assert "lead_consistency" in by_code["000001"]


def test_empty_inputs_return_empty():
    """engine 或 sector_map 为空 → 返回空 dict（防御）。"""
    assert build_sector_leaders(None, None, {}, {}) == {}
    engine = _setup_engine()
    assert build_sector_leaders(engine, None, {}, {}) == {}


def test_respects_top_n_limit():
    """top_n 限制每个板块返回的龙头数量。"""
    engine = _setup_engine()
    sector_map = {}
    for i in range(8):
        code = f"00000{i}"
        closes = [10 + i * 0.1 + j * 0.05 for j in range(11)]
        _insert_series(engine, code, f"票{i}", closes)
        sector_map[code] = "板块X"

    leaders = build_sector_leaders(engine, None, sector_map, {}, lookback=10, top_n=3)
    assert len(leaders["板块X"]) == 3


# ---------------------------------------------------------------------------
# 改动 P0：classify_sector_role LEADER 判定加 rank 约束
# ---------------------------------------------------------------------------

def test_leader_requires_top5_rank_when_relative_high():
    """P0：超额≥3% 但板块排名>5 → 不是 LEADER（应为 CORE）。

    回归 601138 误判：rank=11, relative=6.54%, pct=7.49% 被错判 LEADER。
    修复后超额≥3% 需配合 rank<=5，否则降级 CORE。
    """
    # 601138 场景：超额 6.54% 但排名 11
    role = classify_sector_role(stock_pct=7.49, sector_avg_pct=0.95, rank_in_sector=11)
    assert role == "CORE", f"rank=11 超额6.54% 应为 CORE，实际 {role}"

    # 板块前 5 + 超额 6% → LEADER（排名支撑）
    role2 = classify_sector_role(stock_pct=7.49, sector_avg_pct=0.95, rank_in_sector=3)
    assert role2 == "LEADER", f"rank=3 超额6.54% 应为 LEADER，实际 {role2}"


def test_leader_absolute_strength_ignores_rank():
    """P0：涨停级涨幅(pct≥9%)是绝对强势，不受 rank 约束 → 仍判 LEADER。

    即使排名靠后，单日涨幅≥9%（近涨停）说明绝对强势，应判龙头。
    """
    role = classify_sector_role(stock_pct=9.5, sector_avg_pct=0.5, rank_in_sector=15)
    assert role == "LEADER", f"pct=9.5% 涨停级应判 LEADER，实际 {role}"


def test_leader_top2_with_5pct_still_works():
    """P0：板块前2且涨≥5% → LEADER（原逻辑保留，不受影响）。"""
    role = classify_sector_role(stock_pct=6.0, sector_avg_pct=1.0, rank_in_sector=2)
    assert role == "LEADER"


def test_high_relative_low_rank_falls_to_core():
    """P0：超额高但排名差 + 涨幅未达涨停 → CORE（有资格但非龙头）。"""
    # rank=8（前5外），超额 4%（>3%），涨幅 5%（<9%）
    role = classify_sector_role(stock_pct=5.0, sector_avg_pct=1.0, rank_in_sector=8)
    assert role == "CORE", f"rank=8 应为 CORE，实际 {role}"


# ---------------------------------------------------------------------------
# 改动 #2：板块均值成交额加权（过滤低活跃股）
# ---------------------------------------------------------------------------

def test_sector_avg_weighted_by_amount_filters_inactive_stocks():
    """#2：僵尸股（低换手）不应拉低板块均值。

    场景：板块有 2 只活跃股（涨5%、amount大）+ 8 只僵尸股（跌2%、amount极小）。
    简单均值会被僵尸股拉成负数；加权后活跃股主导，板块均值为正。
    """
    import pandas as pd
    active = pd.DataFrame({
        'code': ['000001', '000002'],
        'pct_chg': [5.0, 5.0],
        'turnover': [8.0, 6.0],
        'amount': [1e9, 1e9],  # 10亿成交额，活跃
    })
    inactive = pd.DataFrame({
        'code': [f'00000{i}' for i in range(3, 11)],
        'pct_chg': [-2.0] * 8,
        'turnover': [0.05] * 8,  # 换手极低，僵尸股
        'amount': [1e4] * 8,     # 1万元成交额，几乎无交易
    })
    snap = pd.concat([active, inactive], ignore_index=True)
    sector_map = {**{c: '测试板块' for c in active['code']},
                  **{c: '测试板块' for c in inactive['code']}}

    strength = build_sector_strength(snap, sector_map, {})
    sec = strength.get('测试板块', {})
    avg = sec.get('sector_avg_pct', 0)

    # 简单均值 = (5*2 + (-2)*8)/10 = -0.6%（被僵尸股拉负）
    # 加权均值 ≈ 5%（活跃股主导）
    assert avg > 3.0, f"加权后板块均值应反映活跃股(>3%)，实际 {avg}（僵尸股拉低了基准）"


def test_sector_avg_falls_back_to_simple_when_no_amount():
    """#2：无 amount 列时用 turnover 加权；都缺时退回简单均值（不崩溃）。"""
    import pandas as pd
    snap = pd.DataFrame({
        'code': ['000001', '000002', '000003'],
        'pct_chg': [3.0, 1.0, 2.0],
        'turnover': [5.0, 1.0, 3.0],  # 无 amount 列
    })
    sector_map = {c: '测试板块' for c in snap['code']}
    strength = build_sector_strength(snap, sector_map, {})
    sec = strength.get('测试板块', {})
    # turnover 加权: (3*5 + 1*1 + 2*3)/(5+1+3) = 22/9 ≈ 2.44
    assert 2.0 < sec['sector_avg_pct'] < 3.0, f"turnover 加权均值应≈2.44，实际 {sec['sector_avg_pct']}"
