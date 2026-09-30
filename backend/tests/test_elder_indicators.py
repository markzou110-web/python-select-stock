"""Elder 扩展指标测试：ADX/DMI 趋向系统、强力指数、OBV、A/D 集散线。

均为 calculate_indicators 的增量列，不改变既有列。bench_df 显式传入以避免
RS 兜底路径触发外部行情请求。
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.indicators import calculate_indicators


def _frame(close, *, open_=None, high=None, low=None, volume=None):
    close = np.asarray(close, dtype=float)
    size = len(close)
    open_ = np.asarray(open_ if open_ is not None else close * 0.999, dtype=float)
    high = np.asarray(high if high is not None else np.maximum(open_, close) * 1.005, dtype=float)
    low = np.asarray(low if low is not None else np.minimum(open_, close) * 0.995, dtype=float)
    volume = np.asarray(volume if volume is not None else np.full(size, 100_000.0), dtype=float)
    return pd.DataFrame({
        "日期": pd.bdate_range("2026-01-01", periods=size),
        "开盘": open_, "收盘": close, "最高": high, "最低": low, "成交量": volume,
    })


def _with_bench(df):
    bench = pd.DataFrame({"日期": df["日期"], "收盘": np.linspace(3000, 3100, len(df))})
    return calculate_indicators(df, bench_df=bench)


def _trending_close(size=120, start=10.0, slope=0.15):
    return start + np.arange(size) * slope


def test_adx_rising_in_uptrend_with_pdi_above_mdi():
    df = _with_bench(_frame(_trending_close()))
    assert df["ADX"].notna().iloc[-1]
    assert df["PDI"].iloc[-1] > df["MDI"].iloc[-1]
    assert df["ADX"].iloc[-1] > 25


def test_adx_distinguishes_trend_from_choppy_market():
    size = 120
    up = _trending_close(size)
    choppy = 20 + 0.6 * np.where(np.arange(size) % 2 == 0, 1.0, -1.0) * np.ones(size)
    adx_trend = _with_bench(_frame(up))["ADX"].iloc[-1]
    adx_chop = _with_bench(_frame(choppy))["ADX"].iloc[-1]
    assert adx_trend > adx_chop + 15


def test_mdi_dominates_in_downtrend():
    df = _with_bench(_frame(_trending_close()[::-1].copy()))  # 逐日下跌
    assert df["MDI"].iloc[-1] > df["PDI"].iloc[-1]


def test_force_index_equals_volume_times_price_change():
    close = _trending_close()
    close[50] -= 1.5  # 制造一根放量大阴线
    volume = np.full(len(close), 100_000.0)
    volume[50] = 300_000.0
    df = _with_bench(_frame(close, volume=volume))
    assert df["Force_Index"].iloc[50] == pytest.approx(volume[50] * (close[50] - close[49]))
    expected_fi2 = df["Force_Index"].ewm(span=2, adjust=False).mean()
    assert df["FI2"].iloc[-1] == pytest.approx(expected_fi2.iloc[-1])
    assert df["FI13"].notna().iloc[-1]


def test_obv_accumulates_signed_volume():
    close = [10.0, 10.5, 10.5, 10.2, 10.8]
    volume = [100.0, 200.0, 300.0, 400.0, 500.0]
    df = _with_bench(_frame(close, volume=volume))
    obv = df["OBV"].to_numpy()
    diffs = np.diff(obv)
    # 平盘日量不计入（sign=0），涨日 +vol，跌日 -vol
    assert diffs[0] == 200.0   # 涨
    assert diffs[1] == 0.0     # 平
    assert diffs[2] == -400.0  # 跌
    assert diffs[3] == 500.0   # 涨


def test_ad_line_weights_close_position_in_range():
    df = pd.DataFrame({
        "日期": pd.bdate_range("2026-01-01", periods=3),
        "开盘": [10.0, 10.0, 10.0],
        "收盘": [10.0, 10.5, 10.0],
        "最高": [11.0, 10.6, 10.0],
        "最低": [9.0, 10.0, 10.0],
        "成交量": [100.0, 100.0, 100.0],
    })
    result = _with_bench(df)
    ad = result["AD_Line"].to_numpy()
    # 第一日 close==open（十字星）→ 集散线不变（增量为 0）
    assert ad[0] == 0
    # 第二日 (10.5-10.0)/(10.6-10.0)=0.8333 → 增量 83.33
    assert ad[1] == pytest.approx(100.0 * (0.5 / 0.6))
    # 第三日无区间（一字板）→ 不贡献
    assert ad[2] == pytest.approx(ad[1])


def test_ad_line_survives_zero_range_bar():
    close = [10.0, 10.0, 10.2]
    df = _with_bench(_frame(close, open_=[10.0, 10.0, 10.1], high=[10.0, 10.0, 10.3], low=[10.0, 10.0, 10.0]))
    assert df["AD_Line"].notna().all()

