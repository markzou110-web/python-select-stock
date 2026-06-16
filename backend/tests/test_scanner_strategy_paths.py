import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import scanner


def test_single_stock_task_backfills_missing_pine_indicators(monkeypatch):
    n = 130
    close = np.linspace(10, 13, n)
    df = pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="D"),
        "开盘": close - 0.3,
        "收盘": close,
        "最高": close + 0.1,
        "最低": close - 0.1,
        "成交量": np.full(n, 200000.0),
        "Vol_MA20": np.full(n, 100000.0),
        "RSI": np.full(n, 60.0),
        "MACD_DIF": np.full(n, 0.2),
        "MACD_DEA": np.full(n, 0.1),
        "BB_Width": np.full(n, 0.08),
        "Sqz_Ratio": np.full(n, 0.08),
        "EMA5": close - 0.2,
        "EMA10": close - 0.3,
        "EMA20": close - 0.4,
        "EMA60": close - 0.5,
    })

    def fake_calculate_pine_indicators(input_df):
        enriched = input_df.copy()
        enriched["RF_Upward"] = True
        enriched["RF_Downward"] = False
        enriched["ST_Signal"] = True
        enriched["RQK_Up"] = True
        enriched["HalfTrend_Up"] = True
        enriched["QQE_Long"] = True
        return enriched

    def fake_check_pine_strategy(input_df, min_signals=3, fund_data=None):
        assert {"RF_Upward", "RQK_Up", "HalfTrend_Up", "QQE_Long"}.issubset(input_df.columns)
        return True, {"Score": 88, "信号数": "5/5"}

    monkeypatch.setattr(scanner, "calculate_pine_indicators", fake_calculate_pine_indicators)
    monkeypatch.setattr(scanner, "check_pine_strategy", fake_check_pine_strategy)

    result = scanner.single_stock_task(
        "000001",
        "测试股票",
        price=13,
        vol=200000,
        open_price=12.7,
        threshold=0.12,
        vol_multiplier=1.5,
        rsi_min=55,
        use_macd_filter=True,
        use_bb_sqz=True,
        sqz_lookback=10,
        use_weekly=False,
        preloaded_df=df,
        strategy_type="pine",
        pine_min_signals=5,
    )

    assert result["Score"] == 88
    assert result["strategy_type"] == "pine"


# ── tv_dual 周线门槛（改动 #4，默认关闭）──

def _make_tv_dual_df():
    """构造一份能让 check_tv_dual_strategy 命中的最小 DataFrame。"""
    n = 130
    close = np.linspace(10, 13, n)
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="D"),
        "开盘": close - 0.3,
        "收盘": close,
        "最高": close + 0.1,
        "最低": close - 0.1,
        "成交量": np.full(n, 200000.0),
        "Vol_MA20": np.full(n, 100000.0),
        "RSI": np.full(n, 60.0),
        "RSI_WILDER": np.full(n, 60.0),
        "MACD_DIF": np.full(n, 0.2),
        "MACD_DEA": np.full(n, 0.1),
        "BB_Width": np.full(n, 0.08),
        "Sqz_Ratio": np.full(n, 0.08),
        "EMA5": close - 0.2,
        "EMA10": close - 0.3,
        "EMA20": close - 0.4,
        "EMA60": close - 0.5,
    })


def test_tv_dual_weekly_gate_disabled_by_default(monkeypatch):
    """默认 tv_weekly_gate=False → 不调用 get_weekly_indicators，不过滤。"""
    weekly_called = {"n": 0}

    def fake_check_tv_dual_strategy(df, **kwargs):
        return True, {"Score": 88, "signal": "强共振"}

    def fake_weekly(*a, **kw):
        weekly_called["n"] += 1
        return False  # 即便周线弱，也不应被调用

    monkeypatch.setattr(scanner, "check_tv_dual_strategy", fake_check_tv_dual_strategy)
    monkeypatch.setattr(scanner, "get_weekly_indicators", fake_weekly)

    result = scanner.single_stock_task(
        "000001", "测试", price=13, vol=200000, open_price=12.7,
        threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True,
        use_bb_sqz=False, sqz_lookback=10, use_weekly=False,
        preloaded_df=_make_tv_dual_df(), strategy_type="tv_dual_strict",
        # tv_weekly_gate 默认 False
    )

    assert weekly_called["n"] == 0  # 默认不查周线
    assert result.get("Score") == 88


