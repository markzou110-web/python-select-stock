from core.rate_limiter import _parse_rate


def test_daily_rate_limit_is_supported():
    capacity, refill_rate = _parse_rate("30/day")

    assert capacity == 30
    assert refill_rate == 30 / 86400
