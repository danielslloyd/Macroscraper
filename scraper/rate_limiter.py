"""Per-adapter polite-delay rate limiter.

Default: random jitter between 1 and 3 seconds between requests. Each adapter
can override `min_delay` and `max_delay` on construction. The limiter is
asyncio-aware: callers `await limiter.wait()` before each request.
"""
from __future__ import annotations

import asyncio
import random
import time


class RateLimiter:
    def __init__(self, min_delay: float = 1.0, max_delay: float = 3.0) -> None:
        if min_delay < 0 or max_delay < min_delay:
            raise ValueError("invalid delay range")
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            target_gap = random.uniform(self.min_delay, self.max_delay)
            elapsed = now - self._last
            if elapsed < target_gap:
                await asyncio.sleep(target_gap - elapsed)
            self._last = time.monotonic()
