import io

from sqlalchemy import delete, select

from app.models import AuditEntry, AuditHead, Role

from .conftest import PASSWORD, build_exam


async def test_login_errors_are_identical_and_rate_limited(api):
    user = await api.user(Role.professor)
    c = api.client
    r1 = await c.post("/api/v1/auth/login", json={"email": user.email, "password": "wrong-password"})
    r2 = await c.post("/api/v1/auth/login", json={"email": "nobody@uai.test", "password": "wrong-password"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json()
    for _ in range(10):
        r = await c.post("/api/v1/auth/login", json={"email": user.email, "password": "wrong-password"})
    assert r.status_code == 429


async def test_logout_revokes_token_and_inactive_users_are_out(api):
    _, admin_h = await api.staff(Role.admin)
    user, headers = await api.staff(Role.professor)
    c = api.client
    assert (await c.get("/api/v1/auth/me", headers=headers)).status_code == 200
    await c.post("/api/v1/auth/logout", headers=headers)
    assert (await c.get("/api/v1/auth/me", headers=headers)).status_code == 401
    headers = await api.login(user.email)
    await c.patch(f"/api/v1/admin/users/{user.id}", headers=admin_h, json={"is_active": False})
    assert (await c.get("/api/v1/auth/me", headers=headers)).status_code == 401
    assert (await c.get("/api/v1/auth/me", headers={"Authorization": "Bearer aegis_forged"})).status_code == 401


async def test_temporary_password_forces_change(api):
    admin, admin_h = await api.staff(Role.admin)
    c = api.client
    r = await c.post("/api/v1/admin/users", headers=admin_h, json={
        "email": "New.Prof@UAI.test", "full_name": "New Prof", "role": "professor",
        "temporary_password": "Temporary-Pass-1"})
    assert r.status_code == 201 and r.json()["email"] == "new.prof@uai.test"
    r = await c.post("/api/v1/auth/login", json={"email": "new.prof@uai.test", "password": "Temporary-Pass-1"})
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    r = await c.get("/api/v1/subjects", headers=headers)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "password_change_required"
    r = await c.post("/api/v1/auth/change-password", headers=headers,
                     json={"current_password": "Temporary-Pass-1", "new_password": "short"})
    assert r.status_code == 400
    r = await c.post("/api/v1/auth/change-password", headers=headers,
                     json={"current_password": "Temporary-Pass-1", "new_password": "A-much-better-one-77"})
    assert r.status_code == 200
    assert (await c.get("/api/v1/subjects", headers=headers)).status_code == 200
    # An admin cannot lock themselves out
    r = await c.patch(f"/api/v1/admin/users/{admin.id}", headers=admin_h, json={"role": "professor"})
    assert r.status_code == 400


async def test_csv_import_is_all_or_nothing(api):
    _, admin_h = await api.staff(Role.admin)
    c = api.client
    await c.post("/api/v1/admin/departments", headers=admin_h, json={"code": "cse", "name": "CSE"})
    await c.post("/api/v1/admin/batches", headers=admin_h, json={"code": "AIML-A", "name": "AIML A"})

    def upload(text):
        return {"file": ("students.csv", io.BytesIO(text.encode()), "text/csv")}

    bad = ("email,full_name,role,college_id,department_code,batch_code\n"
           "a@uai.test,Asha,student,U1,CSE,AIML-A\n"
           "not-an-email,Bad,student,U2,CSE,AIML-A\n"
           "b@uai.test,Bilal,student,U1,CSE,AIML-A\n"
           "c@uai.test,Chen,student,U3,MECH,AIML-A\n")
    r = await c.post("/api/v1/admin/users/import?dry_run=false", headers=admin_h, files=upload(bad))
    report = r.json()
    assert report["created"] == 0 and [e["line"] for e in report["errors"]] == [3, 4, 5]
    assert (await c.get("/api/v1/admin/users?q=uai.test&role=student", headers=admin_h)).json()["total"] == 0

    good = ("﻿Email,Full_Name,College_ID,Department_Code,Batch_Code\n"
            "a@uai.test,Asha,U1,CSE,AIML-A\nb@uai.test,Bilal,U2,CSE,AIML-A\n")
    r = await c.post("/api/v1/admin/users/import", headers=admin_h, files=upload(good))  # dry run by default
    assert r.json()["dry_run"] is True and r.json()["created"] == 2
    assert (await c.get("/api/v1/admin/users?role=student", headers=admin_h)).json()["total"] == 0
    r = await c.post("/api/v1/admin/users/import?dry_run=false", headers=admin_h, files=upload(good))
    assert r.json()["created"] == 2 and r.json()["batch_memberships_added"] == 2
    r = await c.post("/api/v1/admin/users/import?dry_run=false", headers=admin_h, files=upload(good))
    assert r.json()["created"] == 0 and r.json()["updated"] == 2 and r.json()["batch_memberships_added"] == 0


async def test_new_language_is_just_a_row(api):
    _, admin_h = await api.staff(Role.admin)
    c = api.client
    r = await c.post("/api/v1/admin/languages", headers=admin_h, json={
        "key": "pypy3", "name": "PyPy 3", "judge0_id": 999, "file_name": "main.py", "monaco_id": "python",
        "time_multiplier": 1.5})
    assert r.status_code == 201
    keys = [lang["key"] for lang in (await c.get("/api/v1/catalog/languages", headers=admin_h)).json()]
    assert "pypy3" in keys and "rust" not in keys  # disabled seed languages stay hidden


async def test_audit_chain_detects_tampering(api):
    w = await build_exam(api, n_students=1)
    c = api.client
    r = await c.get("/api/v1/admin/audit/verify", headers=w.admin)
    assert r.json()["ok"] is True and r.json()["checked"] > 5
    actions = [e["action"] for e in (await c.get("/api/v1/admin/audit", headers=w.admin)).json()]
    assert "exam.opened" in actions and "subject.created" in actions

    # Someone edits an old entry directly in the database...
    async with api.state.sessionmaker() as db:
        entry = await db.scalar(select(AuditEntry).where(AuditEntry.seq == 2))
        entry.details = {**entry.details, "code": "EDITED"}
        await db.commit()
    r = await c.get("/api/v1/admin/audit/verify", headers=w.admin)
    assert r.json()["ok"] is False and r.json()["problem_seq"] == 2


async def test_audit_chain_detects_deleted_tail(api):
    w = await build_exam(api, n_students=1)
    async with api.state.sessionmaker() as db:
        head = await db.get(AuditHead, 1)
        await db.execute(delete(AuditEntry).where(AuditEntry.seq == head.seq))
        await db.commit()
    r = await api.client.get("/api/v1/admin/audit/verify", headers=w.admin)
    assert r.json()["ok"] is False and r.json()["problem"] == "latest entries were deleted"


async def test_security_headers(api):
    r = await api.client.get("/api/v1/catalog/languages")
    assert r.status_code == 401
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert PASSWORD not in r.text
