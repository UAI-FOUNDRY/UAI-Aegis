"""The whole happy path: setup -> open -> students take the exam -> grading -> results."""

import uuid
from pathlib import Path

from sqlalchemy import select

from app.models import Answer, TestCase

from .conftest import build_exam, start


async def test_full_exam(api):
    w = await build_exam(api, n_students=2)
    c = api.client
    s1, s2 = w.students

    # Wrong code is refused, right code starts the exam
    r = await start(api, w, s1, code="WRONGCOD")
    assert r.status_code == 403
    r = await start(api, w, s1)
    assert r.status_code == 200, r.text
    view = r.json()
    session_id = view["session_id"]
    assert view["status"] == "active" and view["seconds_left"] > 3500
    (q,) = view["questions"]
    assert q["languages"][0]["monaco_id"] == "python"
    assert len(q["samples"]) == 2 and q["hidden_test_count"] == 3
    assert q["samples"][0]["expected_output"] == "5\n"
    assert q["starter_code"] == {"python3": "# write here\n"}

    base = f"/api/v1/student/sessions/{session_id}"
    qid = q["id"]
    # Autosave
    r = await api.signed(s1, "PUT", f"{base}/answers/{qid}", {"language": "python3", "code": "print(0)\n"})
    assert r.status_code == 200 and r.json()["revision"] == 1
    # Stale save is rejected
    r = await api.signed(s1, "PUT", f"{base}/answers/{qid}",
                         {"language": "python3", "code": "x", "base_revision": 0})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_revision"

    # Run: samples only, with outputs shown
    r = await api.signed(s1, "POST", f"{base}/answers/{qid}/run", {"language": "python3", "code": "print(5)\n"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["total"] == 2 and run["passed"] == 1
    assert run["tests"][0]["stdout"] == "5\n" and run["tests"][1]["verdict"] == "WA"

    # Submit: hidden results stay hidden by default
    correct = "a, b = map(int, input().split())\nprint(a + b)\n"
    r = await api.signed(s1, "POST", f"{base}/answers/{qid}/submit", {"language": "python3", "code": correct})
    assert r.status_code == 200, r.text
    sub = r.json()
    assert all(t["is_sample"] for t in sub["tests"])
    assert "graded after the exam" in sub["hidden_summary"]

    # Heartbeat returns the server's clock
    r = await api.signed(s1, "POST", f"{base}/heartbeat", {"focused": True, "fullscreen": True, "displays": 1})
    assert r.status_code == 200 and r.json()["status"] == "active"

    # Finish -> receipt per answer
    r = await api.signed(s1, "POST", f"{base}/finish")
    assert r.status_code == 200, r.text
    fin = r.json()
    assert fin["status"] == "submitted" and len(fin["receipts"][0]["receipt"]) == 64
    # No more writes after finishing
    r = await api.signed(s1, "PUT", f"{base}/answers/{qid}", {"language": "python3", "code": "print(1)"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "session_finished"

    # Student 2 starts and writes a wrong answer, then the professor closes the exam
    r = await start(api, w, s2)
    s2_session = r.json()["session_id"]
    await api.signed(s2, "PUT", f"/api/v1/student/sessions/{s2_session}/answers/{qid}",
                     {"language": "python3", "code": "print(5)\n"})
    r = await c.post(f"/api/v1/exams/{w.exam_id}/close", headers=w.prof_h)
    assert r.status_code == 200
    await api.state.tasks.drain()

    r = await c.get(f"/api/v1/exams/{w.exam_id}/results", headers=w.prof_h)
    assert r.status_code == 200
    totals = {row["student"]["email"]: row for row in r.json()}
    assert totals[s1.user.email]["total"] == 10 and totals[s1.user.email]["grading_status"] == "done"
    assert totals[s2.user.email]["total"] == 2  # 1 of 5 tests (2 3 -> 5)
    assert totals[s2.user.email]["finish_reason"] == "exam_closed"

    # Manual marks: override needs a note, and is audited
    r = await c.put(f"/api/v1/exams/{w.exam_id}/sessions/{s2_session}/answers/{qid}/marks",
                    headers=w.prof_h, json={"override_marks": 6})
    assert r.status_code == 400
    r = await c.put(f"/api/v1/exams/{w.exam_id}/sessions/{s2_session}/answers/{qid}/marks",
                    headers=w.prof_h, json={"override_marks": 6, "note": "Logic correct, output format wrong"})
    assert r.status_code == 200 and r.json()["total"] == 6

    # Results are hidden from students until released
    r = await c.get("/api/v1/student/results", headers=s2.headers)
    assert r.json() == []
    r = await c.post(f"/api/v1/exams/{w.exam_id}/release-results", headers=w.prof_h)
    assert r.status_code == 200
    r = await c.get("/api/v1/student/results", headers=s2.headers)
    assert r.json()[0]["total"] == 6

    # Session detail shows the professor code, events and run history
    r = await c.get(f"/api/v1/exams/{w.exam_id}/sessions/{session_id}", headers=w.prof_h)
    detail = r.json()
    assert detail["answers"][0]["code"] == correct
    kinds = [run["kind"] for run in detail["runs"]]
    assert kinds == ["run", "submit", "final"]
    run_id = detail["runs"][0]["id"]
    r = await c.get(f"/api/v1/exams/{w.exam_id}/sessions/{session_id}/runs/{run_id}", headers=w.prof_h)
    assert r.json()["code"] == "print(5)\n"


async def test_secrets_are_encrypted_at_rest(api):
    w = await build_exam(api, n_students=1)
    (s,) = w.students
    view = (await start(api, w, s)).json()
    await api.signed(s, "PUT", f"/api/v1/student/sessions/{view['session_id']}/answers/{w.question_id}",
                     {"language": "python3", "code": "print('MY-SECRET-ANSWER')"})
    async with api.state.sessionmaker() as db:
        answer = await db.scalar(select(Answer))
        assert b"MY-SECRET-ANSWER" not in answer.code_enc
        tests = (await db.scalars(select(TestCase))).all()
        assert all(b"1000000000" not in t.input_enc for t in tests)
    # And the raw database file never contains them either
    if api.state.settings.database_url.startswith("sqlite"):
        raw = Path(api.state.settings.database_url.split("///")[1]).read_bytes()
        assert b"MY-SECRET-ANSWER" not in raw


async def test_time_up_auto_submits(api):
    from datetime import timedelta

    from app.models import ExamSession, utcnow
    from app.services.exams import sweep_expired

    w = await build_exam(api, n_students=1, policy={"grace_seconds": 0})
    (s,) = w.students
    view = (await start(api, w, s)).json()
    base = f"/api/v1/student/sessions/{view['session_id']}"
    await api.signed(s, "PUT", f"{base}/answers/{w.question_id}",
                     {"language": "python3", "code": "a,b=map(int,input().split())\nprint(a+b)"})
    async with api.state.sessionmaker() as db:
        session = await db.get(ExamSession, uuid.UUID(view["session_id"]))
        session.deadline_at = utcnow() - timedelta(seconds=5)
        await db.commit()
    # Writes after the deadline are refused and trigger the auto-submit
    r = await api.signed(s, "PUT", f"{base}/answers/{w.question_id}", {"language": "python3", "code": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "time_over"
    assert await sweep_expired(api.state) == 0  # already finalised
    await api.state.tasks.drain()
    r = await api.client.get(f"/api/v1/exams/{w.exam_id}/results", headers=w.prof_h)
    row = r.json()[0]
    assert row["finish_reason"] == "time_up" and row["total"] == 10


async def test_readiness_blocks_bad_exam(api):
    w = await build_exam(api, n_students=1, open_exam=False)
    c = api.client
    # Break a test: wrong expected output typed by hand
    tests = (await c.get(f"/api/v1/questions/{w.question_id}/tests", headers=w.prof_h)).json()
    await c.patch(f"/api/v1/questions/{w.question_id}/tests/{tests[2]['id']}", headers=w.prof_h,
                  json={"expected_output": "999\n"})
    r = await c.get(f"/api/v1/exams/{w.exam_id}/readiness", headers=w.prof_h)
    assert r.json()["ready"] is False
    assert any("reference solution fails" in p for p in r.json()["problems"])
    r = await c.post(f"/api/v1/exams/{w.exam_id}/open", headers=w.prof_h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "exam_not_ready"
