"""
In-memory token-bucket rate limiter for FastAPI.

Uses a simple per-endpoint token bucket that does not require Redis.
Suitable for single-process deployments (which is our case with uvicorn).
"""

import time
from typing import Dict, Tuple

from core.logging_config import logger


class _TokenBucket:
    """A single token bucket for one rate-limit key."""

    __slots__ = ("capacity", "refill_rate", "tokens", "last_refill")

    def __init__(self, capacity: int, refill_rate: float):
        """
        Args:
            capacity: Maximum burst size.
            refill_rate: Tokens added per second.
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        """Try to consume one token.  Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


def _parse_rate(rate_str: str) -> Tuple[int, float]:
    """
    Parse a human-friendly rate string into (capacity, refill_rate).

    Supported formats:
        '10/minute'  -> capacity=10, refill_rate=10/60
        '1/hour'     -> capacity=1,  refill_rate=1/3600
        '5/second'   -> capacity=5,  refill_rate=5/1

    Returns:
        (capacity, tokens_per_second)
    """
    try:
        count_str, period = rate_str.strip().split("/")
        count = int(count_str)
    except (ValueError, AttributeError):
        logger.warning(f"Invalid rate format: {rate_str!r}, defaulting to 60/minute")
        return 60, 1.0

    period_seconds = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
    }.get(period.strip().lower(), 60)

    refill_rate = count / period_seconds
    return count, refill_rate


class RateLimiter:
    """
    In-memory rate limiter with per-path token buckets.

    Usage:
        limiter = RateLimiter()
        limiter.register("/api/scan", "10/minute")

        if not limiter.check("/api/scan"):
            return 429  # Too Many Requests
    """

    def __init__(self):
        self._buckets: Dict[str, _TokenBucket] = {}
        self._rules: Dict[str, str] = {}

    def register(self, path_prefix: str, rate: str) -> None:
        """
        Register a rate-limit rule for a path prefix.

        Args:
            path_prefix: URL path prefix to match (e.g. '/api/scan').
            rate: Rate string (e.g. '10/minute').
        """
        capacity, refill_rate = _parse_rate(rate)
        self._rules[path_prefix] = rate
        self._buckets[path_prefix] = _TokenBucket(capacity, refill_rate)
        logger.info(f"Rate limit registered: {path_prefix} -> {rate}")

    def check(self, path: str) -> bool:
        """
        Check whether a request to *path* is allowed.

        Returns True if allowed, False if rate-limited.
        """
        for prefix, bucket in self._buckets.items():
            if path.startswith(prefix):
                return bucket.consume()
        # No rule matched -> always allow
        return True

    def get_matching_rule(self, path: str) -> str:
        """Return the rate-limit rule string for a path, or empty string."""
        for prefix, rule in self._rules.items():
            if path.startswith(prefix):
                return rule
        return ""
