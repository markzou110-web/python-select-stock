import os
import sys
import inspect

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.a_grade_kline_replay import (
    GateSpec,
    _evaluate_strategy_execution,
    _gate_mask,
    _paired_signal_indices,
    _tiered_v11_candidate_mask,
    _tv_signal_events,
    _tv_signal_indices,
    build_gate_report,
    build_route_a_protocol_events,
    prepare_route_a_portfolio_events,
    replay_stock,
    summarize_gate,
)


def test_paired_signal_indices_are_deduplicated_and_causal():
    assert _paired_signal_indices([120, 125, 130], [122, 124, 140], window=3) == [122, 125]


def test_loose_tv_signal_indices_use_deduplicated_union():
    assert _tv_signal_indices([120, 125, 130], [122, 125, 140], require_both=False) == [
        120, 122, 125, 130, 140,
    ]


def test_tv_signal_events_preserve_the_strategy_that_triggered_the_buy():
    assert _tv_signal_events([120, 125], [122, 125], require_both=False) == [
        (120, frozenset({"ma"})),
        (122, frozenset({"zp"})),
        (125, frozenset({"ma", "zp"})),
    ]


def test_tiered_v11_executes_a_and_only_qualified_b_signals():
    frame = pd.DataFrame({
        "tv_execution_tier": ["A", "B", "B", "B", "C"],
        "pa_action": ["AVOID", "READY", "READY", "AVOID", "READY"],
        "pa_score": [10, 60, 59, 80, 90],
        "market_offensive": [False, True, True, True, True],
    })

    assert _tiered_v11_candidate_mask(frame).tolist() == [True, True, False, False, False]
    assert _tv_signal_events([120, 125], [122, 125], require_both=True) == [
        (122, frozenset({"ma", "zp"})),
        (125, frozenset({"ma", "zp"})),
    ]


def _execution_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": pd.date_range("2026-01-01", periods=7),
            "开盘": [10.0, 10.0, 10.5, 11.0, 10.8, 10.6, 10.5],
            "最高": [10.2, 10.4, 11.0, 11.6, 11.0, 10.8, 10.7],
            "最低": [9.8, 9.9, 10.3, 10.8, 10.6, 10.4, 10.3],
            "收盘": [10.0, 10.3, 10.8, 11.4, 10.9, 10.5, 10.4],
            "成交量": [1_000_000] * 7,
            "EMA20": [9.8, 9.9, 10.0, 10.2, 10.7, 10.7, 10.7],
        }
    )


def test_ma_trade_exits_on_strategy_take_profit_instead_of_fixed_holding_period():
    result = _evaluate_strategy_execution(
        "000001",
        _execution_frame(),
        signal_idx=0,
        sources=frozenset({"ma"}),
        zp_short_indices=[],
    )

    assert result["mature"] is True
    assert result["filled"] is True
    assert result["exit_reason"] == "均线策略止盈信号(+15%)"
    assert result["exit_idx"] == 3
    assert result["hold_days"] == 2


def test_ma_take_profit_uses_confirmation_fill_basis():
    frame = _execution_frame()
    frame.loc[1, "最高"] = 10.6
    result = _evaluate_strategy_execution(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"ma"}),
        zp_short_indices=[],
        planned_entry_price=10.5,
    )

    assert result["filled"] is True
    assert result["exit_reason"] == "均线策略EMA20破位卖点"
    assert result["exit_idx"] == 6


def test_zp_trade_exits_next_open_after_short_signal():
    result = _evaluate_strategy_execution(
        "000001",
        _execution_frame(),
        signal_idx=0,
        sources=frozenset({"zp"}),
        zp_short_indices=[3],
    )

    assert result["mature"] is True
    assert result["filled"] is True
    assert result["exit_reason"] == "TV-ZP short卖点"
    assert result["exit_idx"] == 4
    assert result["hold_days"] == 3


def test_profitable_zp_trade_protects_profit_after_ema20_break():
    frame = _execution_frame()
    frame.loc[2, ["开盘", "最高", "最低", "收盘", "EMA20"]] = [10.5, 11.6, 10.4, 11.4, 10.2]
    frame.loc[3, ["开盘", "最高", "最低", "收盘", "EMA20"]] = [11.3, 11.4, 10.7, 10.8, 10.9]
    frame.loc[4, ["开盘", "最高", "最低", "收盘", "EMA20"]] = [10.7, 10.9, 10.5, 10.6, 10.8]

    result = _evaluate_strategy_execution(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"zp"}),
        zp_short_indices=[5],
    )

    assert result["exit_idx"] == 4
    assert result["exit_reason"] == "TV-ZP盈利保护：+15%后EMA20破位卖点"


