"""Violation policy: strikes, instant lock on severe events, professor resume/terminate,
switching laptops, and time-up while locked."""

import uuid
from datetime import timedelta

from app.models import ExamSession, utcnow
from app.security.signing import DeviceSigner
from app.services.exams import sweep_expired

from .conftest import build_exam, start


async def _session(api, w, s):
    view = (await start(api, w, s)).json()
    return view["session_id"], f"/api/v1/student/sessions/{view['session_id']}"


async def test_minor_events_lock_after_max_strikes(api):
    w = await build_exam(api, n_students=1, policy={"max_strikes": 3})
    (s,) = w.students
    sid, base = await _session(api, w, s)

    r = await api.signed(s, "POST", f"{base}/events", {"events": [{"kind": "focus_lost"}]})
    assert r.json()["status"] == "active" and r.json()["strikes_left"] == 2
    r = await api.signed(s, "POST", f"{base}/events", {"events": [{"kind": "paste_blocked"}]})  # info only
    assert r.json()["strikes"] == 1
    r = await api.signed(s, "POST", f"{base}/events",
                         {"events": [{"kind": "focus_lost"}, {"kind": "fullscreen_exited"}]})
    body = r.json()
    assert body["status"] == "locked" and body["lock_reason"] == "too_many_violations"

    # The server refuses work while locked, whatever the app does
    for method, path, payload in [
        ("PUT", f"{base}/answers/{w.question_id}", {"language": "python3", "code": "print(1)"}),
        ("POST", f"{base}/answers/{w.question_id}/run", {"language": "python3", "code": "print(1)"}),
        ("POST", f"{base}/finish", None),
    ]:
        r = await api.signed(s, method, path, payload)
        assert r.status_code == 423 and r.json()["detail"]["code"] == "session_locked"
    # Heartbeats still work so the monitor shows the student as connected
    r = await api.signed(s, "POST", f"{base}/heartbeat", {})
    assert r.status_code == 200 and r.json()["status"] == "locked"

    mon = (await api.client.get(f"/api/v1/exams/{w.exam_id}/monitor", headers=w.prof_h)).json()
    assert mon["counts"]["locked"] == 1 and mon["rows"][0]["connected"] is True

    # Professor resumes with 5 extra minutes; strikes reset
    before = (await api.signed(s, "GET", base)).json()["deadline_at"]
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{sid}/resume", headers=w.prof_h,
                              json={"extra_minutes": 5})
    assert r.status_code == 200 and r.json()["status"] == "active" and r.json()["strikes"] == 0
    after = (await api.signed(s, "GET", base)).json()["deadline_at"]
    assert after > before
    r = await api.signed(s, "PUT", f"{base}/answers/{w.question_id}", {"language": "python3", "code": "print(1)"})
    assert r.status_code == 200

    # Event timeline is visible to the professor
    detail = (await api.client.get(f"/api/v1/exams/{w.exam_id}/sessions/{sid}", headers=w.prof_h)).json()
    kinds = [e["kind"] for e in detail["events"]]
    assert kinds[:4] == ["focus_lost", "paste_blocked", "focus_lost", "fullscreen_exited"]
    assert kinds[-1] == "staff_resume" and detail["events"][3]["caused_lock"] is True


async def test_severe_event_locks_immediately_and_terminate(api):
    w = await build_exam(api, n_students=1)
    (s,) = w.students
    sid, base = await _session(api, w, s)
    await api.signed(s, "PUT", f"{base}/answers/{w.question_id}", {"language": "python3", "code": "print(5)"})
    r = await api.signed(s, "POST", f"{base}/events",
                         {"events": [{"kind": "remote_desktop_detected", "details": {"process": "anydesk"}}]})
    assert r.json()["status"] == "locked" and r.json()["lock_reason"] == "remote_desktop_detected"

    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{sid}/terminate", headers=w.prof_h,
                              json={"reason": "AnyDesk running during the exam"})
    assert r.status_code == 200 and r.json()["status"] == "terminated" and r.json()["flagged"] is True
    await api.state.tasks.drain()
    row = (await api.client.get(f"/api/v1/exams/{w.exam_id}/results", headers=w.prof_h)).json()[0]
    assert row["finish_reason"] == "terminated" and row["total"] == 2  # frozen code still graded


