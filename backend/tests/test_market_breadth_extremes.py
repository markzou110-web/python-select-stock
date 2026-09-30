"""Elder NH-NL 市场宽度指标测试。

compute_market_breadth_extremes：全市场 250 日新高/新低家数 + 站上 MA50 占比；
record_breadth_extremes：upsert 进 breadth_history 当日 MARKET 行（与盘中
record_breadth_snapshot 的聚合列互不覆盖）。
"""
import os
import sys
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.db import init_db, record_breadth_extremes, record_breadth_snapshot
from core.market_regime import compute_market_breadth_extremes
from core.models import Base, DailyK

DAYS = 260


def _series(code, start, end, *, start_price=10.0, end_price=20.0):
    """生成 [start, end] 每日一根的日线；价格在 start_price→end_price 间线性过渡。"""
    dates = pd.bdate_range(start, end)
    n = len(dates)
    prices = pd.Series(pd.RangeIndex(n)).mul((end_price - start_price) / max(n - 1, 1)).add(start_price)
    return [
        {
            "code": code, "date": d.date(), "open": float(p) * 0.999,
            "high": float(p) * 1.005, "low": float(p) * 0.995,
            "close": float(p), "vol": 100000.0,
        }
        for d, p in zip(dates, prices)
    ]


def _engine_with_daily_k(rows):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)  # 走真实迁移路径，验证 breadth_history 新列
    session = engine.raw_connection()
    cursor = session.cursor()
    for row in rows:
        cursor.execute(
            "INSERT INTO daily_k (code, date, open, high, low, close, vol) VALUES (?,?,?,?,?,?,?)",
            (row["code"], row["date"].isoformat(), row["open"], row["high"], row["low"], row["close"], row["vol"]),
        )
    session.commit()
    cursor.close()
    session.close()
    return engine


def _end_day() -> date:
    # 取一个固定的周五作为数据终点（bdate_range 生成工作日）
    return date(2026, 9, 25)


def test_migration_adds_nh_nl_columns_to_breadth_history():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(breadth_history)"))}
    assert {"nh_count", "nl_count", "pct_above_ma50"} <= columns


def test_compute_breadth_counts_new_highs_new_lows_and_ma50_share():
    end = _end_day()
    start = end - timedelta(days=DAYS * 2)
    rows = (
        _series("600001", start, end, start_price=10.0, end_price=20.0)   # 上升趋势 → 末日新高、MA50 上方
        + _series("600002", start, end, start_price=20.0, end_price=10.0)  # 下降趋势 → 末日新低、MA50 下方
        + _series("600003", end - timedelta(days=30), end, start_price=5.0, end_price=6.0)  # 次新股，不足200日
    )
    engine = _engine_with_daily_k(rows)
    stats = compute_market_breadth_extremes(engine)
    assert stats["total_count"] == 3
    assert stats["nh_count"] == 1
    assert stats["nl_count"] == 1
    # 次新股（<50日）不计入 MA50 占比：600001 上方、600002 下方 → 50%
    assert stats["pct_above_ma50"] == 50.0
    assert stats["bar_date"] == end.isoformat()


def test_short_history_stocks_are_not_counted_as_extremes():
    """全市场都只有 60 天历史时：无新高/新低（min_periods=200 不满足），MA50 占比有效。"""
    end = _end_day()
    start = end - timedelta(days=90)
    rows = _series("600010", start, end, start_price=10.0, end_price=11.0)
    engine = _engine_with_daily_k(rows)
    stats = compute_market_breadth_extremes(engine)
    assert stats["total_count"] == 1
    assert stats["nh_count"] == 0
    assert stats["nl_count"] == 0
    assert stats["pct_above_ma50"] == 100.0


def test_record_breadth_extremes_upserts_and_preserves_intraday_row():
    end = _end_day()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)

    # 1) 空表直插：写入 MARKET 行
    stats = {"bar_date": end.isoformat(), "nh_count": 12, "nl_count": 3, "pct_above_ma50": 44.4, "total_count": 5000}
    assert record_breadth_extremes(stats, engine) is True
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT nh_count, nl_count, pct_above_ma50 FROM breadth_history WHERE bar_date = :d AND scope = 'MARKET'"
        ), {"d": end.isoformat()}).fetchone()
    assert row == (12, 3, 44.4)

    # 2) 盘中快照行已存在时：只更新 NH-NL 三列，不覆盖盘中聚合列
    snapshot = pd.DataFrame([
        {"code": "601066", "pct_chg": 5.0, "industry": "证券"},
        {"code": "600030", "pct_chg": -2.0, "industry": "证券"},
    ])
    record_breadth_snapshot(snapshot, {}, engine, bar_date=end.isoformat())
    stats2 = {"bar_date": end.isoformat(), "nh_count": 15, "nl_count": 8, "pct_above_ma50": 40.0, "total_count": 5000}
    assert record_breadth_extremes(stats2, engine) is True
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT nh_count, nl_count, pct_above_ma50, advance_ratio FROM breadth_history WHERE bar_date = :d AND scope = 'MARKET'"
        ), {"d": end.isoformat()}).fetchone()
    assert row == (15, 8, 40.0, 50.0)


def test_record_breadth_extremes_rejects_empty_stats():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)
    assert record_breadth_extremes({"total_count": 0, "bar_date": "2026-09-25"}, engine) is False
    assert record_breadth_extremes(None, engine) is False


def test_latest_breadth_line_formats_premarket_push():
    from core.tasks import _latest_breadth_line

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)
    # 无数据 → None
    assert _latest_breadth_line(engine) is None

    # 盘中快照先写入（nh_count 为 NULL）→ 不推空数据
    record_breadth_snapshot(
        pd.DataFrame([{"code": "601066", "pct_chg": 5.0, "industry": "证券"}]),
        {}, engine, bar_date="2026-09-25",
    )
    assert _latest_breadth_line(engine) is None

    stats = {"bar_date": "2026-09-25", "nh_count": 12, "nl_count": 3, "pct_above_ma50": 44.4, "total_count": 5000}
    assert record_breadth_extremes(stats, engine) is True
    assert _latest_breadth_line(engine) == "Elder宽度(09-25): 52周新高12家/新低3家 MA50上占比44%"

    # 真实场景：之后出现更新的盘中快照行（尚无 NH-NL）→ 不得遮挡最近的有效宽度行
    record_breadth_snapshot(
        pd.DataFrame([{"code": "601066", "pct_chg": 2.0, "industry": "证券"}]),
        {}, engine, bar_date="2026-09-26",
    )
    assert _latest_breadth_line(engine) == "Elder宽度(09-25): 52周新高12家/新低3家 MA50上占比44%"