def test_zp_ema20_break_does_not_exit_before_profit_protection_activates():
    frame = _execution_frame()
    frame.loc[2, ["最高", "收盘", "EMA20"]] = [11.0, 10.5, 10.6]

    result = _evaluate_strategy_execution(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"zp"}),
        zp_short_indices=[3],
    )

    assert result["exit_idx"] == 4
    assert result["exit_reason"] == "TV-ZP short卖点"


def test_or_trade_with_both_sources_uses_the_first_matching_exit():
    result = _evaluate_strategy_execution(
        "000001",
        _execution_frame(),
        signal_idx=0,
        sources=frozenset({"ma", "zp"}),
        zp_short_indices=[1],
    )

    assert result["filled"] is True
    assert result["exit_reason"] == "TV-ZP short卖点"
    assert result["exit_idx"] == 2


def test_or_trade_keeps_fixed_nine_percent_protective_stop():
    frame = _execution_frame()
    frame.loc[2, "开盘"] = 9.4
    frame.loc[2, "最低"] = 9.0
    result = _evaluate_strategy_execution(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"ma", "zp"}),
        zp_short_indices=[3],
    )

    assert result["filled"] is True
    assert result["exit_reason"] == "固定止损"
    assert result["return_pct"] < -9.0


def test_chart_comparison_can_ignore_confirmation_gap_without_disabling_limit_rules():
    frame = _execution_frame()
    frame.loc[1, ["开盘", "最高", "最低", "收盘"]] = [10.5, 10.7, 10.4, 10.6]

    system = _evaluate_strategy_execution(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"zp"}),
        zp_short_indices=[3],
    )
    chart = _evaluate_strategy_execution(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"zp"}),
        zp_short_indices=[3],
        max_open_gap_pct=100.0,
    )

    assert system["filled"] is False
    assert chart["filled"] is True


def test_pure_chart_comparison_can_disable_the_protective_stop():
    frame = _execution_frame()
    frame.loc[2, "开盘"] = 9.4
    frame.loc[2, "最低"] = 9.0
    result = _evaluate_strategy_execution(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"zp"}),
        zp_short_indices=[3],
        stop_loss_pct=None,
    )

    assert result["filled"] is True
    assert result["exit_reason"] == "TV-ZP short卖点"
    assert result["exit_idx"] == 4


def test_chart_ma_target_uses_the_displayed_signal_close_basis():
    frame = _execution_frame()
    frame.loc[1, ["开盘", "最高", "最低", "收盘"]] = [10.5, 10.7, 10.4, 10.6]
    system = _evaluate_strategy_execution(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"ma"}),
        zp_short_indices=[],
        max_open_gap_pct=100.0,
    )
    chart = _evaluate_strategy_execution(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"ma"}),
        zp_short_indices=[],
        max_open_gap_pct=100.0,
        ma_target_basis_price=10.0,
    )

    assert system["exit_reason"] == "均线策略EMA20破位卖点"
    assert chart["exit_reason"] == "均线策略止盈信号(+15%)"
    assert chart["exit_idx"] == 3


def test_kline_replay_defaults_to_tv_or_events():
    from core.a_grade_kline_replay import replay_stock, run_kline_replay

    assert inspect.signature(replay_stock).parameters["require_both"].default is False
    assert inspect.signature(run_kline_replay).parameters["require_both"].default is False


def test_strategy_exit_search_starts_after_delayed_entry_anchor():
    result = _evaluate_strategy_execution(
        "000001",
        _execution_frame(),
        signal_idx=0,
        entry_anchor_idx=2,
        sources=frozenset({"zp"}),
        zp_short_indices=[1, 3],
    )

    assert result["filled"] is True
    assert result["entry_idx"] == 3
    assert result["exit_idx"] == 4
    assert result["exit_reason"] == "TV-ZP short卖点"
    assert result["hold_days"] == 1


def test_trade_without_strategy_sell_point_remains_open_and_is_not_scored():
    frame = _execution_frame()
    frame["最高"] = 10.5
    frame["最低"] = 9.5
    frame["收盘"] = 10.2
    frame["EMA20"] = 9.8
    result = _evaluate_strategy_execution(
        "000001",
        frame,
        signal_idx=0,
        sources=frozenset({"zp"}),
        zp_short_indices=[],
    )

    assert result["mature"] is False
    assert result["filled"] is True
    assert result["return_pct"] is None
    assert result["exit_reason"] == "策略卖点尚未出现"


