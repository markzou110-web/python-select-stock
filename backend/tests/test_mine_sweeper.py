"""Tests for routers/market.py fetch_mine_sweeper_data — 地雷监测数据源准确性。

回归 BUG：原 reductions 分支用 ak.stock_dzjy_mrtj()（大宗交易每日统计）当"减持"数据源，
但大宗交易 ≠ 减持（机构调仓/引入战投/约定购回都走大宗），且实测返回 2022-01-05 陈旧数据，
导致 601138 工业富联因 4 年前一笔小额大宗交易被误判为"地雷预警"→ D 级。
修复：移除 reductions 分支，reductions 恒为空列表（保留 key 维持接口契约）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd
from datetime import datetime, timedelta

import routers.market as market_mod


def _stub_ak(monkeypatch, earnings_codes=None, unlock_codes=None, block_codes=None):
    """用真实 pd.DataFrame stub akshare 三个接口，避免网络调用。"""
    class _FakeAK:
        @staticmethod
        def stock_report_disclosure(market, period):
            if earnings_codes is None:
                return pd.DataFrame()
            future_date = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
            return pd.DataFrame({"股票代码": earnings_codes, "首次预约": [future_date] * len(earnings_codes)})

        @staticmethod
        def stock_restricted_release_detail_em(start_date, end_date):
            if unlock_codes is None:
                return pd.DataFrame()
            return pd.DataFrame({"股票代码": unlock_codes})

        @staticmethod
        def stock_dzjy_mrtj():
            # 即使返回数据，也不应再被 reductions 分支使用
            if block_codes is None:
                return pd.DataFrame()
            return pd.DataFrame({"证券代码": block_codes})

    monkeypatch.setattr(market_mod, "ak", _FakeAK)
    # 清缓存强制重新拉取
    monkeypatch.setattr(market_mod, "_mine_sweeper_cache", {"data": None, "timestamp": 0})


def test_reductions_always_empty_even_when_block_trades_returned(monkeypatch):
    """关键回归：stock_dzjy_mrtj 返回了 601138，但 reductions 必须为空（不再用大宗交易误判减持）。"""
    _stub_ak(
        monkeypatch,
        earnings_codes=["000001"],
        unlock_codes=["000002"],
        block_codes=["601138", "000003"],  # 大宗交易接口返回了 601138
    )

    data = market_mod.fetch_mine_sweeper_data()

    assert data["earnings"] == ["000001"]
    assert data["unlocks"] == ["000002"]
    # 核心断言：reductions 必须为空，即使大宗交易接口返回了数据
    assert data["reductions"] == [], (
        f"reductions 应恒为空（不再用大宗交易误判减持），实际 {data['reductions']}"
    )
    assert "601138" not in data["reductions"], "601138 不应因大宗交易被判减持"


def test_reductions_key_preserved_for_contract(monkeypatch):
    """接口契约：reductions key 必须保留（调用方仍可读 data['reductions']）。"""
    _stub_ak(monkeypatch)  # 所有接口返回空

    data = market_mod.fetch_mine_sweeper_data()
    assert set(data.keys()) == {"earnings", "unlocks", "reductions"}
    assert data["earnings"] == []
    assert data["unlocks"] == []
    assert data["reductions"] == []


def test_earnings_and_unlocks_still_work(monkeypatch):
    """移除 reductions 后，earnings 和 unlocks 应正常工作（不受影响）。"""
    _stub_ak(
        monkeypatch,
        earnings_codes=["600000", "000001"],
        unlock_codes=["300001"],
    )

    data = market_mod.fetch_mine_sweeper_data()
    assert data["earnings"] == ["600000", "000001"]
    assert data["unlocks"] == ["300001"]
