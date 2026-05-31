from core.risk_engine import compute_paper_risk_levels


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
