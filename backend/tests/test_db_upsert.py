"""Tests for core/db.py save_to_db — 验证 ON CONFLICT DO UPDATE 修复复权缺口。

回归 BUG：原 save_to_db 用 ON CONFLICT DO NOTHING，除权后新的前复权数据无法覆盖
旧记录，导致 daily_k 里除权日之前的历史价仍是旧价，形成假缺口（如 688256 从 ¥1868
跌到 ¥1182 的 -36% 假跳水），污染信号/回测胜率。修复为 DO UPDATE 后，同步会用最新的
前复权数据覆盖旧记录，除权缺口被自动修正。

注意：save_to_db 内部用 CAST(:date AS DATE) 这是 PostgreSQL 专有语法，SQLite 把
'YYYY-MM-DD' 字符串当算术表达式（2026-6-20=2000）导致测试失真。因此本测试用两层策略：
1) 文本层：断言 SQL 含 DO UPDATE（确认修复部署，PostgreSQL 生产路径生效）。
2) 行为层：用 SQLite 友好的原生 INSERT 绕过 CAST，验证 DO UPDATE 的 upsert 语义。
"""
import os
import sys
import inspect

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.db import save_to_db


def test_save_to_db_uses_do_update_not_do_nothing():
    """核心回归：save_to_db 源码必须用 ON CONFLICT DO UPDATE（而非 DO NOTHING）。

    这是从根上修复"除权后历史价不更新"的合同。PostgreSQL 生产路径每次同步都会用
    最新的前复权数据覆盖旧记录，除权缺口被自动修正。
    """
    src = inspect.getsource(save_to_db)
    assert "DO UPDATE" in src, "save_to_db 必须用 DO UPDATE 覆盖旧复权数据"
    assert "DO NOTHING" not in src, "save_to_db 不应再用 DO NOTHING（会保留旧未复权价）"
    # 必须覆盖全部 OHLCV 列
    for col in ("open", "high", "low", "close", "vol"):
        assert col in src, f"DO UPDATE 必须覆盖 {col} 列"


def test_upsert_do_update_semantics():
    """验证 ON CONFLICT DO UPDATE 的 upsert 语义：同 (code,date) 第二次写入覆盖第一次。

    用 SQLite 原生 SQL（绕过 save_to_db 里的 PostgreSQL 专有 CAST）验证 DO UPDATE
    行为，确保换库不会退化为 DO NOTHING。
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    d = "2026-06-20"
    sql = """
        INSERT INTO daily_k (code, date, open, high, low, close, vol)
        VALUES (:code, :date, :open, :high, :low, :close, :vol)
        ON CONFLICT (code, date) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, vol = EXCLUDED.vol
    """
    with engine.connect() as conn:
        conn.execute(text(sql), {"code": "688256", "date": d, "open": 1868, "high": 1900, "low": 1850, "close": 1868, "vol": 1000})
        conn.commit()
        # 第二次写入：除权后回溯调整的新前复权价
        conn.execute(text(sql), {"code": "688256", "date": d, "open": 1182, "high": 1190, "low": 1175, "close": 1182, "vol": 1000})
        conn.commit()
        row = conn.execute(text("SELECT close FROM daily_k WHERE code='688256' AND date=:d"), {"d": d}).fetchone()
    # 新值必须覆盖旧值（DO NOTHING 时这里仍是 1868 → bug）
    assert row[0] == 1182, f"复权后新价应覆盖旧价，实际 close={row[0]}"


def test_save_skips_invalid_code():
    """非法 code 格式应被拒绝（防注入，验证 validate 守卫未受 upsert 改动影响）。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    df = pd.DataFrame({
        "日期": [pd.Timestamp("2026-06-20")],
        "开盘": [10.0], "最高": [10.1], "最低": [9.9],
        "收盘": [10.0], "成交量": [100000.0],
    })
    assert save_to_db(df, "000001'; DROP TABLE daily_k;--", engine) is False


def test_sop_detail_fields_in_persistence_whitelist():
    """#1：SOP veto/check/risk/bonus/quality_score/subgrade 必须在持久化白名单里。

    回归 601138 调试痛点：这些字段运行时设置了，但白名单缺失导致持久化时被丢弃，
    用户看到 D 级却无法追溯原因。修复后白名单必须含这些字段。
    """
    from core.db import PRICE_ACTION_DETAIL_KEYS
    required = [
        "sop_vetoes", "sop_checks", "sop_bonuses",
        "sop_quality_score", "sop_subgrade",
        "stock_rank_in_sector",  # P0 个股板块内排名
        "limit_up_unsealed",     # #13 涨停开板标记
    ]
    missing = [f for f in required if f not in PRICE_ACTION_DETAIL_KEYS]
    assert not missing, f"以下字段未在持久化白名单（无法复盘SOP判定）: {missing}"
