import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.execution_reachability import apply_execution_reachability, estimate_limit_up_price


def test_confirmation_above_daily_limit_is_unreachable_and_never_promoted():
    candidate = {
        "代码": "600671", "名称": "天目药业", "现价": 21.13, "涨幅%": 9.99,
        "pa_entry_price": 21.14, "trade_eligible": False, "trade_bucket": "OBSERVE",
        "trade_blockers": ["涨停/近涨停，等待隔日确认"],
    }

    summary = apply_execution_reachability([candidate])

    assert estimate_limit_up_price(candidate) == 21.13
    assert candidate["confirmation_reachability"] == "UNREACHABLE_TODAY"
    assert candidate["execution_review_state"] == "NEXT_DAY_REVIEW"
    assert candidate["trade_eligible"] is False
    assert summary["unreachable"] == 1


def test_reachability_can_only_lower_existing_trade_permission():
    candidate = {
        "代码": "301520", "名称": "万邦医药", "现价": 54.22, "涨幅%": 20.01,
        "pa_entry_price": 54.22, "trade_eligible": True, "trade_bucket": "TRADE",
        "trade_blockers": [],
    }

    apply_execution_reachability([candidate])

    assert candidate["confirmation_reachability"] == "LIMIT_LOCKED_WAIT_NEXT_SESSION"
    assert candidate["trade_eligible"] is False
    assert candidate["trade_bucket"] == "OBSERVE"


def test_mainline_exception_is_shadow_only():
    candidate = {
        "代码": "000001", "名称": "主线核心", "现价": 10.0, "涨幅%": 2.0,
        "pa_entry_price": 10.1, "pa_risk_reward": 2.0,
        "trade_opportunity_score": 75, "trade_eligible": False, "trade_bucket": "OBSERVE",
        "sector_mainline": "MAIN", "sector_phase": "SECTOR_CONFIRM", "sector_role": "CORE",
        "pa_volume_confirmed": True, "trade_blockers": ["市场退潮，暂停新增仓位"],
    }

    summary = apply_execution_reachability([candidate])

    assert candidate["confirmation_reachability"] == "REACHABLE_TODAY"
    assert candidate["strong_exception_shadow"] is True
    assert candidate["trade_eligible"] is False
    assert candidate["execution_review_state"] == "STRONG_WATCH"
    assert summary["strong_exception_shadow"] == 1


def test_weak_sector_blocker_excludes_shadow_exception():
    candidate = {
        "代码": "000002", "名称": "弱板块", "现价": 10.0, "涨幅%": 2.0,
        "pa_entry_price": 10.1, "pa_risk_reward": 2.0,
        "trade_opportunity_score": 75, "trade_eligible": False, "trade_bucket": "OBSERVE",
        "sector_mainline": "MAIN", "sector_phase": "SECTOR_CONFIRM", "sector_role": "CORE",
        "pa_volume_confirmed": True,
        "trade_blockers": ["市场退潮，暂停新增仓位", "板块强度弱，禁止实盘"],
    }

    apply_execution_reachability([candidate])

    assert candidate["strong_exception_shadow"] is False


def test_reachability_uses_frozen_confirmation_instead_of_moving_daily_high():
    candidate = {
        "代码": "300759", "现价": 41.5, "涨幅%": 4.0,
        "execution_plan_frozen": True,
        "frozen_confirmation_price": 41.0, "frozen_stop_price": 38.0,
        "pa_entry_price": 43.0, "pa_stop_price": 40.0,
        "trade_eligible": False, "trade_bucket": "OBSERVE", "trade_blockers": [],
    }
    apply_execution_reachability([candidate])
    assert candidate["confirmation_reachability"] == "REACHABLE_TODAY"
    assert candidate["confirmation_reachability_reason"] == "确认价处于当日可达区间"
