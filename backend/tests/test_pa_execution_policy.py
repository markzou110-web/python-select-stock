from core.pa_execution_policy import classify_price_action_execution


def test_price_action_execution_score_bands_are_explicit():
    normal = classify_price_action_execution({"price_action_score": 75, "pa_trade_action": "READY"})
    t1 = classify_price_action_execution({"price_action_score": 65, "pa_trade_action": "WATCH"})
    pullback = classify_price_action_execution({"price_action_score": 55, "pa_trade_action": "WAIT"})
    blocked = classify_price_action_execution({"price_action_score": 49, "pa_trade_action": "READY"})

    assert normal["tier"] == "NORMAL"
    assert t1["tier"] == "T1_CONFIRM"
    assert t1["hard_blocked"] is False
    assert pullback["tier"] == "PULLBACK_WATCH"
    assert pullback["hard_blocked"] is False
    assert blocked["tier"] == "HARD_BLOCK"
    assert blocked["hard_blocked"] is True


def test_avoid_invalidated_and_low_quality_setups_remain_hard_blocks():
    avoid = classify_price_action_execution({"price_action_score": 90, "pa_trade_action": "AVOID"})
    invalidated = classify_price_action_execution({
        "price_action_score": 90, "pa_trade_action": "READY",
        "pa_pullback_status": "INVALIDATED",
    })
    low_quality = classify_price_action_execution({
        "price_action_score": 90, "pa_trade_action": "READY",
        "pa_trade_setup": "外包K",
    })

    assert avoid["hard_blocked"] is True
    assert invalidated["hard_blocked"] is True
    assert low_quality["hard_blocked"] is True


def test_discovery_only_candidate_without_pa_stays_observation_not_hard_block():
    policy = classify_price_action_execution({
        "bottom_discovery_watch_only": True,
        "bottom_discovery_stage": "B1_REVERSAL",
    })

    assert policy["tier"] == "DISCOVERY_WATCH"
    assert policy["hard_blocked"] is False
