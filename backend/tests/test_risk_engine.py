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
    # 改动 #13 持仓侧：curr=11.0(+10%) 触发脉冲保护，spike_protect=10.67 > 移动风控 10.3，
    # 故 active_stop 收紧到 10.67（锁住更多脉冲利润，比移动风控更紧）。
    assert risk["spike_protect_price"] == round(11.0 * 0.97, 2)
    assert risk["active_stop_price"] == round(11.0 * 0.97, 2)


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


# ── 弱市止损收紧（改动 #11）──

def test_weak_regime_bear_tightens_stop():
    """bear 市时初始止损从 -9%（9.1）收紧到 -6%（9.4）。"""
    risk = compute_paper_risk_levels(10.0, 10.0, 10.0, market_regime="bear")
    assert risk["initial_stop_price"] == 9.4
    assert any("弱市" in note for note in risk["risk_notes"])


def test_weak_regime_volatile_tightens_stop():
    """volatile 市同样收紧到 -6%。"""
    risk = compute_paper_risk_levels(10.0, 10.0, 10.0, market_regime="volatile")
    assert risk["initial_stop_price"] == 9.4


def test_bull_regime_keeps_fixed_stop():
    """bull 市不收紧，保持 -9%。"""
    risk = compute_paper_risk_levels(10.0, 10.0, 10.0, market_regime="bull")
    assert risk["initial_stop_price"] == 9.1


def test_no_regime_keeps_fixed_stop():
    """未传 regime 时保持 -9%（向后兼容）。"""
    risk = compute_paper_risk_levels(10.0, 10.0, 10.0)
    assert risk["initial_stop_price"] == 9.1


# ── 保本移动止损（改动 #14）──
# 补全利润区间 [0%, 5%) 的止损空白：浮盈达 +3% 即把止损上移到成本线附近。

def test_breakeven_activates_at_3pct_profit():
    """改动 #14：浮盈达 +4%（max_pl=4%，curr 也 +2%）→ 止损上移到保本线（成本-0.5%）。

    原 [0%,5%) 区间无止损上移，股票从 +4% 回撤到 -9% 会损失 13%。
    """
    risk = compute_paper_risk_levels(10.0, 10.4, 10.2)  # entry=10, high=10.4(+4%), curr=10.2(+2%)
    assert risk["risk_stage"] == "保本移动"
    # 保本线 = 10 * (1 - 0.005) = 9.95，远高于初始 -9% 线 9.1
    assert risk["breakeven_price"] == 9.95
    assert risk["active_stop_price"] == 9.95
    assert risk["active_stop_price"] > 9.5  # 显著高于初始止损线


def test_breakeven_below_threshold_keeps_initial_stop():
    """浮盈仅 +2%（未达 +3% 阈值）→ 止损仍为初始 -9% 档，保本未触发。"""
    risk = compute_paper_risk_levels(10.0, 10.2, 10.1)  # max_pl=2%, curr=1%
    assert risk["breakeven_price"] == 0.0
    assert risk["risk_stage"] != "保本移动"
    assert risk["active_stop_price"] == 9.1  # 初始 -9% 线


def test_breakeven_upgrades_to_capital_protect_at_5pct():
    """浮盈达 +6%（超过 5% 的 capital_protect 阈值）→ 阶段升级到"保本保护"（更高档覆盖）。"""
    risk = compute_paper_risk_levels(10.0, 10.6, 10.5)  # max_pl=6%
    assert risk["risk_stage"] == "保本保护"
    assert risk["capital_protect_price"] == 10.1  # entry * 1.01
    assert risk["active_stop_price"] == 10.1  # 高档覆盖低档


# ── 持仓追高保护（改动 #13 持仓侧）──

def test_position_spike_protection_triggers_on_pullback():
    """浮盈 +8%（pl_pct）且已从高点回落（high > curr*1.01）→ 止损收紧到 curr*0.97。"""
    risk = compute_paper_risk_levels(10.0, 11.0, 10.8)  # entry=10, curr=10.8(+8%), high=11.0(>10.8*1.01)
    spike_price = round(10.8 * 0.97, 2)  # 10.48
    assert risk["spike_protect_price"] == spike_price
    # 由于 max_pl=10% 也触发了移动风控（high*0.92=10.12），active_stop 取 max
    # spike(10.48) > 移动风控(10.12)，故 active_stop 应为 10.48
    assert risk["active_stop_price"] == max(spike_price, risk["moving_stop_price"])


def test_position_spike_protection_not_triggered_in_one_sided_uptrend():
    """单边上涨（curr 接近 high，未回落）→ 不触发脉冲保护（避免砍掉主升浪）。"""
    risk = compute_paper_risk_levels(10.0, 10.9, 10.88)  # curr 接近 high，high < curr*1.01
    assert risk["spike_protect_price"] == 0.0
