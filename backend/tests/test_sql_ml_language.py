"""SQL and ML question types, the exam language lock, and the professor's batch/language views."""

import io
import json
import uuid

from app.models import Role
from app.seed import DEMO_PASSWORD, _ml_csv, seed_demo_data, seed_reference_data

from .conftest import REFERENCE, TESTS, build_exam, start

SCHEMA = "CREATE TABLE marks (student TEXT, subject TEXT, score INTEGER);"


async def _subject(api, languages):
    prof, h = await api.staff(Role.professor)
    r = await api.client.post("/api/v1/subjects", headers=h, json={
        "code": f"S{uuid.uuid4().hex[:6]}", "name": "Lab", "language_keys": languages})
    assert r.status_code == 201, r.text
    return prof, h, r.json()["id"]


async def test_sql_query_questions(api):
    _, h, sid = await _subject(api, ["sql", "python3"])
    c = api.client
    r = await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "Toppers", "statement_md": "Students scoring 90+, by name.", "difficulty": "easy",
        "grader": "sql", "grader_config": {"schema_sql": SCHEMA},
        "reference_language": "sql",
        "reference_code": "SELECT student, score FROM marks WHERE score >= 90 ORDER BY student;"})
    assert r.status_code == 201, r.text
    qid = r.json()["id"]
    await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": [
        {"input": "INSERT INTO marks VALUES ('asha','dbms',95),('bilal','dbms',60);", "is_sample": True},
        {"input": "INSERT INTO marks VALUES ('chen','os',90),('dev','os',89),('esha','os',100);"},
        {"input": "INSERT INTO marks VALUES ('farah','cn',10);"},  # empty result is a valid test too
    ]})
    rep = (await c.post(f"/api/v1/questions/{qid}/generate-outputs", headers=h)).json()
    assert rep["ok"], rep
    tests = (await c.get(f"/api/v1/questions/{qid}/tests", headers=h)).json()
    assert json.loads(tests[0]["expected_preview"]) == {"columns": ["student", "score"], "rows": [["asha", 95]]}

    async def attempt(code):
        r = await c.post(f"/api/v1/questions/{qid}/try", headers=h, json={"language": "sql", "code": code})
        assert r.status_code == 200, r.text
        return r.json()

    assert (await attempt("select student, score from marks where score >= 90"))["fraction"] == 1.0  # any order
    wrong = await attempt("SELECT student, score FROM marks WHERE score > 90")
    assert [t["verdict"] for t in wrong["tests"]] == ["AC", "WA", "AC"]
    assert "row" in wrong["tests"][1]["message"]
    cols = await attempt("SELECT student FROM marks WHERE score >= 90")
    assert "column" in cols["tests"][0]["message"]
    broken = await attempt("SELEC student FROM marks")
    assert {t["verdict"] for t in broken["tests"]} == {"RE"} and "SQL error" in broken["tests"][0]["stderr"]
    attach = await attempt("ATTACH DATABASE '/etc/passwd' AS x")
    assert {t["verdict"] for t in attach["tests"]} == {"RE"}  # file access is denied
    # Python is not accepted for an SQL question
    r = await c.post(f"/api/v1/questions/{qid}/try", headers=h, json={"language": "python3", "code": "print(1)"})
    assert {t["verdict"] for t in r.json()["tests"]} == {"IE"}


async def test_sql_script_mode(api):
    _, h, sid = await _subject(api, ["sql"])
    c = api.client
    q = (await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "Grace marks", "statement_md": "Add 5 to every score below 40.", "difficulty": "medium",
        "grader": "sql", "grader_config": {"mode": "script", "schema_sql": SCHEMA,
                                           "check_query": "SELECT student, score FROM marks"},
        "reference_language": "sql", "reference_code": "UPDATE marks SET score = score + 5 WHERE score < 40;"})).json()
    await c.post(f"/api/v1/questions/{q['id']}/tests", headers=h, json={"tests": [
        {"input": "INSERT INTO marks VALUES ('a','x',39),('b','x',40),('c','x',10);"}]})
    assert (await c.post(f"/api/v1/questions/{q['id']}/generate-outputs", headers=h)).json()["ok"]
    good = (await c.post(f"/api/v1/questions/{q['id']}/try", headers=h, json={
        "language": "sql", "code": "UPDATE marks SET score = score + 5 WHERE score <= 39;"})).json()
    bad = (await c.post(f"/api/v1/questions/{q['id']}/try", headers=h, json={
        "language": "sql", "code": "UPDATE marks SET score = score + 5;"})).json()
    assert good["fraction"] == 1.0 and bad["fraction"] == 0.0
    r = await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "x", "statement_md": "x", "difficulty": "easy", "grader": "sql", "grader_config": {"mode": "script"}})
    assert r.status_code == 400 and "check_query" in r.json()["detail"]["message"]


