"""当日K线一致性回归：

背景实例（2026-09-21, 300852）：午间同步把半日部分数据写入 daily_k（收 69.66、
量仅为日均 8%），18:00 收盘最终化同步因进程重启被跳过，导致图表画半日 K 线
而头部实时快照为 72.25/+5.81%。

覆盖：
1. get_snapshot_daily_bar：快照 → 当日 OHLCV（fail-open）
2. apply_snapshot_bar_to_frame：日期匹配才写回，high/low 取更极端值
3. needs_post_close_catch_up：错过的收盘同步在启动时补跑的判定
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.data import (
    apply_snapshot_bar_to_frame,
    get_snapshot_daily_bar,
)
from core.sync_scheduler import needs_post_close_catch_up


def _snapshot_frame(rows):
    base = {
        "code": "300852", "name": "四会富仕", "quote_time": "20260921161427",
        "price": 72.25, "open": 68.37, "high": 72.80, "low": 67.70,
        "vol": 133608.0, "pct_chg": 5.81, "last_close": 68.28,
    }
    records = [{**base, **row} for row in rows] if rows else [base]
    return pd.DataFrame(records)


def test_snapshot_bar_parses_quote_date_and_ohlc():
    bar = get_snapshot_daily_bar("300852", _snapshot_frame(None))
    assert bar == {
        "date": "2026-09-21", "open": 68.37, "high": 72.80,
        "low": 67.70, "close": 72.25, "vol": 133608.0,
    }


def test_snapshot_bar_missing_or_invalid_fails_open():
    frame = _snapshot_frame(None)
    assert get_snapshot_daily_bar("000999", frame) is None          # 无此代码
    assert get_snapshot_daily_bar("300852", pd.DataFrame()) is None  # 空快照
    assert get_snapshot_daily_bar("300852", None) is not None        # 无快照→内部刷新


def test_snapshot_bar_bad_price_fails_open():
    frame = _snapshot_frame([{"price": 0.0}])
    assert get_snapshot_daily_bar("300852", frame) is None


def test_apply_snapshot_bar_corrects_last_row_when_date_matches():
    df = pd.DataFrame({
        "日期": ["2026-09-18", "2026-09-21"],
        "开盘": [69.48, 68.37],
        "最高": [74.50, 69.98],
        "最低": [66.33, 67.70],
        "收盘": [68.28, 69.66],
        "成交量": [140800.0, 11517.0],
    })
    bar = {"date": "2026-09-21", "open": 68.37, "high": 72.80, "low": 67.70, "close": 72.25, "vol": 133608.0}
    assert apply_snapshot_bar_to_frame(df, bar) is True
    assert df.iloc[-1]["收盘"] == 72.25
    assert df.iloc[-1]["最高"] == 72.80          # 取更极端值
    assert df.iloc[-1]["开盘"] == 68.37
    assert df.iloc[-1]["成交量"] == 133608.0


def test_apply_snapshot_bar_ignores_date_mismatch():
    df = pd.DataFrame({"日期": ["2026-09-18"], "开盘": [69.48], "最高": [74.5], "最低": [66.33], "收盘": [68.28]})
    bar = {"date": "2026-09-21", "open": 68.37, "high": 72.80, "low": 67.70, "close": 72.25}
    assert apply_snapshot_bar_to_frame(df, bar) is False
    assert df.iloc[-1]["收盘"] == 68.28


def test_apply_snapshot_bar_fail_open_on_empty():
    assert apply_snapshot_bar_to_frame(None, {"date": "2026-09-21"}) is False
    assert apply_snapshot_bar_to_frame(pd.DataFrame(), {"date": "2026-09-21"}) is False


def test_catch_up_needed_when_last_sync_was_midday():
    now = datetime(2026, 9, 21, 21, 32)
    last = datetime(2026, 9, 21, 12, 29)
    assert needs_post_close_catch_up(now, last, is_trading_day=True) is True


def test_catch_up_skipped_when_post_close_sync_ran():
    now = datetime(2026, 9, 21, 21, 32)
    last = datetime(2026, 9, 21, 18, 5)
    assert needs_post_close_catch_up(now, last, is_trading_day=True) is False


def test_catch_up_skipped_before_post_close_time():
    now = datetime(2026, 9, 21, 14, 0)
    assert needs_post_close_catch_up(now, None, is_trading_day=True) is False


def test_catch_up_skipped_on_non_trading_day():
    now = datetime(2026, 9, 20, 21, 32)  # 周日
    assert needs_post_close_catch_up(now, None, is_trading_day=False) is False


def test_catch_up_needed_when_last_sync_was_yesterday():
    now = datetime(2026, 9, 21, 21, 32)
    last = datetime(2026, 9, 20, 18, 5)
    assert needs_post_close_catch_up(now, last, is_trading_day=True) is True
