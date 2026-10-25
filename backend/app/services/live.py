"""Live push for the professor's monitor.

Whenever a session changes (a student starts, gets a strike, is locked, hands in, is
graded...), the code that changed it calls ``notify()`` after committing. The hub passes
the note to every monitor WebSocket watching that exam, and each connection then reads
the fresh row from the database with its own permission check.

Notes carry only ids and a change name (never names, code or marks), so nothing private
passes through Redis.

- Development / one worker: notes are passed in memory.
- Production / several workers: notes go through Redis pub/sub, so a professor connected
  to worker A hears about a student whose request was handled by worker B.

A ticket (short-lived, single use, HMAC-signed) lets a browser open the WebSocket, since
browsers cannot send an Authorization header on WebSocket connections.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..state import AppState

log = logging.getLogger("aegis.live")

CHANNEL_PREFIX = "aegis:live:"
QUEUE_SIZE = 500
RESYNC = "resync"  # "something was missed, reload everything"

# Changes that affect the whole exam (send a full snapshot), not one row.
EXAM_CHANGES = {"exam_opened", "exam_closed", "regrading", RESYNC}


class LiveHub:
    def __init__(self, redis: Any = None) -> None:
        self._redis = redis
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._listener: asyncio.Task | None = None
        self._listening = asyncio.Event()
        self.connections: dict[uuid.UUID, int] = {}  # open monitor sockets per professor (this worker)

    # ---------------------------------------------------------------- publishing
    async def publish(self, exam_id: uuid.UUID | str, note: dict[str, Any]) -> None:
        if self._redis is not None:
            await self._redis.publish(CHANNEL_PREFIX + str(exam_id), json.dumps(note))
        else:
            self._deliver(str(exam_id), note)

    def _deliver(self, exam_id: str, note: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(exam_id, ())):
            try:
                queue.put_nowait(note)
            except asyncio.QueueFull:  # a stuck connection: drop its backlog, ask it to reload
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"change": RESYNC})

    def _deliver_all(self, note: dict[str, Any]) -> None:
        for exam_id in list(self._subscribers):
            self._deliver(exam_id, note)

    # --------------------------------------------------------------- subscribing
    @contextlib.asynccontextmanager
    async def subscribe(self, exam_id: uuid.UUID | str) -> AsyncIterator[asyncio.Queue]:
        key = str(exam_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.setdefault(key, set()).add(queue)
        try:
            if self._redis is not None:
                await self._ensure_listener()
            yield queue
        finally:
            subs = self._subscribers.get(key)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    del self._subscribers[key]

    def watchers(self, exam_id: uuid.UUID | str) -> int:
        return len(self._subscribers.get(str(exam_id), ()))

    async def _ensure_listener(self) -> None:
        if self._listener is None or self._listener.done():
            self._listening.clear()
            self._listener = asyncio.create_task(self._listen())
        await asyncio.wait_for(self._listening.wait(), timeout=5)

    async def _listen(self) -> None:
        """One Redis subscription per worker, shared by all its connections."""
        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.psubscribe(CHANNEL_PREFIX + "*")
                self._listening.set()
                async for message in pubsub.listen():
                    if message.get("type") != "pmessage":
                        continue
                    channel = message["channel"]
                    channel = channel.decode() if isinstance(channel, bytes) else channel
                    try:
                        note = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    self._deliver(channel[len(CHANNEL_PREFIX):], note)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("live updates: Redis subscription lost, reconnecting", exc_info=True)
                self._listening.clear()
                self._deliver_all({"change": RESYNC})
                await asyncio.sleep(1)
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()

    async def aclose(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._listener
            self._listener = None


async def notify(state: AppState, exam_id: uuid.UUID, change: str, session_id: uuid.UUID | None = None) -> None:
    """Tell anyone watching this exam's monitor that something changed. Call it after the
    database commit. Never raises: a failed push must not fail the student's request
    (the monitor also refreshes itself every few seconds)."""
    note: dict[str, Any] = {"change": change}
    if session_id is not None:
        note["session_id"] = str(session_id)
    try:
        await state.live.publish(exam_id, note)
    except Exception:
        log.warning("live update for exam %s could not be sent", exam_id, exc_info=True)


# ------------------------------------------------------------------------------ tickets

_TICKET_VERSION = "lt1"


def _ticket_key(audit_key: bytes) -> bytes:
    # A separate key derived from the audit key, so a ticket MAC can never be confused
    # with an audit-chain MAC.
    return hmac.new(audit_key, b"AEGIS-LIVE-TICKET-KEY-V1", hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class Ticket:
    exam_id: uuid.UUID
    auth_session_id: uuid.UUID
    expires_at: int
    nonce: str


def issue_ticket(audit_key: bytes, exam_id: uuid.UUID, auth_session_id: uuid.UUID, ttl_seconds: int) -> str:
    body = f"{_TICKET_VERSION}.{exam_id}.{auth_session_id}.{int(time.time()) + ttl_seconds}.{secrets.token_urlsafe(16)}"
    mac = hmac.new(_ticket_key(audit_key), body.encode(), hashlib.sha256).digest()
    return _b64(body.encode()) + "." + _b64(mac)


def read_ticket(audit_key: bytes, ticket: str) -> Ticket | None:
    """Signature and expiry only; the caller also marks the nonce used (single use)."""
    try:
        body_b64, mac_b64 = ticket.split(".")
        body = _unb64(body_b64)
        expected = hmac.new(_ticket_key(audit_key), body, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(mac_b64)):
            return None
        version, exam_id, session_id, expires, nonce = body.decode().split(".")
        if version != _TICKET_VERSION or int(expires) < time.time():
            return None
        return Ticket(uuid.UUID(exam_id), uuid.UUID(session_id), int(expires), nonce)
    except (ValueError, UnicodeDecodeError):
        return None