ML_CONFIG = {"task": "classification", "target": "passed", "metric": "accuracy",
             "thresholds": [{"at": 0.85, "credit": 1.0}, {"at": 0.7, "credit": 0.5}]}


async def test_ml_questions(api):
    from app.seed import _ML_REFERENCE

    _, h, sid = await _subject(api, ["python3-ml"])
    c = api.client
    r = await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "Pass?", "statement_md": "Predict passed.", "difficulty": "medium", "grader": "ml",
        "grader_config": ML_CONFIG, "reference_language": "python3-ml", "reference_code": _ML_REFERENCE})
    assert r.status_code == 201, r.text
    qid = r.json()["id"]
    for seed, sample in ((5, True), (6, False)):
        files = {"train": ("train.csv", io.BytesIO(_ml_csv(seed * 10, 160).encode()), "text/csv"),
                 "test": ("test.csv", io.BytesIO(_ml_csv(seed * 10 + 1, 60).encode()), "text/csv")}
        r = await c.post(f"/api/v1/questions/{qid}/datasets", headers=h, files=files,
                         data={"is_sample": str(sample).lower()})
        assert r.status_code == 201, r.text
    # Labels never reach the sandbox: the test CSV it receives has no 'passed' column
    full = (await c.get(f"/api/v1/questions/{qid}/tests/{r.json()['id']}", headers=h)).json()
    assert json.loads(full["input"])["test"].splitlines()[0] == "hours,attendance"
    assert len(json.loads(full["expected_output"])) == 60

    assert (await c.post(f"/api/v1/questions/{qid}/generate-outputs", headers=h)).status_code == 400
    ref = (await c.post(f"/api/v1/questions/{qid}/validate", headers=h)).json()
    assert ref["fraction"] == 1.0 and "accuracy" in ref["tests"][0]["message"]

    async def attempt(code):
        r = await c.post(f"/api/v1/questions/{qid}/try", headers=h, json={"language": "python3-ml", "code": code})
        assert r.status_code == 200, r.text
        return r.json()

    rule = ("import csv, io\n"
            "def solve(train_csv, test_csv):\n"
            "    print('debug output is fine')\n"
            "    return [1 if float(r['hours']) > 5 else 0 for r in csv.DictReader(io.StringIO(test_csv))]\n")
    partial = await attempt(rule)
    assert 0 < partial["fraction"] <= 1
    zeros = await attempt("def solve(train_csv, test_csv):\n    return [0] * 3\n")
    assert {t["verdict"] for t in zeros["tests"]} == {"WA"} and "predictions" in zeros["tests"][0]["message"]
    missing = await attempt("x = 1\n")
    assert {t["verdict"] for t in missing["tests"]} == {"RE"} and "solve" in missing["tests"][0]["stderr"]


async def test_ml_metrics_and_thresholds():
    from app.grading.ml import MlGrader, compute_metric

    assert compute_metric("accuracy", [1, 0, 1, 1], ["1", "0", "0", "1.0"]) == 0.75
    assert round(compute_metric("f1_macro", ["a", "a", "b"], ["a", "b", "b"]), 4) == 0.6667
    assert compute_metric("rmse", [1, 2, 3], [1, 2, 5]) == (4 / 3) ** 0.5
    assert compute_metric("r2", [1, 2, 3], [1, 2, 3]) == 1.0
    g = MlGrader()
    cfg = g.validate_config({"task": "regression", "target": "y", "metric": "rmse",
                             "thresholds": [{"at": 0.5, "credit": 0.5}, {"at": 0.1, "credit": 1}]})
    assert cfg["thresholds"][0] == {"at": 0.1, "credit": 1.0}  # best first


