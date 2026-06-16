from datetime import datetime

from core.risk_engine import compute_paper_risk_levels, track_high_since_entry


def test_risk_engine_uses_capital_protection_before_trailing():
    risk = compute_paper_risk_levels(29.15, 31.43, 31.43)

    assert risk["risk_stage"] == "保本保护"
    assert risk["initial_stop_price"] == 26.53
    assert risk["capital_protect_price"] == 29.44
    assert risk["moving_stop_price"] == 0.0
    assert risk["active_stop_price"] == 29.44


def test_risk_engine_activates_mid_trailing_after_ten_percent_profit():
    risk = compute_paper_risk_levels(10.0, 11.2, 11.0)

    assert risk["risk_stage"] == "移动风控"
    assert risk["moving_stop_price"] == 10.3
    assert risk["active_stop_price"] == 10.3


def test_risk_engine_filters_overwide_structure_stop():
    risk = compute_paper_risk_levels(
        10.0,
        10.0,
        10.2,
        {"pa_stop_price": 8.0, "pa_target_price": 12.0},
    )

    assert risk["structure_stop_price"] == 0.0
    assert risk["active_stop_price"] == 9.1
    assert risk["risk_notes"]


def test_same_day_entry_does_not_use_pre_entry_snapshot_high():
    high = track_high_since_entry(
        entry_price=34.64,
        stored_high=36.66,
        current_price=34.64,
        snapshot_high=36.66,
        entry_date="2026-06-13",
        now=datetime(2026, 6, 13, 18, 0),
    )

    assert high == 34.64


def test_later_day_entry_keeps_tracking_snapshot_high():
    high = track_high_since_entry(
        entry_price=34.64,
        stored_high=35.0,
        current_price=35.2,
        snapshot_high=36.66,
        entry_date="2026-06-12",
        now=datetime(2026, 6, 13, 18, 0),
    )

    assert high == 36.66


# ── ATR 自适应止损（改动 #8）──

def test_no_atr_keeps_fixed_stop():
    """不传 atr 时保持原 entry*0.91 行为（向后兼容）。"""
    risk = compute_paper_risk_levels(10.0, 10.0, 10.0)
    assert risk["initial_stop_price"] == 9.1
    assert risk["active_stop_price"] == 9.1


def test_atr_tightens_stop_for_low_volatility():
    """低波动股（小 ATR）的 ATR 止损应比固定 -9% 更紧（只收紧不放宽）。

    entry=10.0, ATR=0.3 → entry - 2.0*0.3 = 9.4，比固定 9.1 更高（更紧）。
    """
    risk = compute_paper_risk_levels(10.0, 10.0, 10.0, atr=0.3)
    assert risk["initial_stop_price"] == 9.4  # max(9.1, 9.4)
    assert risk["active_stop_price"] == 9.4
    assert any("ATR" in note for note in risk["risk_notes"])


def test_atr_never_widens_stop_beyond_fixed():
    """高波动股（大 ATR）的 ATR 止损不会比固定 -9% 更宽。

    entry=10.0, ATR=1.0 → entry - 2.0*1.0 = 8.0（更宽），但被 max(9.1, ...) 钳制为 9.1。
    这保证 ATR 只收紧、不放宽的安全语义。
    """
    risk = compute_paper_risk_levels(10.0, 10.0, 10.0, atr=1.0)
    assert risk["initial_stop_price"] == 9.1  # 固定止损胜出
    assert risk["active_stop_price"] == 9.1


def test_atr_clamps_within_bounds():
    """ATR 止损被 clamp 到 [-15%, -5%] 区间内。

    entry=10.0, ATR=0.1 → entry - 0.2 = 9.8（即 -2%），超过 -5% 上限，被钳为 9.5。
    """
    risk = compute_paper_risk_levels(10.0, 10.0, 10.0, atr=0.1)
    assert risk["initial_stop_price"] == 9.5  # clamp 到 -5%
