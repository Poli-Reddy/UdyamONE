"""Small per-process safety limiter; production gateways must also rate limit."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class LocalRateLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, identity: str, bucket: str, limit: int, seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        key = (identity, bucket)
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= now - seconds:
                events.popleft()
            if len(events) >= limit:
                retry = max(1, round(seconds - (now - events[0])))
                return False, retry
            events.append(now)
            return True, 0


LIMITER = LocalRateLimiter()


LIMITS = {
    "/api/recommend": (60, 60),
    "/api/intent": (60, 60),
    "/api/orders": (10, 300),
    "/api/tts": (20, 60),
    "/api/bills/extract": (5, 300),
    "/api/kyc": (5, 300),
    "/api/auth/customer/request-otp": (5, 3600),
    "/api/auth/customer/verify-otp": (10, 900),
}