async def test_policy_can_make_severe_events_strikes(api):
    w = await build_exam(api, n_students=1, policy={"lock_on_severe": False, "max_strikes": 2,
                                                    "severity": {"focus_lost": "info"}})
    (s,) = w.students
    _, base = await _session(api, w, s)
    r = await api.signed(s, "POST", f"{base}/events", {"events": [{"kind": "focus_lost"}, {"kind": "vm_detected"}]})
    assert r.json()["status"] == "active" and r.json()["strikes"] == 1


async def test_switching_laptops_needs_professor_approval(api):
    w = await build_exam(api, n_students=1)
    (s,) = w.students
    sid, base = await _session(api, w, s)
    await api.signed(s, "PUT", f"{base}/answers/{w.question_id}", {"language": "python3", "code": "print(42)"})

    # Same student, new laptop (e.g. battery died). The new device must register first.
    new_laptop = DeviceSigner()
    await api.register(s.headers, s.user, new_laptop)
    old_laptop = s.signer
    s.signer = new_laptop
    r = await start(api, w, s)
    view = r.json()
    assert view["status"] == "locked" and view["lock_reason"] == "device_changed"
    assert view["device_change_pending"] is True and view["questions"] == []  # nothing shown until approved
    r = await api.signed(s, "PUT", f"{base}/answers/{w.question_id}", {"language": "python3", "code": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "device_mismatch"

    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{sid}/resume", headers=w.prof_h,
                              json={"approve_new_device": True})
    assert r.status_code == 200
    view = (await api.signed(s, "GET", base)).json()
    assert view["status"] == "active" and view["questions"][0]["draft"]["code"] == "print(42)"

    # The old laptop is now locked out of this session
    s.signer = old_laptop
    r = await api.signed(s, "GET", base)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "device_mismatch"


async def test_locked_session_is_auto_submitted_at_time_up(api):
    w = await build_exam(api, n_students=1, policy={"grace_seconds": 0})
    (s,) = w.students
    sid, base = await _session(api, w, s)
    await api.signed(s, "PUT", f"{base}/answers/{w.question_id}",
                     {"language": "python3", "code": "a,b=map(int,input().split())\nprint(a+b)"})
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{sid}/lock", headers=w.prof_h,
                              json={"reason": "phone"})
    assert r.json()["status"] == "locked"
    async with api.state.sessionmaker() as db:
        session = await db.get(ExamSession, uuid.UUID(sid))
        session.deadline_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    assert await sweep_expired(api.state) == 1
    await api.state.tasks.drain()
    row = (await api.client.get(f"/api/v1/exams/{w.exam_id}/results", headers=w.prof_h)).json()[0]
    assert row["finish_reason"] == "locked_at_end" and row["flagged"] is True and row["total"] == 10
    # Too late to resume
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{sid}/resume", headers=w.prof_h, json={})
    assert r.status_code == 409


async def test_heartbeat_gap_is_recorded_not_punished(api):
    w = await build_exam(api, n_students=1, policy={"heartbeat_timeout_seconds": 10})
    (s,) = w.students
    sid, base = await _session(api, w, s)
    await api.signed(s, "POST", f"{base}/heartbeat", {})
    async with api.state.sessionmaker() as db:
        session = await db.get(ExamSession, uuid.UUID(sid))
        session.last_heartbeat_at = utcnow() - timedelta(seconds=60)
        await db.commit()
    r = await api.signed(s, "POST", f"{base}/heartbeat", {})
    assert r.json()["strikes"] == 0
    detail = (await api.client.get(f"/api/v1/exams/{w.exam_id}/sessions/{sid}", headers=w.prof_h)).json()
    assert detail["events"][0]["kind"] == "heartbeat_gap" and detail["events"][0]["severity"] == "info"


async def test_concurrent_events_do_not_lose_strikes(api):
    """Row locking: 10 events sent at the same moment must all count (PostgreSQL only;
    SQLite serialises writers differently)."""
    import asyncio

    import pytest

    if not api.state.settings.database_url.startswith("postgresql"):
        pytest.skip("needs PostgreSQL row locks")
    w = await build_exam(api, n_students=1, policy={"max_strikes": 50})
    (s,) = w.students
    _, base = await _session(api, w, s)
    responses = await asyncio.gather(*[
        api.signed(s, "POST", f"{base}/events", {"events": [{"kind": "focus_lost"}]}) for _ in range(10)
    ])
    assert all(r.status_code == 200 for r in responses)
    r = await api.signed(s, "POST", f"{base}/heartbeat", {})
    assert r.json()["strikes"] == 10
