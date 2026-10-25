"""Nobody can see or change anyone else's work, and stolen tokens or replayed requests fail."""

import time
import uuid

from sqlalchemy import select

from app.models import Answer, Role
from app.security.signing import DeviceSigner

from .conftest import build_exam, start


async def test_student_cannot_touch_another_students_session(api):
    w = await build_exam(api, n_students=2)
    a, b = w.students
    a_view = (await start(api, w, a)).json()
    await start(api, w, b)
    a_base = f"/api/v1/student/sessions/{a_view['session_id']}"
    await api.signed(a, "PUT", f"{a_base}/answers/{w.question_id}", {"language": "python3", "code": "print('A')"})

    # B, with a valid token and a valid device signature, gets 404 on A's session
    for method, path, payload in [
        ("GET", a_base, None),
        ("PUT", f"{a_base}/answers/{w.question_id}", {"language": "python3", "code": "print('B')"}),
        ("POST", f"{a_base}/events", {"events": [{"kind": "vm_detected"}]}),
        ("POST", f"{a_base}/finish", None),
    ]:
        r = await api.signed(b, method, path, payload)
        assert r.status_code == 404, (method, path, r.text)
    view = (await api.signed(a, "GET", a_base)).json()
    assert view["status"] == "active" and view["questions"][0]["draft"]["code"] == "print('A')"


async def test_swapped_ciphertext_is_detected(api):
    w = await build_exam(api, n_students=2)
    a, b = w.students
    a_sid = (await start(api, w, a)).json()["session_id"]
    b_sid = (await start(api, w, b)).json()["session_id"]
    for s, sid, code in ((a, a_sid, "print('A')"), (b, b_sid, "print('B')")):
        await api.signed(s, "PUT", f"/api/v1/student/sessions/{sid}/answers/{w.question_id}",
                         {"language": "python3", "code": code})
    # Simulate someone with database access swapping the two encrypted answers
    async with api.state.sessionmaker() as db:
        ans = {str(x.session_id): x for x in (await db.scalars(select(Answer))).all()}
        ans[a_sid].code_enc, ans[b_sid].code_enc = ans[b_sid].code_enc, ans[a_sid].code_enc
        await db.commit()
    r = await api.signed(a, "GET", f"/api/v1/student/sessions/{a_sid}")
    assert r.status_code == 500 and r.json()["detail"]["code"] == "integrity_error"


