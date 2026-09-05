from __future__ import annotations

import numpy as np
import pandas as pd


def _frame(close, *, open_=None, high=None, low=None, volume=None, code="600001"):
    size = len(close)
    close = np.asarray(close, dtype=float)
    open_ = np.asarray(open_ if open_ is not None else close * 0.99, dtype=float)
    high = np.asarray(high if high is not None else np.maximum(open_, close) * 1.01, dtype=float)
    low = np.asarray(low if low is not None else np.minimum(open_, close) * 0.99, dtype=float)
    volume = np.asarray(volume if volume is not None else np.full(size, 100_000.0), dtype=float)
    return pd.DataFrame({
        "code": [code] * size,
        "日期": pd.date_range("2026-01-01", periods=size, freq="B"),
        "开盘": open_,
        "最高": high,
        "最低": low,
        "收盘": close,
        "成交量": volume,
    })


def test_cross_sectional_rps_ranks_only_information_available_as_of_date():
    from core.sequoia_research import compute_cross_sectional_rps

    dates = pd.date_range("2025-01-01", periods=122, freq="B")
    rows = []
    paths = {
        "000001": np.linspace(10, 20, len(dates)),
        "000002": np.linspace(10, 11, len(dates)),
        "000003": np.linspace(20, 10, len(dates)),
    }
    # 截止日之后制造极端反转；不得改变截止日的 RPS。
    paths["000001"][-1] = 1
    paths["000003"][-1] = 30
    for code, values in paths.items():
        rows.extend(
            {"code": code, "date": day, "close": value, "industry": "测试"}
            for day, value in zip(dates, values)
        )

    result = compute_cross_sectional_rps(
        pd.DataFrame(rows),
        periods=(60, 120),
        as_of=dates[-2],
    )

    assert result["000001"]["rps_120"] == 100.0
    assert result["000003"]["rps_120"] < result["000002"]["rps_120"]
    assert result["000001"]["rps_data_date"] == dates[-2].date().isoformat()


def test_rps_loader_reduces_history_to_point_in_time_closes():
    from sqlalchemy import create_engine
    from core.sequoia_research import load_cross_sectional_rps

    engine = create_engine("sqlite:///:memory:")
    dates = pd.date_range("2025-01-01", periods=121, freq="B")
    daily_rows = []
    for code, start, end in (
        ("000001", 10, 20),
        ("000002", 10, 11),
        ("000003", 20, 10),
    ):
        daily_rows.extend(
            {"code": code, "date": day.date().isoformat(), "close": value}
            for day, value in zip(dates, np.linspace(start, end, len(dates)))
        )
    pd.DataFrame(daily_rows).to_sql("daily_k", engine, index=False)
    pd.DataFrame({
        "code": ["000001", "000002", "000003"],
        "industry": ["测试", "测试", "测试"],
    }).to_sql("stock_basic", engine, index=False)

    result = load_cross_sectional_rps(engine, dates[-1].date().isoformat())

    assert result["000001"]["rps_120"] == 100.0
    assert result["000003"]["rps_120"] < result["000002"]["rps_120"]
    assert result["000001"]["rps_data_date"] == dates[-1].date().isoformat()


def test_high_tight_flag_detects_ordered_advance_then_tight_low_volume_base():
    from core.sequoia_research import high_tight_flag_signal_mask

    close = np.r_[np.full(40, 10.0), np.linspace(10.0, 17.0, 16), np.full(24, 16.7)]
    frame = _frame(close, volume=np.r_[np.full(79, 100_000.0), 40_000.0])

    mask = high_tight_flag_signal_mask(frame)

    assert bool(mask.iloc[-1]) is True


def test_turtle_breakout_uses_prior_20_day_high_and_liquidity():
    from core.sequoia_research import turtle_breakout_signal_mask

    close = np.r_[np.full(29, 10.0), 11.2]
    frame = _frame(
        close,
        open_=np.r_[np.full(29, 9.9), 10.8],
        high=np.r_[np.full(29, 10.5), 11.4],
        low=np.r_[np.full(29, 9.8), 10.7],
        volume=np.full(30, 100_000.0),
    )

    mask = turtle_breakout_signal_mask(frame)

    assert bool(mask.iloc[-1]) is True
    assert not bool(mask.iloc[:-1].any())


