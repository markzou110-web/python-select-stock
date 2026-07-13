"""Tests for 改动 #14 — 走查前推 / 样本外测试 (batch_experiment.run_walk_forward_experiment)."""
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.batch_experiment import (
    _split_df_by_ratio,
    build_rolling_windows,
    run_rolling_walk_forward_experiment,
    run_walk_forward_experiment,
    DEFAULT_TRAIN_RATIO,
    OVERFIT_WARNING_GAP,
)


def test_rolling_windows_keep_train_validation_test_separate():
    windows = build_rolling_windows(220, 100, 20, 20, 20)
    assert len(windows) == 5
    first = windows[0]
    assert first["train"].stop == first["validation"].start
    assert first["validation"].stop == first["test"].start
    assert set(range(*first["train"].indices(220))).isdisjoint(range(*first["test"].indices(220)))


def test_rolling_walk_forward_returns_weighted_oos_summary(monkeypatch):
    df = _pine_fixture(220)
    monkeypatch.setattr("core.batch_experiment.load_from_db", lambda *args: df.copy())
    monkeypatch.setattr("core.batch_experiment.calculate_indicators", lambda frame, **kw: frame)
    monkeypatch.setattr("core.batch_experiment.calculate_pine_indicators", lambda frame: frame)

    result = run_rolling_walk_forward_experiment(None, ["000001"], "pine", {
        "train_size": 100, "validation_size": 20, "test_size": 20, "step_size": 20,
    })
    assert result["meta"]["params_frozen"] is True
    assert result["summary"]["test_windows"] == 5
    periods = result["items"][0]["windows"][0]["periods"]
    assert periods["train"]["end"] < periods["validation"]["start"]
    assert periods["validation"]["end"] < periods["test"]["start"]


def test_rolling_walk_forward_excludes_unmature_window_tail(monkeypatch):
    df = _pine_fixture(220)
    captured = []
    monkeypatch.setattr("core.batch_experiment.load_from_db", lambda *args: df.copy())
    monkeypatch.setattr("core.batch_experiment.calculate_indicators", lambda frame, **kw: frame)
    monkeypatch.setattr("core.batch_experiment.calculate_pine_indicators", lambda frame: frame)

    def fake_backtest(frame, strategy_type, params):
        captured.append(params.copy())
        return {"summary": {"signal_count": 0, "win_rate": 0, "total_return": 0, "profit_factor": 0, "max_drawdown": 0}}

    monkeypatch.setattr("core.batch_experiment.run_single_stock_backtest", fake_backtest)
    run_rolling_walk_forward_experiment(None, ["000001"], "pine", {
        "train_size": 100, "validation_size": 20, "test_size": 20, "step_size": 20,
        "max_hold_days": 10, "entry_mode": "next_open_confirm",
    })
    first_test = captured[2]
    expected_end = str(df.loc[128, "日期"])[:10]  # test ends at 139; reserve 11 rows
    assert first_test["signal_end_date"] == expected_end


def _pine_fixture(rows: int = 200) -> pd.DataFrame:
    """构造一份带 pine 信号的 DataFrame（rows 行，idx 125/138 处触发信号）。"""
    start = date(2024, 1, 1)
    close = np.linspace(10, 16, rows)
    df = pd.DataFrame({
        "日期": [(start + timedelta(days=i)).isoformat() for i in range(rows)],
        "开盘": close - 0.5,
        "最高": close + 0.1,
        "最低": close - 0.2,
        "收盘": close,
        "成交量": np.full(rows, 1_000_000.0),
        "Vol_MA20": np.full(rows, 500_000.0),
        "ATR": np.full(rows, 0.3),
        "RF_Upward": False, "RF_Downward": False, "ST_Signal": False,
        "RQK_Up": False, "HalfTrend_Up": False, "QQE_Long": False,
    })
    for idx in [125, 138]:
        if idx < rows:
            df.loc[idx, ["RF_Upward", "ST_Signal", "RQK_Up", "HalfTrend_Up", "QQE_Long"]] = True
    return df