def test_replay_keeps_raw_buy_markers_for_gate_specific_position_state(monkeypatch):
    import core.a_grade_kline_replay as replay

    rows = 150
    dates = pd.date_range("2025-01-01", periods=rows)
    prices = pd.DataFrame(
        {
            "日期": dates,
            "开盘": [10.0] * rows,
            "最高": [10.5] * rows,
            "最低": [9.5] * rows,
            "收盘": [10.0] * rows,
            "成交量": [1_000_000] * rows,
            "name": ["样本"] * rows,
            "industry": ["测试"] * rows,
        }
    )
    market = pd.DataFrame({"日期": dates, "收盘": [1000.0] * rows, "offensive": [True] * rows})

    def fake_indicators(frame, bench_df):
        result = frame.copy()
        result["Vol_MA20"] = 500_000
        result["EMA20"] = 9.8
        return result

    monkeypatch.setattr(replay, "calculate_indicators", fake_indicators)
    monkeypatch.setattr(replay, "_find_squeeze_signal_indices", lambda *args, **kwargs: [120, 122, 130])
    monkeypatch.setattr(replay, "_find_tv_zp_signal_indices", lambda frame: ([], [], {}))
    monkeypatch.setattr(
        replay,
        "analyze_price_action",
        lambda frame: {"price_action_score": 60, "pa_trade_plan": {"action": "WAIT"}},
    )
    monkeypatch.setattr(
        replay,
        "_evaluate_strategy_execution",
        lambda code, frame, signal_idx, sources, zp_short_indices, **kwargs: {
            "mature": True,
            "filled": True,
            "return_pct": 1.0,
            "exit_reason": "测试卖点",
            "entry_idx": signal_idx + 1,
            "exit_idx": signal_idx + 5,
            "hold_days": 4,
        },
    )

    result = replay_stock(
        "000001",
        prices,
        market,
        require_both=False,
        include_chart_comparison=True,
    )

    assert [row["signal_date"] for row in result] == [dates[120], dates[122], dates[130]]
    assert result[0]["exec_entry_date"] == dates[121]
    assert result[0]["exec_exit_date"] == dates[125]
    assert result[0]["chart_protected_exec_return_pct"] == 1.0
    assert result[0]["chart_points_only_exec_return_pct"] == 1.0


def test_route_a_protocol_events_apply_pa_and_market_variants_before_position_state():
    dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
    signals = pd.DataFrame(
        {
            "code": ["000001"] * 3,
            "signal_idx": [120, 121, 122],
            "signal_date": dates,
            "pa_action": ["BUY"] * 3,
            "pa_score": [56.0, 61.0, 66.0],
            "pct_5d": [10.0] * 3,
            "exec_filled": [False] * 3,
            "exec_return_pct": [None] * 3,
            "exec_exit_idx": [None] * 3,
            "exec_exit_date": [None] * 3,
        }
    )
    dual_axis = pd.DataFrame(
        {
            "date": dates,
            "route_a_permission_1d": ["CONFIRM", "CONFIRM", "BLOCKED"],
            "route_a_permission_2d": ["OBSERVE", "CONFIRM", "BLOCKED"],
        }
    )

    result = build_route_a_protocol_events(signals, dual_axis)

    counts = result.groupby("variant").size().to_dict()
    assert counts["pa55_confirm1d"] == 2
    assert counts["pa60_confirm1d"] == 1
    assert counts["pa55_confirm2d"] == 1
    assert counts["pa60_confirm2d"] == 1
    assert "pa65_confirm1d" not in counts


def test_portfolio_events_use_executed_x1_hard_stop_and_clip_future_exit():
    selected = pd.DataFrame(
        {
            "code": ["000001"],
            "industry": ["半导体"],
            "pa_score": [65.0],
            "raw_score": [85.0],
            "breadth_stage": ["ADVANCE"],
            "pa_stop_price": [9.2],
            "portfolio_exec_filled": [True],
            "portfolio_exec_entry_date": pd.to_datetime(["2026-01-02"]),
            "portfolio_exec_exit_date": pd.to_datetime(["2026-04-01"]),
            "portfolio_exec_entry_price": [10.0],
            "portfolio_exec_exit_price": [12.0],
            "portfolio_test_end": pd.to_datetime(["2026-03-31"]),
        }
    )

    result = prepare_route_a_portfolio_events(selected)

    assert result.iloc[0]["entry_price"] == 10.0
    assert round(result.iloc[0]["stop_price"], 4) == 9.0955
    assert result.iloc[0]["r0_single_capital_pct"] == 15.0
    assert result.iloc[0]["market_capital_pct"] == 70.0
    assert pd.isna(result.iloc[0]["exit_date"])
    assert pd.isna(result.iloc[0]["exit_price"])