def test_limit_up_shakeout_respects_board_specific_price_limits():
    from core.sequoia_research import limit_up_shakeout_signal_mask

    main = _frame(
        [10.0, 11.0, 11.3],
        open_=[9.9, 10.2, 11.8],
        high=[10.1, 11.0, 11.9],
        low=[9.8, 10.2, 11.0],
        volume=[100_000, 100_000, 250_000],
        code="600001",
    )
    growth_same_move = main.assign(code="300001")
    growth_limit_up = _frame(
        [10.0, 12.0, 12.3],
        open_=[9.9, 10.5, 12.8],
        high=[10.1, 12.0, 12.9],
        low=[9.8, 10.5, 12.0],
        volume=[100_000, 100_000, 250_000],
        code="300001",
    )

    assert bool(limit_up_shakeout_signal_mask(main, "600001").iloc[-1]) is True
    assert bool(limit_up_shakeout_signal_mask(growth_same_move, "300001").iloc[-1]) is False
    assert bool(limit_up_shakeout_signal_mask(growth_limit_up, "300001").iloc[-1]) is True


def test_sequoia_research_strategies_are_registered_as_shadow_only():
    from core.strategy_registry import get_strategy

    for strategy_type in ("high_tight_flag", "turtle_breakout", "limit_up_shakeout"):
        item = get_strategy(strategy_type)
        assert item is not None
        assert item["supports_scan"] is True
        assert item["supports_backtest"] is True
        assert item["release_state"] == "SHADOW"
        assert item["trade_eligible"] is False


def test_backtest_dispatch_uses_new_signal_definitions():
    from core.strategy import _find_all_signal_indices

    close = np.r_[np.full(129, 10.0), 11.2]
    frame = _frame(
        close,
        open_=np.r_[np.full(129, 9.9), 10.8],
        high=np.r_[np.full(129, 10.5), 11.4],
        low=np.r_[np.full(129, 9.8), 10.7],
        volume=np.full(130, 100_000.0),
    )

    indices = _find_all_signal_indices(frame, "turtle_breakout", 0.12, 1.5, 55, 3)

    assert indices == [129]


def test_research_backtest_uses_shared_execution_engine():
    from core.strategy import calculate_research_pattern_win_rate

    close = np.r_[np.full(129, 10.0), 11.2, np.full(6, 11.3)]
    frame = _frame(
        close,
        open_=np.r_[np.full(129, 9.9), 10.8, np.full(6, 11.25)],
        high=np.r_[np.full(129, 10.5), 11.4, np.full(6, 11.35)],
        low=np.r_[np.full(129, 9.8), 10.7, np.full(6, 11.2)],
        volume=np.full(136, 100_000.0),
    )

    result = calculate_research_pattern_win_rate(frame, "turtle_breakout")

    assert result["signal_count"] == 1
    assert result["avg_return"] > 0


def test_limit_up_shakeout_requires_confirmed_sealed_event():
    from core.sequoia_research import confirm_limit_up_shakeout_candidates

    candidates = [
        {"代码": "600001", "Score": 62},
        {"代码": "600002", "Score": 62},
        {"代码": "600003", "Score": 62},
    ]
    event_map = {
        "600001": {"status": "SEALED", "limit_up_streak": 2, "break_count": 1},
        "600002": {"status": "BROKEN", "limit_up_streak": 1, "break_count": 3},
    }

    confirmed = confirm_limit_up_shakeout_candidates(candidates, event_map, "2026-09-03")

    assert [item["代码"] for item in confirmed] == ["600001"]
    assert confirmed[0]["shakeout_source_event_date"] == "2026-09-03"
    assert confirmed[0]["prior_limit_up_status"] == "SEALED"


def test_scanner_research_candidate_is_never_trade_eligible():
    from core.scanner import _check_sequoia_research_strategy

    close = np.r_[np.full(79, 10.0), 11.2]
    frame = _frame(
        close,
        open_=np.r_[np.full(79, 9.9), 10.8],
        high=np.r_[np.full(79, 10.5), 11.4],
        low=np.r_[np.full(79, 9.8), 10.7],
        volume=np.full(80, 100_000.0),
    )

    matched, result = _check_sequoia_research_strategy(
        frame,
        "600001",
        "测试股份",
        "turtle_breakout",
    )

    assert matched is True
    assert result["release_state"] == "SHADOW"
    assert result["trade_eligible"] is False
    assert result["sequoia_research_shadow_only"] is True


def test_shadow_research_candidate_never_enters_bark_operation_lists():
    from core.sentinel import (
        _candidate_brief_action,
        _select_after_close_watchlist,
        _select_intraday_push_stocks,
    )

    candidate = {
        "代码": "600001",
        "Score": 70,
        "final_rank_score": 90,
        "sop_grade": "C",
        "trade_bucket": "SHADOW",
        "trade_eligible": False,
        "sequoia_research_shadow_only": True,
    }

    assert _select_intraday_push_stocks([candidate]) == []
    assert _select_after_close_watchlist([candidate]) == []
    assert _candidate_brief_action(candidate) == "SHADOW研究观察"
