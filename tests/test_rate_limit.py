"""Tests for the Suno rate limiter."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from custom_components.suno.rate_limit import SunoRateLimiter


async def test_acquire_release_basic() -> None:
    """Acquire and release work without error."""
    limiter = SunoRateLimiter(max_concurrent=2)
    await limiter.acquire()
    limiter.release()


async def test_semaphore_limits_concurrency() -> None:
    """Only N concurrent acquires are allowed."""
    limiter = SunoRateLimiter(max_concurrent=2)
    await limiter.acquire()
    await limiter.acquire()

    # Third acquire should block — use a short timeout to prove it
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(limiter.acquire(), timeout=0.05)

    # After releasing one slot, acquire succeeds
    limiter.release()
    await asyncio.wait_for(limiter.acquire(), timeout=0.1)

    # Clean up
    limiter.release()
    limiter.release()


async def test_report_rate_limit_sets_throttle() -> None:
    """After report_rate_limit, acquire sleeps for the throttle duration."""
    limiter = SunoRateLimiter(max_concurrent=3)

    delay = await limiter.report_rate_limit(retry_after=0.05)
    assert delay == pytest.approx(0.05, abs=0.01)
    assert limiter.is_throttled is True

    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.04  # Slept at least ~50ms
    limiter.release()


async def test_retry_after_capped_at_max() -> None:
    """Retry-After values greater than 300 are capped to MAX_RETRY_AFTER."""
    limiter = SunoRateLimiter()
    delay = await limiter.report_rate_limit(retry_after=999)
    assert delay == 300


async def test_is_throttled_property() -> None:
    """Returns True when throttled, False after expiry."""
    limiter = SunoRateLimiter()
    assert limiter.is_throttled is False

    await limiter.report_rate_limit(retry_after=0.05)
    assert limiter.is_throttled is True

    await asyncio.sleep(0.06)
    assert limiter.is_throttled is False


async def test_total_429_count() -> None:
    """Increments on each report_rate_limit call."""
    limiter = SunoRateLimiter()
    assert limiter.total_429_count == 0

    await limiter.report_rate_limit(retry_after=0.01)
    assert limiter.total_429_count == 1

    await limiter.report_rate_limit(retry_after=0.01)
    await limiter.report_rate_limit(retry_after=0.01)
    assert limiter.total_429_count == 3


async def test_seconds_remaining() -> None:
    """Returns correct remaining time."""
    limiter = SunoRateLimiter()
    assert limiter.seconds_remaining == 0.0

    await limiter.report_rate_limit(retry_after=10.0)
    remaining = limiter.seconds_remaining
    assert 9.0 < remaining <= 10.0

    # After throttle clears, remaining should be 0
    with patch("custom_components.suno.rate_limit.time.monotonic", return_value=time.monotonic() + 11):
        assert limiter.seconds_remaining == 0.0


async def test_report_rate_limit_default_retry_after() -> None:
    """When retry_after is None, default delay of 2.0 is used."""
    limiter = SunoRateLimiter()
    delay = await limiter.report_rate_limit(retry_after=None)
    assert delay == 2.0
