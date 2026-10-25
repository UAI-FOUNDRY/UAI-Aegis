"""Live monitor: the server pushes changes to the professor portal over a WebSocket.

    1. POST /api/v1/exams/{id}/live-ticket          (normal bearer request)
    2. open  ws(s)://server/api/v1/exams/{id}/live?ticket=...   within 30 s, once

Messages from the server (JSON text frames):

    {"type": "snapshot", "data": <same as GET /monitor>}
        right after connecting, every ~10 s, and after exam-wide changes (opened,
        closed, regrading). Replace everything you show with it.
    {"type": "row", "change": "locked", "changes": ["strike", "locked"],
     "server_now": "...", "data": <one MonitorRow>}
        within a fraction of a second of a student's change. Replace the row with the
        same student.id and recompute the counts.

Close codes: 4401 ticket missing/expired/used (get a new ticket and reconnect),
4403 no access (stop), 4429 too many open monitors, 1012 server restarting (reconnect).
The client never needs to send anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import too_many
from ...models import AuthSession, Exam, ExamSession, Role, User, utcnow
from ...services.authz import Cap, caps_for
from ...services.live import EXAM_CHANGES, issue_ticket, read_ticket
from ...state import AppState
from ..deps import get_db, get_state, require_professor
from .exams import _exam_for, monitor_row, monitor_snapshot

router = APIRouter(tags=["live"])
log = logging.getLogger("aegis.live")

DEBOUNCE_SECONDS = 0.2  # gather a burst of changes into one round of updates


class TicketOut(BaseModel):
    ticket: str
    expires_in: int
    path: str  # append to ws(s)://<server>


@router.post("/exams/{exam_id}/live-ticket", response_model=TicketOut)
async def live_ticket(exam_id: uuid.UUID, request: Request, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    """A one-time ticket to open the live monitor WebSocket (browsers cannot send the
    Authorization header on a WebSocket). Valid for 30 seconds and for one connection."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.view)
    if not await state.limiter.hit(f"live-ticket:{user.id}", 30, 60):
        raise too_many("Too many live monitor connections. Wait a minute.")
    ttl = state.settings.live_ticket_seconds
    ticket = issue_ticket(state.settings.audit_key_bytes, exam.id, request.state.auth_session.id, ttl)
    return TicketOut(ticket=ticket, expires_in=ttl, path=f"/api/v1/exams/{exam.id}/live?ticket={ticket}")


async def _allowed(db: AsyncSession, auth_session_id: uuid.UUID, exam_id: uuid.UUID) -> tuple[User, Exam] | None:
    """The same checks as a normal request: signed in, active professor, may view this exam.
    Re-run on every refresh, so signing out or losing access ends the stream."""
    auth = await db.get(AuthSession, auth_session_id, populate_existing=True)
    if auth is None or auth.revoked_at is not None or auth.expires_at <= utcnow():
        return None
    user = await db.get(User, auth.user_id, populate_existing=True)
    if user is None or not user.is_active or user.role != Role.professor or user.must_change_password:
        return None
    exam = await db.get(Exam, exam_id, populate_existing=True)
    if exam is None or Cap.view not in await caps_for(db, user, exam.subject_id, exam.id):
        return None
    return user, exam


async def _send(websocket: WebSocket, message: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(message, default=str))


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    """Read (and ignore) anything the client sends, until it goes away."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


@router.websocket("/exams/{exam_id}/live")
async def live_monitor(websocket: WebSocket, exam_id: uuid.UUID, ticket: str = Query("", max_length=512),
                       batch_code: str | None = Query(None, max_length=64)):
    state: AppState = websocket.app.state.aegis
    settings = state.settings
    origin = websocket.headers.get("origin")
    if origin and settings.cors_origin_list and origin not in settings.cors_origin_list:
        await websocket.close(code=4403)  # a page on another site; refused before the handshake
        return
    await websocket.accept()

    parsed = read_ticket(settings.audit_key_bytes, ticket)
    if parsed is None or parsed.exam_id != exam_id or not await state.nonces.use(
            f"live-ticket:{parsed.nonce}", settings.live_ticket_seconds + 5):
        await websocket.close(code=4401, reason="ticket_invalid")
        return
    async with state.sessionmaker() as db:
        access = await _allowed(db, parsed.auth_session_id, exam_id)
    if access is None:
        await websocket.close(code=4403, reason="forbidden")
        return
    user_id = access[0].id
    hub = state.live
    if hub.connections.get(user_id, 0) >= settings.live_max_connections_per_user:
        await websocket.close(code=4429, reason="too_many_connections")
        return

    hub.connections[user_id] = hub.connections.get(user_id, 0) + 1
    disconnected = asyncio.create_task(_wait_for_disconnect(websocket))
    try:
        async with hub.subscribe(exam_id) as queue:
            await _send_snapshot(state, websocket, exam_id, batch_code)
            while True:
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({getter, disconnected}, timeout=settings.live_refresh_seconds,
                                             return_when=asyncio.FIRST_COMPLETED)
                if disconnected in done:
                    getter.cancel()
                    return
                if getter not in done:  # quiet period: refresh everything and re-check access
                    getter.cancel()
                    async with state.sessionmaker() as db:
                        if await _allowed(db, parsed.auth_session_id, exam_id) is None:
                            await websocket.close(code=4403, reason="forbidden")
                            return
                    await _send_snapshot(state, websocket, exam_id, batch_code)
                    continue
                notes = [getter.result()]
                await asyncio.sleep(DEBOUNCE_SECONDS)
                while not queue.empty():
                    notes.append(queue.get_nowait())
                await _send_changes(state, websocket, exam_id, batch_code, notes)
    except (WebSocketDisconnect, RuntimeError, OSError):
        return  # the client left while we were sending
    finally:
        disconnected.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await disconnected
        remaining = hub.connections.get(user_id, 1) - 1
        if remaining > 0:
            hub.connections[user_id] = remaining
        else:
            hub.connections.pop(user_id, None)


async def _send_snapshot(state: AppState, websocket: WebSocket, exam_id: uuid.UUID, batch_code: str | None) -> None:
    async with state.sessionmaker() as db:
        exam = await db.get(Exam, exam_id)
        if exam is None:
            await websocket.close(code=4403, reason="forbidden")
            return
        snapshot = await monitor_snapshot(db, exam, batch_code)
    await _send(websocket, {"type": "snapshot", "data": snapshot.model_dump(mode="json")})


async def _send_changes(state: AppState, websocket: WebSocket, exam_id: uuid.UUID, batch_code: str | None,
                        notes: list[dict[str, Any]]) -> None:
    if any(n.get("change") in EXAM_CHANGES for n in notes):
        await _send_snapshot(state, websocket, exam_id, batch_code)
        return
    changes: dict[str, list[str]] = {}  # session id -> changes, in order
    for note in notes:
        if note.get("session_id"):
            changes.setdefault(note["session_id"], []).append(str(note.get("change", "")))
    if not changes:
        return
    messages = []
    async with state.sessionmaker() as db:
        exam = await db.get(Exam, exam_id)
        for session_id, kinds in changes.items():
            try:
                session = await db.get(ExamSession, uuid.UUID(session_id))
            except ValueError:
                continue
            if exam is None or session is None or session.exam_id != exam.id:
                continue
            row = await monitor_row(db, exam, session)
            if batch_code and row.batch_code != batch_code:
                continue
            messages.append({"type": "row", "change": kinds[-1], "changes": kinds,
                             "server_now": utcnow().isoformat(), "data": row.model_dump(mode="json")})
    for message in messages:
        await _send(websocket, message)
