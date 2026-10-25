"""Process-wide objects shared by the API and background jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .config import Settings
from .grading.executor import Executor
from .security.crypto import FieldCipher
from .security.stores import NonceStore, RateLimiter


class TaskRunner:
    """Keeps references to background tasks (so they are not garbage-collected) and lets
    tests wait for them."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    cipher: FieldCipher
    executor: Executor
    limiter: RateLimiter
    nonces: NonceStore
    redis: Any = None
    tasks: TaskRunner = field(default_factory=TaskRunner)
    # At most this many sessions are graded at once (Judge0 has its own concurrency limit too).
    grading_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4))