def test_gate_mask_applies_only_declared_kline_gates():
    frame = pd.DataFrame(
        {
            "pa_action": ["BUY", "AVOID", "WAIT"],
            "pa_score": [62, 80, 59],
            "pct_5d": [9, 2, 4],
            "raw_score": [89, 95, 92],
            "market_offensive": [True, True, False],
        }
    )
    gate = GateSpec(
        "candidate",
        min_pa_score=60,
        max_pct_5d=15,
        min_raw_score=88,
        require_non_avoid=True,
        require_offensive_market=True,
    )
    assert _gate_mask(frame, gate).tolist() == [True, False, False]


def test_summarize_gate_uses_filled_execution_returns():
    frame = pd.DataFrame(
        {
            "code": ["1", "2", "3"],
            "signal_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "exec_mature": [True, True, True],
            "exec_filled": [True, True, False],
            "exec_return_pct": [5.0, -2.0, None],
            "exec_exit_reason": ["持有期结束", "固定止损", None],
            "exec_hold_days": [8, 3, None],
        }
    )
    result = summarize_gate(frame, pd.Series(True, index=frame.index))
    assert result["signals"] == 3
    assert result["filled"] == 2
    assert result["win_rate"] == 50.0
    assert result["avg_return"] == 1.5
    assert result["profit_factor"] == 2.5
    assert result["avg_hold_days"] == 5.5
    assert result["exit_reasons"] == {"固定止损": 1, "持有期结束": 1}


def test_gate_report_does_not_select_tiny_validation_sample():
    frame = pd.DataFrame(
        {
            "code": ["1", "2", "3"],
            "signal_date": pd.to_datetime(["2024-01-01", "2025-01-01", "2026-01-01"]),
            "pa_action": ["BUY"] * 3,
            "pa_score": [80] * 3,
            "pct_5d": [1] * 3,
            "raw_score": [95] * 3,
            "market_offensive": [True] * 3,
            "exec_mature": [True] * 3,
            "exec_filled": [True] * 3,
            "exec_return_pct": [2.0] * 3,
            "exec_exit_reason": ["持有期结束"] * 3,
        }
    )
    report = build_gate_report(frame)
    assert report["status"] == "NO_SUPPORTED_GATE"
    assert report["selected_gate"] is None


def test_fixed_split_support_does_not_claim_e3_without_walk_forward():
    rows = []
    for signal_date, count in (("2024-01-01", 60), ("2025-01-01", 40), ("2026-01-01", 100)):
        for idx in range(count):
            rows.append(
                {
                    "code": f"{signal_date[:4]}-{idx}",
                    "signal_idx": 10,
                    "signal_date": signal_date,
                    "pa_action": "BUY",
                    "pa_score": 65,
                    "pct_5d": 5,
                    "raw_score": 95,
                    "market_offensive": True,
                    "exec_mature": True,
                    "exec_filled": True,
                    "exec_return_pct": 2.0,
                    "exec_exit_reason": "测试卖点",
                    "exec_exit_idx": 15,
                    "exec_hold_days": 4,
                }
            )

    report = build_gate_report(pd.DataFrame(rows))

    assert report["selected_gate"] is not None
    assert report["provisional_gate"] == report["selected_gate"]
    assert "maximize validation avg return" in report["selection_rule"]
    assert report["fixed_split_support"] is True
    assert report["test_confirmation"] is False
    assert report["status"] == "E3_NOT_REACHED"
    assert "24/6/3月走查前推尚未执行" in report["e3_blockers"]


def test_gate_report_applies_position_dedup_after_each_gate_filter():
    frame = pd.DataFrame(
        {
            "code": ["1", "1"],
            "signal_idx": [10, 12],
            "signal_date": pd.to_datetime(["2026-01-01", "2026-01-03"]),
            "pa_action": ["BUY", "BUY"],
            "pa_score": [40, 65],
            "pct_5d": [1, 1],
            "raw_score": [95, 95],
            "market_offensive": [True, True],
            "exec_mature": [True, True],
            "exec_filled": [True, True],
            "exec_return_pct": [-5.0, 5.0],
            "exec_exit_reason": ["固定止损", "测试卖点"],
            "exec_exit_idx": [20, 15],
            "exec_hold_days": [9, 2],
        }
    )

    report = build_gate_report(frame)

    baseline = report["gates"]["strict_signal_baseline"]["test"]
    pa60 = report["gates"]["pa_60"]["test"]
    assert baseline["signals"] == 1
    assert baseline["avg_return"] == -5.0
    assert pa60["signals"] == 1
    assert pa60["avg_return"] == 5.0