def test_split_df_by_ratio_splits_in_order():
    """拆分后 train 段在前、test 段在后，无重叠，比例近似 train_ratio。"""
    df = _pine_fixture(200)
    df_train, df_test = _split_df_by_ratio(df, 0.7)
    # train 段早于 test 段
    assert str(df_train["日期"].iloc[-1]) <= str(df_test["日期"].iloc[0])
    # 比例近似 0.7（train 段约 140 行，test 段约 60 行）
    assert abs(len(df_train) - 140) <= 5
    assert abs(len(df_test) - 60) <= 5
    # 无重叠
    train_dates = set(df_train["日期"])
    test_dates = set(df_test["日期"])
    assert not (train_dates & test_dates)


def test_split_df_too_short_returns_empty_test():
    """数据不足 80 行时 test 段为空。"""
    df = _pine_fixture(50)
    df_train, df_test = _split_df_by_ratio(df, 0.7)
    assert df_test.empty


def test_walk_forward_returns_is_and_oos_structure(monkeypatch):
    """run_walk_forward_experiment 返回结构含 in_sample / out_of_sample / overfit_gap。"""
    df = _pine_fixture(200)

    def fake_load(code, start_date, engine):
        return df.copy()

    def fake_calc(df, enable_pine_indicators=False):
        return df

    def fake_calc_pine(df):
        return df

    monkeypatch.setattr("core.batch_experiment.load_from_db", fake_load)
    monkeypatch.setattr("core.batch_experiment.calculate_indicators", fake_calc)
    monkeypatch.setattr("core.batch_experiment.calculate_pine_indicators", fake_calc_pine)

    result = run_walk_forward_experiment(None, ["000001"], "pine", {})
    assert result["meta"]["train_ratio"] == DEFAULT_TRAIN_RATIO
    item = result["items"][0]
    assert item["status"] == "OK"
    assert "in_sample" in item and "out_of_sample" in item
    assert "win_rate" in item["in_sample"] and "win_rate" in item["out_of_sample"]
    # overfit_gap = OOS胜率 - IS胜率
    assert item["overfit_gap"] == round(
        item["out_of_sample"]["win_rate"] - item["in_sample"]["win_rate"], 2
    )
    assert "overfit_warning" in item


def test_walk_forward_overfit_warning_flag(monkeypatch):
    """IS 胜率明显高于 OOS 胜率（gap < -OVERFIT_WARNING_GAP）→ overfit_warning=True。"""
    df = _pine_fixture(200)

    monkeypatch.setattr("core.batch_experiment.load_from_db", lambda code, sd, e: df.copy())
    monkeypatch.setattr("core.batch_experiment.calculate_indicators", lambda df, **kw: df)
    monkeypatch.setattr("core.batch_experiment.calculate_pine_indicators", lambda df: df)

    # mock run_single_stock_backtest：train 段返回高胜率(80%)，test 段返回低胜率(40%)
    call_state = {"n": 0}

    def fake_backtest(df_in, strategy_type="squeeze", params=None, bench_df=None):
        call_state["n"] += 1
        is_train = call_state["n"] % 2 == 1  # 第1次=train，第2次=test
        wr = 80.0 if is_train else 40.0
        return {
            "summary": {
                "signal_count": 5, "win_rate": wr, "total_return": 10.0,
                "profit_factor": 2.0, "max_drawdown": -5.0,
            },
            "trades": [], "equity_curve": [], "reason": "",
        }

    monkeypatch.setattr("core.batch_experiment.run_single_stock_backtest", fake_backtest)

    result = run_walk_forward_experiment(None, ["000001"], "pine", {})
    item = result["items"][0]
    # IS=80%, OOS=40%, gap = 40-80 = -40 < -10 → 过拟合警告
    assert item["in_sample"]["win_rate"] == 80.0
    assert item["out_of_sample"]["win_rate"] == 40.0
    assert item["overfit_gap"] == -40.0
    assert item["overfit_warning"] is True  # 明显过拟合
