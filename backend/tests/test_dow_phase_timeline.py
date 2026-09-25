"""道氏趋势阶段时间轴（build_price_action_annotations.phase_timeline）的回归测试。

覆盖：
1. phase_timeline 结构/连续性/与 summary 一致性；
2. 去抖折叠 _confirm_phase_segments：单日抖动段被过滤，首尾段豁免。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.price_action import (
    _confirm_phase_segments,
    build_price_action_annotations,
)


def _uptrend_df(n: int = 160) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 10 * np.cumprod(1 + rng.normal(0.004, 0.01, n))
    dates = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘": close * 0.995,
        "最高": close * 1.012,
        "最低": close * 0.988,
        "收盘": close,
        "成交量": rng.integers(8e6, 12e6, n).astype(float),
    })


def test_phase_timeline_structure_and_continuity():
    result = build_price_action_annotations(_uptrend_df(), lookback=25)
    timeline = result["phase_timeline"]
    assert isinstance(timeline, list) and timeline, "上升趋势应至少识别出一个阶段"
    for item in timeline:
        assert {"time", "phase", "action"} <= set(item)
        assert item["phase"] != "数据不足"
    phases = [item["phase"] for item in timeline]
    # 阶段时间轴只在确认的切换点记录，相邻两条必须不同
    assert all(a != b for a, b in zip(phases, phases[1:]))
    # 时间轴升序且落在重放窗口内
    times = [item["time"] for item in timeline]
    assert times == sorted(times)
    # 末段是当前实时阶段（豁免去抖），必须与 summary 一致
    assert timeline[-1]["phase"] == result["summary"]["pa_trend_phase"]


def test_short_dataframe_returns_empty_timeline():
    result = build_price_action_annotations(_uptrend_df(15))
    assert result["phase_timeline"] == []
    assert "markers" in result and "lines" in result


def test_phase_timeline_does_not_change_markers_contract():
    result = build_price_action_annotations(_uptrend_df(), lookback=20)
    assert isinstance(result["markers"], list)
    assert isinstance(result["lines"], list)
    for marker in result["markers"]:
        assert "time" in marker and "text" in marker


# ── 去抖折叠（_confirm_phase_segments）──

def _points(spec):
    """spec: [(phase, 持续bar数), ...] → 展开为逐bar点序列。"""
    points, day = [], 0
    for phase, bars in spec:
        for _ in range(bars):
            day += 1
            points.append({"time": f"2026-01-{day:02d}", "phase": phase, "action": ""})
    return points


def test_debounce_drops_interior_single_day_spikes():
    from core.price_action import PHASE_TIMELINE_MIN_HOLD_BARS

    assert PHASE_TIMELINE_MIN_HOLD_BARS >= 2
    # 拉升3天 → 衰竭段1天(噪音) → 拉升2天 → 加速段4天 → 趋势破坏1天(尾部豁免)
    raw = _points([
        ("第一波拉升", 3), ("衰竭段", 1), ("第一波拉升", 2), ("加速段", 4), ("趋势破坏", 1),
    ])
    confirmed = _confirm_phase_segments(raw, min_hold=3)
    phases = [item["phase"] for item in confirmed]
    # 单日的"衰竭段"和"趋势破坏"中间噪音被吸收；尾段(当前阶段)豁免保留
    assert phases == ["第一波拉升", "加速段", "趋势破坏"]
    assert confirmed[0]["time"] == "2026-01-01"
    # 折叠后相邻条目阶段必不相同（合并生效）
    assert all(a != b for a, b in zip(phases, phases[1:]))


def test_debounce_keeps_confirmed_switches_with_start_dates():
    from core.price_action import _confirm_phase_segments

    # 震荡2天(不足阈值→丢弃) → 加速段3天(确认) → 空头趋势2天(尾段豁免)
    raw = _points([("震荡观察", 2), ("加速段", 3), ("空头趋势", 2)])
    confirmed = _confirm_phase_segments(raw, min_hold=3)
    assert [(item["phase"], item["time"]) for item in confirmed] == [
        ("加速段", "2026-01-03"),     # 持续3天，确认切换，时间取段首
        ("空头趋势", "2026-01-06"),   # 尾段豁免：当前实时阶段
    ]


def test_debounce_never_produces_adjacent_duplicates():
    # 恢复型抖动：震荡2天(丢弃) → 衰竭段1天(丢弃) → 震荡3天(尾段豁免)
    raw = _points([("震荡观察", 2), ("衰竭段", 1), ("震荡观察", 3)])
    confirmed = _confirm_phase_segments(raw, min_hold=3)
    assert [item["phase"] for item in confirmed] == ["震荡观察"]
    assert confirmed[0]["time"] == "2026-01-04"


def test_confirmed_segment_not_swallowed_by_earlier_noise_same_phase():
    """回归：6根确认的衰竭段不得因与早先1根同名噪音段相隔多个丢弃段而被合并吞掉。

    对应线上实例 600667：窗口头部 1 根"衰竭段"噪音 + 7月顶部 6 根确认"衰竭段"，
    修复前后者被前者吞掉，导致最重要的顶部标注消失。
    """
    from core.price_action import _confirm_phase_segments

    raw = _points([
        ("衰竭段", 1), ("震荡观察", 1), ("第一波拉升", 1), ("首次回调", 1),
        ("衰竭段", 6), ("震荡观察", 4),
    ])
    confirmed = _confirm_phase_segments(raw, min_hold=3)
    assert [item["phase"] for item in confirmed] == ["衰竭段", "震荡观察"]
    assert confirmed[0]["time"] == "2026-01-05"   # 确认的衰竭段起点（不被吞、不被提前）
    assert confirmed[1]["time"] == "2026-01-11"


def test_same_phase_segments_separated_by_single_noise_segment_merge():
    from core.price_action import _confirm_phase_segments

    # 加速段4天 → 衰竭段1天(噪音) → 加速段3天：间隔恰一个抖动段 → 合并为一个切换点
    raw = _points([("加速段", 4), ("衰竭段", 1), ("加速段", 3)])
    confirmed = _confirm_phase_segments(raw, min_hold=3)
    assert [(item["phase"], item["time"]) for item in confirmed] == [("加速段", "2026-01-01")]


def test_min_hold_is_tunable():
    from core.price_action import _confirm_phase_segments

    raw = _points([("第一波拉升", 2), ("衰竭段", 2)])
    assert [item["phase"] for item in _confirm_phase_segments(raw, min_hold=2)] == [
        "第一波拉升", "衰竭段",
    ]
