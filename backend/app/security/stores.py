"""Rate limiting and replay (nonce) protection, backed by Redis in production and by
process memory in development and tests."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Protocol


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record one attempt; return False if the limit for the window is exceeded."""

    async def is_blocked(self, key: str, limit: int, window_seconds: int) -> bool:
        """True if `limit` attempts were already recorded in the window (records nothing)."""


class NonceStore(Protocol):
    async def use(self, key: str, ttl_seconds: int) -> bool:
        """Mark a nonce as used; return False if it was already used (a replay)."""


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    async def is_blocked(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            hits = self._hits.get(key, deque())
            return sum(1 for t in hits if t > now - window_seconds) >= limit


class MemoryNonceStore:
    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def use(self, key: str, ttl_seconds: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            if len(self._seen) > 50_000:
                self._seen = {k: exp for k, exp in self._seen.items() if exp > now}
            expires = self._seen.get(key)
            if expires is not None and expires > now:
                return False
            self._seen[key] = now + ttl_seconds
            return True


class RedisRateLimiter:
    """Fixed-window counter: INCR + EXPIRE in one round trip."""

    def __init__(self, redis) -> None:
        self._redis = redis

    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        window = int(time.time() // window_seconds)
        redis_key = f"aegis:rl:{key}:{window}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds + 1)
            count, _ = await pipe.execute()
        return int(count) <= limit

    async def is_blocked(self, key: str, limit: int, window_seconds: int) -> bool:
        window = int(time.time() // window_seconds)
        count = await self._redis.get(f"aegis:rl:{key}:{window}")
        return int(count or 0) >= limit


class RedisNonceStore:
    def __init__(self, redis) -> None:
        self._redis = redis

    async def use(self, key: str, ttl_seconds: int) -> bool:
        return bool(await self._redis.set(f"aegis:nonce:{key}", "1", nx=True, ex=ttl_seconds))
