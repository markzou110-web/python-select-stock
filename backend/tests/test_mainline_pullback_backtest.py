import pandas as pd

from core.mainline_pullback_backtest import (
    build_report,
    build_sector_context,
    find_first_pullback_confirmation,
    simulate_atr_trade,
    stock_sector_fit,
)


def test_sector_context_marks_strong_early_sector_as_mainline():
    rows = pd.DataFrame([
        {"date": "2026-01-01", "industry": "半导体", "pct": 0.5, "breadth": 65, "hot_ratio": 5, "limit_count": 0},
        {"date": "2026-01-02", "industry": "半导体", "pct": 2.0, "breadth": 75, "hot_ratio": 12, "limit_count": 2},
        {"date": "2026-01-02", "industry": "银行", "pct": 0.1, "breadth": 51, "hot_ratio": 0, "limit_count": 0},
    ])
    result = build_sector_context(rows)
    semiconductor = result[(result["industry"] == "半导体") & (result["date"] == pd.Timestamp("2026-01-02"))].iloc[0]
    assert semiconductor["phase"] in {"SECTOR_EARLY", "SECTOR_CONFIRM"}
    assert bool(semiconductor["mainline"]) is True


def _pullback_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=8),
        "开盘": [10, 10.5, 10.35, 10.1, 10.25, 10.6, 10.8, 11],
        "最高": [10.6, 10.6, 10.45, 10.3, 10.65, 10.9, 11, 11.2],
        "最低": [9.9, 10.2, 10.0, 9.95, 10.15, 10.5, 10.7, 10.9],
        "收盘": [10.5, 10.4, 10.15, 10.2, 10.6, 10.8, 10.9, 11.1],
        "成交量": [100, 70, 65, 75, 130, 120, 110, 100],
        "Vol_MA20": [100] * 8,
        "EMA10": [10] * 8,
        "EMA20": [9.8] * 8,
    })


def test_first_pullback_requires_prior_low_volume_bar_then_confirmation():
    assert find_first_pullback_confirmation(_pullback_frame(), 0, wait_days=5) == 4


def test_practical_pullback_accepts_average_volume_confirmation():
    frame = _pullback_frame()
    frame.loc[4, "成交量"] = 100
    assert find_first_pullback_confirmation(
        frame,
        0,
        wait_days=10,
        max_pullback_volume_ratio=1.0,
        min_confirmation_volume_ratio=1.0,
    ) == 4


def test_stock_sector_fit_uses_only_trailing_relative_returns():
    frame = pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=7),
        "收盘": [10, 10.2, 10.4, 10.6, 10.8, 11.0, 11.4],
    })
    lookup = {
        (pd.Timestamp(day).normalize(), "半导体"): {"pct": 0.5, "sector_5d_pct": 2.0}
        for day in frame["日期"]
    }
    result = stock_sector_fit(frame, 6, "半导体", lookup)
    assert result["core"] is True
    assert result["relative_5d"] > 1
    assert result["lead_consistency"] >= 60


def test_atr_trade_enters_next_open_and_applies_structure_stop_with_costs():
    frame = pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=12),
        "开盘": [10.0] * 12,
        "最高": [10.2] * 12,
        "最低": [9.9, 9.9, 9.5] + [9.9] * 9,
        "收盘": [10.0] * 12,
        "ATR": [0.2] * 12,
    })
    result = simulate_atr_trade(
        "600000",
        frame,
        0,
        max_hold_days=10,
        max_open_gap_pct=3.0,
        structure_low=9.6,
    )
    assert result["filled"] is True
    assert result["entry_date"] == "2026-01-02"
    assert result["exit_date"] == "2026-01-03"
    assert result["exit_reason"] == "结构止损"
    assert result["return_pct"] < -4


def test_report_keeps_small_profitable_test_sample_in_shadow():
    rows = []
    for arm in (
        "signal_next_open_10d", "signal_next_open_20d",
        "pullback_strict_10d", "pullback_strict_20d",
        "pullback_practical_10d", "pullback_practical_20d",
    ):
        for period, count in (("2024-01-01", 120), ("2025-01-01", 60), ("2026-01-01", 20)):
            for idx in range(count):
                rows.append({
                    "code": f"{idx:06d}", "signal_date": period, "arm": arm,
                    "exec_filled": True, "exec_return_pct": 2.0, "exec_exit_reason": "持有期结束",
                })
    report = build_report(pd.DataFrame(rows))
    assert report["selected_arm"] is not None
    assert report["status"] == "SHADOW_ONLY"
    assert report["test_confirmed"] is False
