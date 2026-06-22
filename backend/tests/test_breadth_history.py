"""Tests for breadth_history 表 + 双写双读机制。

回归 BUG（6/22 节后首日）：盘中扫描时，板块/市场层面的历史数据直接读 daily_k 表，
但节后首日 daily_k 还是上个交易日（6/18）的数据，导致：
  - 证券板块 sector_trend_slope 用 6/18 的 -3.02% 算出 -4.85 → 误判 SECTOR_FADE
  - 市场宽度 cycle_history 缺今日 → 情绪误判 RETREAT
即使 6/22 证券板块实际暴涨 6%（44 只中 43 只上涨），中信建投交易分 99.71 仍被
"板块退潮"blocker 拦截。

修复：新建 breadth_history 表，每次扫描后写入今日的 MARKET + SECTOR 实时聚合宽度；
build_sector_history_context / load_market_cycle_history 优先读新表，新表空时回退 daily_k。
"""
import os
import sys
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.db import init_db, record_breadth_snapshot


def _make_engine_with_breadth():
    """内存 SQLite + 全表建表（含 breadth_history）。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    # init_db 内的 CREATE TABLE IF NOT EXISTS breadth_history（dialect 分支）
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS breadth_history (
                bar_date DATE NOT NULL,
                scope VARCHAR(10) NOT NULL,
                industry VARCHAR(50),
                advance_ratio FLOAT,
                strong_ratio FLOAT,
                weak_ratio FLOAT,
                avg_return FLOAT,
                limit_up_ratio FLOAT,
                total_count INTEGER,
                updated_at TIMESTAMP,
                UNIQUE(bar_date, scope, industry)
            )
        """))
        conn.commit()
    return engine


def _sample_snapshot():
    """构造 3 只证券 + 2 只半导体的实时快照（模拟 6/22 证券暴涨）。"""
    return pd.DataFrame([
        {"code": "601066", "name": "中信建投", "price": 27.39, "pct_chg": 10.0, "industry": "证券"},
        {"code": "600030", "name": "中信证券", "price": 28.64, "pct_chg": 7.83, "industry": "证券"},
        {"code": "000776", "name": "广发证券", "price": 22.70, "pct_chg": 9.98, "industry": "证券"},
        {"code": "600460", "name": "士兰微", "price": 44.02, "price_open": 40.0, "pct_chg": 4.99, "industry": "半导体"},
        {"code": "300244", "name": "迪安诊断", "price": 19.59, "pct_chg": -1.01, "industry": "半导体"},
    ])


def test_record_breadth_snapshot_upserts_market_and_sectors():
    """record_breadth_snapshot 应写入 1 行 MARKET + N 行 SECTOR，且 upsert 幂等。"""
    engine = _make_engine_with_breadth()
    sector_map = {"601066": "证券", "600030": "证券", "000776": "证券",
                  "600460": "半导体", "300244": "半导体"}
    today = date.today().isoformat()

    # 第一次写入
    record_breadth_snapshot(_sample_snapshot(), sector_map, engine, bar_date=today)

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT scope, industry, advance_ratio, strong_ratio, avg_return, total_count "
            "FROM breadth_history WHERE bar_date = :d ORDER BY scope, industry"
        ), {"d": today}).fetchall()

    scopes = {r[0] for r in rows}
    assert "MARKET" in scopes
    assert "SECTOR" in scopes
    # 3 个证券全涨（advance=100%），半导体 1 涨 1 跌（advance=50%）
    sec_rows = {r[1]: r for r in rows if r[0] == "SECTOR"}
    assert sec_rows["证券"][2] == 100.0  # advance_ratio
    assert sec_rows["证券"][4] > 9.0     # avg_return 接近 9.27
    assert sec_rows["半导体"][2] == 50.0

    # 第二次写入（upsert，不应产生重复行）
    record_breadth_snapshot(_sample_snapshot(), sector_map, engine, bar_date=today)
    with engine.connect() as conn:
        cnt = conn.execute(text(
            "SELECT COUNT(*) FROM breadth_history WHERE bar_date = :d"
        ), {"d": today}).scalar()
    assert cnt == 3  # 1 MARKET + 2 SECTOR，upsert 不新增