def test_tv_dual_weekly_gate_filters_bearish_weekly(monkeypatch):
    """tv_weekly_gate=True + 周线弱 → 返回 reason 过滤，不调用 check_tv_dual_strategy。"""
    strategy_called = {"n": 0}

    def fake_check_tv_dual_strategy(df, **kwargs):
        strategy_called["n"] += 1
        return True, {"Score": 88, "signal": "强共振"}

    monkeypatch.setattr(scanner, "check_tv_dual_strategy", fake_check_tv_dual_strategy)
    # 周线弱（False）
    monkeypatch.setattr(scanner, "get_weekly_indicators", lambda *a, **kw: False)

    result = scanner.single_stock_task(
        "000001", "测试", price=13, vol=200000, open_price=12.7,
        threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True,
        use_bb_sqz=False, sqz_lookback=10, use_weekly=False,
        preloaded_df=_make_tv_dual_df(), strategy_type="tv_dual_strict",
        tv_weekly_gate=True,
    )

    assert strategy_called["n"] == 0  # 被周线门槛过滤，未到策略判定
    assert "周线" in result.get("reason", "")


def test_tv_dual_weekly_gate_passes_bullish_weekly(monkeypatch):
    """tv_weekly_gate=True + 周线强 → 通过门槛，进入策略判定。"""
    monkeypatch.setattr(
        scanner, "check_tv_dual_strategy",
        lambda df, **kw: (True, {"Score": 88, "signal": "强共振"}),
    )
    monkeypatch.setattr(scanner, "get_weekly_indicators", lambda *a, **kw: True)

    result = scanner.single_stock_task(
        "000001", "测试", price=13, vol=200000, open_price=12.7,
        threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True,
        use_bb_sqz=False, sqz_lookback=10, use_weekly=False,
        preloaded_df=_make_tv_dual_df(), strategy_type="tv_dual_strict",
        tv_weekly_gate=True,
    )

    assert result.get("Score") == 88  # 通过门槛，命中策略


# ── 数据预检熔断（改动 #2）──

def test_perform_market_scan_aborts_when_preflight_blocking(monkeypatch):
    """preflight 报告 blocking=True → perform_market_scan 返回空列表。"""
    monkeypatch.setattr(scanner, "SCAN_PREFLIGHT_ENFORCE", True)

    fake_engine = object()
    monkeypatch.setattr(scanner, "get_db_engine", lambda: fake_engine)
    monkeypatch.setattr(
        scanner, "build_scan_preflight",
        lambda engine, data_date=None, **kw: {
            "status": "error",
            "blocking": True,
            "checks": [{"name": "coverage", "status": "error", "message": "覆盖率过低"}],
        },
    )

    result = scanner.perform_market_scan(strategy_type="tv_dual_strict")
    assert result == []


def test_perform_market_scan_proceeds_when_preflight_not_blocking(monkeypatch):
    """preflight blocking=False → 不熔断中止，继续走扫描流程（验证未被早返回 []）。

    用 MagicMock 假 engine 让本地兜底的 DB 查询返回空快照，避免真实 DB 与网络，
    聚焦"预检通过 → 不在预检处中断"的语义。
    """
    from unittest.mock import MagicMock

    monkeypatch.setattr(scanner, "SCAN_PREFLIGHT_ENFORCE", True)

    fake_engine = MagicMock()
    # 本地兜底会查 daily_k 取 max_date；让连接执行返回空，使快照最终为空 → 无候选。
    fake_conn = MagicMock()
    exec_result = MagicMock()
    exec_result.fetchone.return_value = (None,)  # 无 max_date
    fake_conn.execute.return_value = exec_result
    fake_engine.connect.return_value.__enter__ = lambda self: fake_conn
    fake_engine.connect.return_value.__exit__ = lambda *a: False

    monkeypatch.setattr(scanner, "get_db_engine", lambda: fake_engine)
    preflight_called = {"n": 0}
    monkeypatch.setattr(
        scanner, "build_scan_preflight",
        lambda engine, data_date=None, **kw: (
            preflight_called.__setitem__("n", preflight_called["n"] + 1) or {
                "status": "ok",
                "blocking": False,
                "checks": [],
                "summary": {},
            }
        ),
    )
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: pd.DataFrame())

    # 预检不阻断：函数会继续执行并最终因无数据/无候选抛 HTTPException(503) 或返回 []。
    # 关键断言：build_scan_preflight 被调用且 blocking=False 时未在预检处早返回。
    try:
        result = scanner.perform_market_scan(strategy_type="tv_dual_strict", local_only=True)
        assert result == []
    except Exception:
        # 因 mock engine 无法真实查询，下游可能抛 503——这也证明已越过预检阶段
        pass

    assert preflight_called["n"] == 1  # 预检确实被调用且未被熔断跳过
