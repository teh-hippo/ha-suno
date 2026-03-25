"""Shared API rate limiter for Suno integration."""

from __future__ import annotations

import asyncio
import time


class SunoRateLimiter:
    """Coordinates API request rate limiting across multiple config entries."""

    MAX_RETRY_AFTER = 300  # Cap Retry-After to 5 minutes

    def __init__(self, max_concurrent: int = 3) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._throttle_until: float = 0
        self._throttle_lock = asyncio.Lock()
        self._total_429_count: int = 0

    async def acquire(self) -> None:
        """Acquire a request slot, waiting for any active throttle to clear."""
        await self._semaphore.acquire()
        try:
            if self._throttle_until > 0:
                wait = self._throttle_until - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
        except BaseException:
            self._semaphore.release()
            raise

    def release(self) -> None:
        """Release a request slot."""
        self._semaphore.release()

    async def report_rate_limit(self, retry_after: float | None = None) -> float:
        """Record a 429 response and compute the backoff delay.

        Returns the delay in seconds.
        """
        async with self._throttle_lock:
            self._total_429_count += 1
            delay = min(retry_after or 2.0, self.MAX_RETRY_AFTER)
            self._throttle_until = max(self._throttle_until, time.monotonic() + delay)
            return delay

    @property
    def is_throttled(self) -> bool:
        """Return True if requests are currently throttled."""
        return self._throttle_until > 0 and time.monotonic() < self._throttle_until

    @property
    def seconds_remaining(self) -> float:
        """Return seconds until throttle clears."""
        remaining = self._throttle_until - time.monotonic()
        return max(0.0, remaining)

    @property
    def total_429_count(self) -> int:
        """Return the total number of 429 responses recorded."""
        return self._total_429_count