async def test_signature_is_required_and_bound_to_the_device(api):
    w = await build_exam(api, n_students=2)
    a, b = w.students
    path = f"/api/v1/student/exams/{w.exam_id}/start"
    # No signature (e.g. a script with a copied token)
    r = await api.client.post(path, headers=a.headers, json={"access_code": w.access_code})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "signature_required"
    # A's token with B's registered device
    r = await api.client.post(path, content=b"{}", headers={**a.headers, **b.signer.headers("POST", path, b"{}"),
                                                             "Content-Type": "application/json"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unknown_device"
    # An unregistered key claiming A's device id
    rogue = DeviceSigner()
    rogue.device_id = a.signer.device_id
    r = await api.client.post(path, content=b"{}", headers={**a.headers, **rogue.headers("POST", path, b"{}"),
                                                             "Content-Type": "application/json"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "bad_signature"


async def test_replay_and_stale_requests_are_rejected(api):
    w = await build_exam(api, n_students=1)
    (s,) = w.students
    sid = (await start(api, w, s)).json()["session_id"]
    path = f"/api/v1/student/sessions/{sid}/heartbeat"
    body = b"{}"
    headers = {**s.headers, **s.signer.headers("POST", path, body), "Content-Type": "application/json"}
    assert (await api.client.post(path, content=body, headers=headers)).status_code == 200
    r = await api.client.post(path, content=body, headers=headers)  # exact replay
    assert r.status_code == 401 and r.json()["detail"]["code"] == "replayed_request"
    r = await api.signed(s, "POST", path, {}, timestamp=int(time.time()) - 3600)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "stale_signature"
    # Body changed after signing
    headers = {**s.headers, **s.signer.headers("POST", path, b"{}"), "Content-Type": "application/json"}
    r = await api.client.post(path, content=b'{"focused": false}', headers=headers)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "bad_signature"


async def test_students_see_no_hidden_tests_and_cannot_use_staff_api(api):
    w = await build_exam(api, n_students=1)
    (s,) = w.students
    view = (await start(api, w, s)).json()
    text = str(view)
    assert "1000000000" not in text and "-15" not in text  # hidden inputs/outputs never sent
    for path in (f"/api/v1/questions/{w.question_id}/tests", f"/api/v1/exams/{w.exam_id}/monitor",
                 "/api/v1/admin/users", f"/api/v1/subjects/{w.subject_id}"):
        assert (await api.client.get(path, headers=s.headers)).status_code == 403


async def test_unenrolled_student_cannot_see_exam(api):
    w = await build_exam(api, n_students=1)
    outsider = await api.student()
    r = await start(api, w, outsider)
    assert r.status_code == 404
    assert (await api.client.get("/api/v1/student/exams", headers=outsider.headers)).json() == []


async def test_professors_only_see_subjects_shared_with_them(api):
    w = await build_exam(api, n_students=1)
    other, other_h = await api.staff(Role.professor)
    c = api.client
    for path in (f"/api/v1/subjects/{w.subject_id}", f"/api/v1/subjects/{w.subject_id}/questions",
                 f"/api/v1/questions/{w.question_id}", f"/api/v1/exams/{w.exam_id}/monitor"):
        assert (await c.get(path, headers=other_h)).status_code == 404, path
    assert (await c.get("/api/v1/subjects", headers=other_h)).json() == []

    # Owner shares one exam as grader: other can see that exam's results but not the question pool
    r = await c.post(f"/api/v1/subjects/{w.subject_id}/access", headers=w.prof_h,
                     json={"email": other.email, "role": "grader", "exam_id": w.exam_id})
    assert r.status_code == 201, r.text
    grant_id = r.json()["id"]
    assert (await c.get(f"/api/v1/exams/{w.exam_id}/results", headers=other_h)).status_code == 200
    assert (await c.get(f"/api/v1/subjects/{w.subject_id}/questions", headers=other_h)).status_code == 404
    assert (await c.post(f"/api/v1/exams/{w.exam_id}/release-results", headers=other_h)).status_code == 403
    exams = (await c.get(f"/api/v1/subjects/{w.subject_id}/exams", headers=other_h)).json()
    assert [e["id"] for e in exams] == [w.exam_id] and exams[0]["access_code"] is not None

    # Revoked -> gone again
    assert (await c.delete(f"/api/v1/subjects/{w.subject_id}/access/{grant_id}", headers=w.prof_h)).status_code == 204
    assert (await c.get(f"/api/v1/exams/{w.exam_id}/results", headers=other_h)).status_code == 404

    # The last owner cannot be removed
    grants = (await c.get(f"/api/v1/subjects/{w.subject_id}/access", headers=w.prof_h)).json()
    owner_grant = next(g for g in grants if g["role"] == "owner")
    r = await c.delete(f"/api/v1/subjects/{w.subject_id}/access/{owner_grant['id']}", headers=w.prof_h)
    assert r.status_code == 400


async def test_admin_manages_but_cannot_read_content(api):
    w = await build_exam(api, n_students=1)
    c = api.client
    assert (await c.get(f"/api/v1/subjects/{w.subject_id}", headers=w.admin)).status_code == 200
    for path in (f"/api/v1/subjects/{w.subject_id}/questions", f"/api/v1/questions/{w.question_id}/tests",
                 f"/api/v1/exams/{w.exam_id}/results"):
        assert (await c.get(path, headers=w.admin)).status_code == 403, path
    # Grants can only go to professors
    student = w.students[0].user
    r = await c.post(f"/api/v1/subjects/{w.subject_id}/access", headers=w.admin,
                     json={"email": student.email, "role": "viewer"})
    assert r.status_code == 400


async def test_random_question_picking_differs_between_students(api):
    w = await build_exam(api, n_students=6, extra_questions=4)
    picked = set()
    for s in w.students:
        view = (await start(api, w, s)).json()
        assert len(view["questions"]) == 1
        picked.add(view["questions"][0]["id"])
    assert len(picked) > 1  # 6 students, 5 questions: identical papers would be ~1 in 7776


async def test_registering_someone_elses_key_fails(api):
    a = await api.student()
    b_user = await api.user(Role.student)
    b_headers = await api.login(b_user.email)
    r = await api.client.post("/api/v1/student/devices", headers=b_headers, json={
        "public_key": a.signer.public_key_b64, "proof": a.signer.registration_proof(b_user.id),
        "label": "x", "platform": "linux"})
    assert r.status_code == 409
    # A proof made for another user id is rejected
    signer = DeviceSigner()
    r = await api.client.post("/api/v1/student/devices", headers=b_headers, json={
        "public_key": signer.public_key_b64, "proof": signer.registration_proof(uuid.uuid4()),
        "label": "x", "platform": "linux"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_proof"