async def test_exam_language_lock(api):
    w = await build_exam(api, n_students=1, open_exam=False)
    c = api.client
    await c.put(f"/api/v1/subjects/{w.subject_id}/languages", headers=w.prof_h, json={"keys": ["python3", "c", "java"]})
    r = await c.patch(f"/api/v1/exams/{w.exam_id}", headers=w.prof_h, json={"languages": ["rust"]})
    assert r.status_code == 400  # not taught in this subject
    r = await c.patch(f"/api/v1/exams/{w.exam_id}", headers=w.prof_h, json={"languages": ["python3"]})
    assert r.status_code == 200 and r.json()["languages"] == ["python3"]
    code = (await c.post(f"/api/v1/exams/{w.exam_id}/open", headers=w.prof_h)).json()["access_code"]
    w.access_code = code
    (s,) = w.students
    view = (await start(api, w, s)).json()
    assert [lang["key"] for lang in view["questions"][0]["languages"]] == ["python3"]
    base = f"/api/v1/student/sessions/{view['session_id']}/answers/{w.question_id}"
    r = await api.signed(s, "PUT", base, {"language": "c", "code": "int main(){}"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "language_not_allowed"
    r = await api.signed(s, "PUT", base, {"language": "python3", "code": REFERENCE})
    assert r.status_code == 200
    # The lock cannot change while the exam is live
    r = await c.patch(f"/api/v1/exams/{w.exam_id}", headers=w.prof_h, json={"languages": ["c"]})
    assert r.status_code == 409


async def test_professor_views_by_batch_and_language(api):
    w = await build_exam(api, n_students=2, open_exam=False)
    c = api.client
    # A second division takes the same subject
    r = await c.post("/api/v1/admin/batches", headers=w.admin, json={"code": "ZZ-DIV-B", "name": "Div B"})
    div_b = r.json()["id"]
    late = await api.student()
    await c.post(f"/api/v1/admin/batches/{div_b}/members", headers=w.admin, json={"emails": [late.user.email]})
    await c.put(f"/api/v1/subjects/{w.subject_id}/batches", headers=w.prof_h, json={"batch_ids": [w.batch_id, div_b]})
    await c.put(f"/api/v1/subjects/{w.subject_id}/languages", headers=w.prof_h, json={"keys": ["python3", "c"]})
    w.access_code = (await c.post(f"/api/v1/exams/{w.exam_id}/open", headers=w.prof_h)).json()["access_code"]

    plan = [(w.students[0], "python3", REFERENCE), (w.students[1], "c", "int main(){return 0;}"),
            (late, "python3", "print(5)")]
    for s, lang, code in plan:
        sid = (await start(api, w, s)).json()["session_id"]
        r = await api.signed(s, "PUT", f"/api/v1/student/sessions/{sid}/answers/{w.question_id}",
                             {"language": lang, "code": code})
        assert r.status_code == 200, r.text

    board = (await c.get("/api/v1/dashboard", headers=w.prof_h)).json()
    (subject,) = board
    (exam,) = subject["exams"]
    by_code = {b["code"]: b for b in exam["batches"]}
    assert by_code["ZZ-DIV-B"]["enrolled"] == 1 and by_code["ZZ-DIV-B"]["started"] == 1
    assert sum(b["started"] for b in exam["batches"]) == 3
    assert exam["languages_used"] == {"python3": 2, "c": 1}

    py = (await c.get(f"/api/v1/exams/{w.exam_id}/answers?language=python3", headers=w.prof_h)).json()
    assert py["total"] == 2 and {a["language"] for a in py["items"]} == {"python3"}
    div = (await c.get(f"/api/v1/exams/{w.exam_id}/answers?batch_id={div_b}", headers=w.prof_h)).json()
    assert [a["student"]["email"] for a in div["items"]] == [late.user.email] and div["items"][0]["code"] == "print(5)"

    rows = (await c.get(f"/api/v1/exams/{w.exam_id}/results?language=c", headers=w.prof_h)).json()
    assert [r["student"]["email"] for r in rows] == [w.students[1].user.email] and rows[0]["languages"] == ["c"]
    mon = (await c.get(f"/api/v1/exams/{w.exam_id}/monitor?batch_code=ZZ-DIV-B", headers=w.prof_h)).json()
    assert [r["student"]["email"] for r in mon["rows"]] == [late.user.email]

    other, other_h = await api.staff(Role.professor)
    assert (await c.get("/api/v1/dashboard", headers=other_h)).json() == []
    assert (await c.get(f"/api/v1/exams/{w.exam_id}/answers", headers=other_h)).status_code == 404


async def test_demo_data_exams_are_all_ready(api):
    """The demo the mentor will see: three labs whose exams pass every readiness check
    (including each reference solution passing its own tests)."""
    async with api.state.sessionmaker() as db:
        await seed_reference_data(db)
        info = await seed_demo_data(db, api.state.cipher)
        again = await seed_demo_data(db, api.state.cipher)  # safe to run twice
    assert again["note"] == "demo data already present"
    r = await api.client.post("/api/v1/auth/login", json={"email": "prof.demo@aegis.local", "password": DEMO_PASSWORD})
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    for key in ("Python exam (Python only) id", "SQL exam id", "ML exam id"):
        r = await api.client.get(f"/api/v1/exams/{info[key]}/readiness", headers=headers)
        assert r.json() == {"ready": True, "problems": [], "warnings": []}, (key, r.json())
    assert len((await api.client.get("/api/v1/dashboard", headers=headers)).json()) == 3
    assert TESTS  # shared fixtures stay importable


async def test_student_takes_sql_and_ml_demo_exams(api):
    from app.security.signing import DeviceSigner
    from app.seed import _ML_REFERENCE, _SQL_REFERENCE

    from .conftest import Student

    async with api.state.sessionmaker() as db:
        await seed_reference_data(db)
        info = await seed_demo_data(db, api.state.cipher)
    c = api.client
    r = await c.post("/api/v1/auth/login", json={"email": "prof.demo@aegis.local", "password": DEMO_PASSWORD})
    prof_h = {"Authorization": f"Bearer {r.json()['token']}"}
    r = await c.post("/api/v1/auth/login", json={"email": "student1.demo@aegis.local", "password": DEMO_PASSWORD,
                                                 "client": "desktop"})
    student_h = {"Authorization": f"Bearer {r.json()['token']}"}
    student = Student(user=type("U", (), {"id": r.json()["user"]["id"]})(), headers=student_h, signer=DeviceSigner())
    await api.register(student_h, student.user, student.signer)

    for exam_key, language, code in (("SQL exam id", "sql", _SQL_REFERENCE), ("ML exam id", "python3-ml", _ML_REFERENCE)):
        exam_id = info[exam_key]
        access = (await c.post(f"/api/v1/exams/{exam_id}/open", headers=prof_h)).json()["access_code"]
        view = (await api.signed(student, "POST", f"/api/v1/student/exams/{exam_id}/start",
                                 {"access_code": access})).json()
        (q,) = view["questions"]
        assert [lang["key"] for lang in q["languages"]] == [language]
        base = f"/api/v1/student/sessions/{view['session_id']}/answers/{q['id']}"
        run = (await api.signed(student, "POST", f"{base}/run", {"language": language, "code": code})).json()
        assert run["passed"] == run["total"] == 1, run
        await api.signed(student, "POST", f"/api/v1/student/sessions/{view['session_id']}/finish")
        await c.post(f"/api/v1/exams/{exam_id}/close", headers=prof_h)
        await api.state.tasks.drain()
        (row,) = (await c.get(f"/api/v1/exams/{exam_id}/results", headers=prof_h)).json()
        assert row["total"] == row["max_total"] and row["batch_code"] == "CSE-AIML-2025-A", row
