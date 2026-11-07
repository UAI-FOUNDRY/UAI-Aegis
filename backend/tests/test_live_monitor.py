"""Live monitor: changes are pushed to the professor over a WebSocket within a moment."""

import asyncio
import contextlib
import json
import os
import socket
import time
import uuid

import pytest
import pytest_asyncio
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.models import Role
from app.services.live import RESYNC, LiveHub, issue_ticket, read_ticket

from .conftest import build_exam, start

REDIS_URL = os.environ.get("AEGIS_TEST_REDIS_URL")


@pytest_asyncio.fixture
async def ws_base(app):
    """Serve the test app on a real port (WebSockets need a real server)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning",
                                           timeout_graceful_shutdown=2))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started or task.done():
            break
        await asyncio.sleep(0.02)
    assert server.started, f"test server did not start on port {port}"
    yield f"ws://127.0.0.1:{port}"
    # Stop it. uvicorn waits for open connections to close, with no time limit; a WebSocket
    # left half-closed (seen on Windows) would hang the test run forever. Give it a moment,
    # then force it.
    server.should_exit = True
    try:
        await asyncio.wait_for(asyncio.shield(task), 3)
    except TimeoutError:
        server.force_exit = True
        await asyncio.wait_for(task, 5)


async def _ticket(api, headers, exam_id) -> str:
    r = await api.client.post(f"/api/v1/exams/{exam_id}/live-ticket", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["path"]


async def _next(ws, kind: str | None = None, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), max(0.01, deadline - time.monotonic())))
        if kind is None or msg["type"] == kind:
            return msg


async def _row_until(ws, student_email: str, status: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        msg = await _next(ws, timeout=max(0.01, deadline - time.monotonic()))
        if msg["type"] == "row" and msg["data"]["student"]["email"] == student_email \
                and msg["data"]["status"] == status:
            return msg


async def _close_code(ws) -> int:
    with pytest.raises(ConnectionClosed) as info:
        while True:
            await asyncio.wait_for(ws.recv(), 5)
    return info.value.rcvd.code


async def test_changes_are_pushed_as_they_happen(api, ws_base):
    w = await build_exam(api, n_students=2, policy={"max_strikes": 2})
    s1, s2 = w.students
    async with connect(ws_base + await _ticket(api, w.prof_h, w.exam_id)) as ws:
        snap = await _next(ws, "snapshot")
        assert snap["data"]["counts"] == {"not_started": 2, "disconnected": 0}

        t0 = time.monotonic()
        session_id = (await start(api, w, s1)).json()["session_id"]
        msg = await _row_until(ws, s1.user.email, "active")
        assert msg["change"] == "started" and time.monotonic() - t0 < 2
        assert msg["data"]["batch_code"] and msg["data"]["session_id"] == session_id

        base = f"/api/v1/student/sessions/{session_id}"
        await api.signed(s1, "POST", f"{base}/heartbeat", {})
        assert (await _row_until(ws, s1.user.email, "active"))["change"] == "connected"
        await api.signed(s1, "POST", f"{base}/events", {"events": [{"kind": "focus_lost"}]})
        strike = await _row_until(ws, s1.user.email, "active")
        assert strike["change"] == "strike" and strike["data"]["strikes"] == 1
        await api.signed(s1, "POST", f"{base}/events", {"events": [{"kind": "focus_lost"}]})
        locked = await _row_until(ws, s1.user.email, "locked")
        assert locked["change"] == "locked" and locked["data"]["lock_reason"] == "too_many_violations"
        assert locked["data"]["event_count"] == 2

        await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{session_id}/resume", headers=w.prof_h,
                              json={"extra_minutes": 5})
        assert (await _row_until(ws, s1.user.email, "active"))["change"] == "resumed"
        await api.signed(s1, "POST", f"{base}/finish")
        msg = await _row_until(ws, s1.user.email, "submitted")
        seen = list(msg["changes"])
        while msg["data"]["grading_status"] != "done":  # grading follows (may arrive in the same message)
            msg = await _next(ws, "row")
            seen += msg["changes"]
        assert seen[0] == "submitted" and "graded" in seen

        await api.client.post(f"/api/v1/exams/{w.exam_id}/close", headers=w.prof_h)
        snap = await _next(ws, "snapshot")
        assert snap["data"]["exam_status"] == "closed"
        assert snap["data"]["counts"]["submitted"] == 1 and snap["data"]["counts"]["not_started"] == 1


async def test_batch_filter_and_quiet_refresh(api, ws_base):
    api.state.settings.live_refresh_seconds = 0.3
    w = await build_exam(api, n_students=1)
    async with connect(ws_base + await _ticket(api, w.prof_h, w.exam_id) + "&batch_code=OTHER") as ws:
        assert (await _next(ws, "snapshot"))["data"]["rows"] == []
        await start(api, w, w.students[0])
        msg = await _next(ws)  # the student's row is filtered out; the next thing is a refresh
        assert msg["type"] == "snapshot" and msg["data"]["rows"] == []


async def test_tickets_are_single_use_and_bound_to_the_exam(api, ws_base):
    w = await build_exam(api, n_students=1)
    path = await _ticket(api, w.prof_h, w.exam_id)
    async with connect(ws_base + path) as ws:
        await _next(ws, "snapshot")
        async with connect(ws_base + path) as again:  # replayed ticket
            assert await _close_code(again) == 4401
    other = await build_exam(api, n_students=1)
    wrong_exam = path.replace(w.exam_id, other.exam_id)
    async with connect(ws_base + wrong_exam) as ws:
        assert await _close_code(ws) == 4401
    async with connect(f"{ws_base}/api/v1/exams/{w.exam_id}/live?ticket=forged.ticket") as ws:
        assert await _close_code(ws) == 4401
    async with connect(f"{ws_base}/api/v1/exams/{w.exam_id}/live") as ws:
        assert await _close_code(ws) == 4401

    # Students and professors without access cannot get a ticket at all
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/live-ticket", headers=w.students[0].headers)
    assert r.status_code == 403
    _, stranger = await api.staff(Role.professor)
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/live-ticket", headers=stranger)
    assert r.status_code == 404


async def test_signing_out_ends_the_stream(api, ws_base):
    api.state.settings.live_refresh_seconds = 0.3
    w = await build_exam(api, n_students=1)
    async with connect(ws_base + await _ticket(api, w.prof_h, w.exam_id)) as ws:
        await _next(ws, "snapshot")
        await api.client.post("/api/v1/auth/logout", headers=w.prof_h)
        assert await _close_code(ws) == 4403


async def test_foreign_origin_and_connection_limit(api, ws_base):
    w = await build_exam(api, n_students=1)
    api.state.settings.cors_origins = "https://portal.uai.test"
    with pytest.raises(InvalidStatus):
        async with connect(ws_base + await _ticket(api, w.prof_h, w.exam_id), origin="https://evil.example"):
            pass
    async with connect(ws_base + await _ticket(api, w.prof_h, w.exam_id), origin="https://portal.uai.test") as ws:
        await _next(ws, "snapshot")

    api.state.settings.live_max_connections_per_user = 1
    async with connect(ws_base + await _ticket(api, w.prof_h, w.exam_id)) as first:
        await _next(first, "snapshot")
        async with connect(ws_base + await _ticket(api, w.prof_h, w.exam_id)) as second:
            assert await _close_code(second) == 4429
    await asyncio.sleep(0.1)
    assert api.state.live.connections == {}


def test_ticket_tampering_and_expiry():
    key = b"k" * 32
    exam_id, session_id = uuid.uuid4(), uuid.uuid4()
    good = issue_ticket(key, exam_id, session_id, 30)
    parsed = read_ticket(key, good)
    assert parsed and parsed.exam_id == exam_id and parsed.auth_session_id == session_id
    assert read_ticket(b"x" * 32, good) is None
    body, mac = good.split(".")
    import base64
    raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode().replace(str(exam_id), str(uuid.uuid4()))
    forged = base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode() + "." + mac
    assert read_ticket(key, forged) is None
    assert read_ticket(key, issue_ticket(key, exam_id, session_id, -1)) is None
    assert read_ticket(key, "garbage") is None


async def test_slow_listener_gets_a_resync_instead_of_unbounded_memory():
    hub = LiveHub()
    async with hub.subscribe("exam") as queue:
        for i in range(2000):
            await hub.publish("exam", {"change": "event", "session_id": str(i)})
        assert queue.qsize() <= 500
        notes = [queue.get_nowait() for _ in range(queue.qsize())]
        assert {"change": RESYNC} in notes
    assert hub.watchers("exam") == 0


@pytest.mark.skipif(not REDIS_URL, reason="set AEGIS_TEST_REDIS_URL to test live updates through Redis")
async def test_redis_carries_updates_between_workers():
    from redis.asyncio import Redis

    worker_a, worker_b = Redis.from_url(REDIS_URL), Redis.from_url(REDIS_URL)
    hub_a, hub_b = LiveHub(worker_a), LiveHub(worker_b)
    try:
        async with hub_b.subscribe("exam-1") as queue:
            await hub_a.publish("exam-1", {"change": "locked", "session_id": "s1"})
            await hub_a.publish("exam-2", {"change": "locked", "session_id": "s2"})  # not watched by b
            assert await asyncio.wait_for(queue.get(), 5) == {"change": "locked", "session_id": "s1"}
            await asyncio.sleep(0.2)
            assert queue.empty()
    finally:
        for hub in (hub_a, hub_b):
            await hub.aclose()
        for r in (worker_a, worker_b):
            with contextlib.suppress(Exception):
                await r.aclose()
