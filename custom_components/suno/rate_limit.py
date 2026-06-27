"""Shared API rate limiter for Suno integration."""

from __future__ import annotations

import asyncio
import time


class SunoRateLimiter:
    """Throttle Suno API requests for a single account.

    Each config entry owns its own limiter so that a ``429`` against one
    account never throttles another. Concurrency, however, is bounded
    globally: when a shared ``concurrency_gate`` semaphore is supplied it
    is used as the acquire/release gate, so ``N`` accounts cannot all
    hammer Suno at once (for example on a Home Assistant restart). The
    throttle/backoff state stays per-instance (per account).
    """

    MAX_RETRY_AFTER = 300  # Cap Retry-After to 5 minutes

    def __init__(self, max_concurrent: int = 3, *, concurrency_gate: asyncio.Semaphore | None = None) -> None:
        self._semaphore = concurrency_gate if concurrency_gate is not None else asyncio.Semaphore(max_concurrent)
        self._throttle_until: float = 0
        self._throttle_lock = asyncio.Lock()
        self._total_429_count: int = 0

    async def acquire(self) -> None:
        """Acquire a request slot, waiting out any active throttle first.

        The per-account throttle is waited out BEFORE the shared concurrency
        slot is taken, so one account's 429 backoff never holds a global
        concurrency slot and stalls other accounts that share the gate.
        """
        while (wait := self._throttle_until - time.monotonic()) > 0:
            await asyncio.sleep(wait)
        await self._semaphore.acquire()

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