def test_record_breadth_snapshot_failure_does_not_block_scan():
    """写入失败时只 log 不抛，绝不阻断扫描主流程。"""
    engine = _make_engine_with_breadth()
    # 传一个不存在的 engine 方法触发异常
    bad_engine = None
    # 不应抛异常
    record_breadth_snapshot(_sample_snapshot(), {}, bad_engine, bar_date=date.today().isoformat())
    # 到这里没崩就说明容灾生效


def test_build_sector_history_context_prefers_breadth_table(monkeypatch):
    """build_sector_history_context 应优先用 breadth_history 今日行，
    使 sector_trend_slope 含今日实时数据（而非滞后的 daily_k）。
    """
    from core import sector_strength

    engine = _make_engine_with_breadth()
    today = date.today().isoformat()

    # 构造 breadth_history：证券板块今日暴涨（advance=97.7%）
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO breadth_history
                (bar_date, scope, industry, advance_ratio, strong_ratio, weak_ratio, avg_return, total_count)
            VALUES
                (:d, 'SECTOR', '证券', 97.7, 84.1, 0.0, 6.08, 44),
                (:d, 'SECTOR', '半导体', 50.0, 10.0, 0.0, 1.5, 2)
        """), {"d": today})
        # 构造滞后的 daily_k：证券前4日（模拟节前退潮）
        for d_str, pct in [("2026-06-12", 3.88), ("2026-06-15", 2.82),
                            ("2026-06-16", 0.91), ("2026-06-17", -0.29)]:
            close = 20.0 * (1 + pct / 100)
            conn.execute(text(
                "INSERT INTO daily_k (code, date, close) VALUES ('601066', :d, :c)"
            ), {"d": d_str, "c": close})
        conn.commit()

    sector_map = {"601066": "证券"}
    history = sector_strength.build_sector_history_context(engine, sector_map)

    # 证券板块应含今日 breadth 数据
    assert "证券" in history
    sec = history["证券"]
    # 关键断言：sector_trend_slope 应反映今日暴涨（含 6.08%），而非滞后的 -4.85
    # 今日 avg=6.08，前4日均值≈(3.88+2.82+0.91-0.29)/4=1.83，slope=6.08-1.83=4.25（正数）
    assert sec["sector_trend_slope"] > 0, \
        f"slope 应为正（今日暴涨），实际 {sec['sector_trend_slope']}（若为负说明仍在用滞后数据）"


def test_load_market_cycle_history_prefers_breadth_table():
    """load_market_cycle_history 应优先用 breadth_history MARKET 行。"""
    from core import decision_layer

    engine = _make_engine_with_breadth()
    today = date.today().isoformat()

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO breadth_history
                (bar_date, scope, industry, advance_ratio, strong_ratio, weak_ratio, avg_return, total_count)
            VALUES
                (:d, 'MARKET', NULL, 52.8, 11.0, 4.2, 0.63, 5594)
        """), {"d": today})
        conn.commit()

    cycle = decision_layer.load_market_cycle_history(engine)

    # cycle_history 应含今日的 MARKET 宽度
    assert len(cycle) >= 1
    last = cycle[-1]
    assert last["advance_ratio"] == 52.8
    assert last["strong_ratio"] == 11.0


def test_fallback_to_daily_k_when_breadth_empty():
    """breadth_history 表为空时，两个读函数都应回退到 daily_k 逻辑，不崩。"""
    from core import sector_strength, decision_layer

    engine = _make_engine_with_breadth()
    # 不写入 breadth_history，只写 daily_k
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO daily_k (code, date, close) VALUES ('601066', '2026-06-18', 24.79)"
        ))
        conn.execute(text(
            "INSERT INTO daily_k (code, date, close) VALUES ('601066', '2026-06-17', 25.0)"
        ))
        conn.commit()

    # 不应抛异常
    sector_history = sector_strength.build_sector_history_context(engine, {"601066": "证券"})
    cycle = decision_layer.load_market_cycle_history(engine)
    assert isinstance(sector_history, dict)
    assert isinstance(cycle, list)
