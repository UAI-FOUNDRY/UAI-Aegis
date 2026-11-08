"""Exam-day controls: extra time (one student or everyone), the admins' blocked-app
list, and sign-in limits that do not lock a whole lab out."""

from __future__ import annotations

from datetime import datetime

from app.models import Role

from .conftest import PASSWORD, build_exam, start


def _t(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def test_professor_adds_time_for_one_student(api):
    w = await build_exam(api, n_students=2)
    s1, s2 = w.students
    v1 = (await start(api, w, s1)).json()
    v2 = (await start(api, w, s2)).json()
    sid = v1["session_id"]
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{sid}/extend", headers=w.prof_h,
                              json={"minutes": 15, "reason": "laptop restarted"})
    assert r.status_code == 200, r.text
    assert (_t(r.json()["deadline_at"]) - _t(v1["deadline_at"])).total_seconds() == 15 * 60
    # The student's app sees the new end time with its next heartbeat
    status = (await api.signed(s1, "POST", f"/api/v1/student/sessions/{sid}/heartbeat", {})).json()
    assert (_t(status["deadline_at"]) - _t(v1["deadline_at"])).total_seconds() == 15 * 60
    # Only that student
    other = (await api.signed(s2, "POST", f"/api/v1/student/sessions/{v2['session_id']}/heartbeat", {})).json()
    assert other["deadline_at"] == v2["deadline_at"]
    detail = (await api.client.get(f"/api/v1/exams/{w.exam_id}/sessions/{sid}", headers=w.prof_h)).json()
    (event,) = [e for e in detail["events"] if e["kind"] == "staff_extend"]
    assert event["details"]["extra_minutes"] == 15 and event["details"]["reason"] == "laptop restarted"

    # A paused student can get time too; a finished one cannot
    await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{sid}/lock", headers=w.prof_h, json={})
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{sid}/extend", headers=w.prof_h,
                              json={"minutes": 5})
    assert r.status_code == 200
    await api.signed(s2, "POST", f"/api/v1/student/sessions/{v2['session_id']}/finish")
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{v2['session_id']}/extend",
                              headers=w.prof_h, json={"minutes": 5})
    assert r.status_code == 409
    for bad in (0, 241):
        r = await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{sid}/extend", headers=w.prof_h,
                                  json={"minutes": bad})
        assert r.status_code == 422
    # Another professor without access cannot
    _, other_h = await api.staff(Role.professor)
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{sid}/extend", headers=other_h,
                              json={"minutes": 5})
    assert r.status_code in (403, 404)


async def test_professor_adds_time_for_everyone_still_working(api):
    w = await build_exam(api, n_students=4)
    s1, s2, s3, _not_started = w.students
    views = [(await start(api, w, s)).json() for s in (s1, s2, s3)]
    await api.client.post(f"/api/v1/exams/{w.exam_id}/sessions/{views[1]['session_id']}/lock", headers=w.prof_h,
                          json={})
    await api.signed(s3, "POST", f"/api/v1/student/sessions/{views[2]['session_id']}/finish")
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/extend", headers=w.prof_h,
                              json={"minutes": 10, "reason": "power cut"})
    assert r.status_code == 200 and r.json() == {"extended": 2, "minutes": 10}  # working + paused
    for view, student, moved in ((views[0], s1, True), (views[1], s2, True)):
        status = (await api.signed(student, "POST", f"/api/v1/student/sessions/{view['session_id']}/heartbeat",
                                   {})).json()
        assert ((_t(status["deadline_at"]) - _t(view["deadline_at"])).total_seconds() == 600) is moved
    await api.client.post(f"/api/v1/exams/{w.exam_id}/close", headers=w.prof_h)
    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/extend", headers=w.prof_h, json={"minutes": 10})
    assert r.status_code == 409


async def test_blocked_apps_are_managed_by_admins_and_sent_to_the_app(api):
    w = await build_exam(api, n_students=1)
    (s,) = w.students
    c = api.client
    r = await c.post("/api/v1/admin/blocked-apps", headers=w.admin,
                     json={"process": "WhatsApp.exe", "label": "WhatsApp"})
    assert r.status_code == 201 and r.json()["process"] == "whatsapp" and r.json()["action"] == "strike"
    r = await c.post("/api/v1/admin/blocked-apps", headers=w.admin,
                     json={"process": "ChatGPT", "label": "ChatGPT", "action": "lock"})
    assert r.status_code == 201
    r = await c.post("/api/v1/admin/blocked-apps", headers=w.admin, json={"process": "whatsapp", "label": "x"})
    assert r.status_code == 409
    r = await c.post("/api/v1/admin/blocked-apps", headers=w.prof_h, json={"process": "discord", "label": "x"})
    assert r.status_code == 403

    # Before Start (pre-check) and in every session view
    r = await c.get("/api/v1/student/blocked-apps", headers=s.headers)
    assert [(a["process"], a["action"]) for a in r.json()] == [("chatgpt", "lock"), ("whatsapp", "strike")]
    view = (await start(api, w, s)).json()
    assert {a["process"] for a in view["blocked_apps"]} == {"chatgpt", "whatsapp"}

    # A 'strike' app costs one strike; a 'lock' app pauses the exam
    base = f"/api/v1/student/sessions/{view['session_id']}"
    r = await api.signed(s, "POST", f"{base}/events",
                         {"events": [{"kind": "forbidden_app_detected", "details": {"process": "whatsapp"}}]})
    assert r.json()["strikes"] == 1 and r.json()["status"] == "active"
    r = await api.signed(s, "POST", f"{base}/events",
                         {"events": [{"kind": "blocked_app_detected", "details": {"process": "chatgpt"}}]})
    assert r.json()["status"] == "locked"

    listed = (await c.get("/api/v1/admin/blocked-apps", headers=w.admin)).json()
    for app in listed:
        assert (await c.delete(f"/api/v1/admin/blocked-apps/{app['id']}", headers=w.admin)).status_code == 204
    assert (await c.get("/api/v1/student/blocked-apps", headers=s.headers)).json() == []


async def test_a_lab_behind_one_address_is_not_locked_out_by_typos(api):
    settings = api.state.settings
    settings.login_attempts_per_ip = 3
    users = [await api.user(Role.student) for _ in range(5)]

    async def bad_login(email):
        return await api.client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-1"})

    # Without the campus network listed, the shared address is blocked after 3 typos...
    codes = [(await bad_login(u.email)).status_code for u in users]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
    r = await api.client.post("/api/v1/auth/login", json={"email": users[4].email, "password": PASSWORD})
    assert r.status_code == 429

    # ...with it, only each account's own limit applies
    settings.login_ip_exempt_networks = "127.0.0.0/8, ::1/128"
    more = [await api.user(Role.student) for _ in range(5)]
    codes = [(await bad_login(u.email)).status_code for u in more]
    assert codes == [401] * 5
    r = await api.client.post("/api/v1/auth/login", json={"email": more[0].email, "password": PASSWORD})
    assert r.status_code == 200
