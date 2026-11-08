"""Question data files: professors attach datasets, students see a summary and their
code opens the file by name (local runner here; Judge0 request building below)."""

from __future__ import annotations

import base64
import io
import json
import os
import zipfile

import httpx
import pytest

from app.grading.executor import (
    _WRAPPER_FRAME,
    DataFile,
    ExecRequest,
    ExecStatus,
    Judge0Executor,
    LocalDevExecutor,
)
from app.models import Role

from .conftest import build_exam, start

MARKS = b"name,mark,city\nasha,91,Pune\nravi,78,Mumbai\nmeera,,Pune\nkabir,85.5,Delhi\n"


async def _upload(api, headers, qid, content: bytes, filename: str = "marks.csv", **form):
    return await api.client.post(f"/api/v1/questions/{qid}/files", headers=headers,
                                 files={"file": (filename, content, "text/csv")}, data=form)


async def test_upload_shows_a_summary_and_the_code_reads_the_file(api):
    w = await build_exam(api, n_students=2, open_exam=False)
    r = await _upload(api, w.prof_h, w.question_id, MARKS)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["name"] == "marks.csv" and f["kind"] == "table" and f["rows"] == 4 and f["column_count"] == 3
    assert f["read_hint"] == 'pd.read_csv("marks.csv")'
    cols = {c["name"]: c for c in f["meta"]["columns"]}
    assert cols["mark"]["type"] == "number" and cols["mark"]["missing"] == 1
    assert cols["mark"]["min"] == 78 and cols["mark"]["max"] == 91
    assert cols["city"]["type"] == "text" and cols["city"]["distinct"] == 3
    assert f["meta"]["header"] == ["name", "mark", "city"] and f["meta"]["preview"][0] == ["asha", "91", "Pune"]
    # The bytes are on disk under a random, read-only name, not in the database
    files = api.state.files
    stored = [p for p in files.root.iterdir() if not p.name.startswith(".")]
    assert len(stored) == 1 and stored[0].read_bytes() == MARKS and len(stored[0].name) == 32
    if os.name == "posix":
        assert stored[0].stat().st_mode & 0o777 == 0o444
        assert files.root.stat().st_mode & 0o777 == 0o711  # sandboxes can't list the folder

    r = await api.client.post(f"/api/v1/exams/{w.exam_id}/open", headers=w.prof_h)
    code = r.json()["access_code"]
    s1, s2 = w.students
    view = (await start(api, w, s1, code)).json()
    (q,) = view["questions"]
    assert [x["name"] for x in q["files"]] == ["marks.csv"] and q["files"][0]["rows"] == 4
    base = f"/api/v1/student/sessions/{view['session_id']}"
    r = await api.signed(s1, "GET", f"{base}/questions/{q['id']}/files/{f['id']}")
    assert r.status_code == 200 and r.json()["meta"]["preview"][1][0] == "ravi"

    # The student's code opens it by name (Run shows sample output)
    reader = "import csv\nrows = list(csv.DictReader(open('marks.csv')))\nprint(rows[0]['name'], len(rows))\n"
    r = await api.signed(s1, "POST", f"{base}/answers/{q['id']}/run", {"language": "python3", "code": reader})
    assert r.status_code == 200, r.text
    assert r.json()["tests"][0]["stdout"].strip() == "asha 4"

    # Another student cannot read it through someone else's session
    view2 = (await start(api, w, s2, code)).json()
    r = await api.signed(s2, "GET", f"{base}/questions/{q['id']}/files/{f['id']}")
    assert r.status_code in (404, 409)
    # ...nor a file of another question through their own session
    r = await api.signed(s2, "GET", f"/api/v1/student/sessions/{view2['session_id']}/questions/{q['id']}/files/"
                                    "00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_reference_solution_can_use_the_file_for_expected_outputs(api):
    w = await build_exam(api, n_students=1, open_exam=False)
    c = api.client
    reference = ("import csv\nn = int(input())\nrows = list(csv.DictReader(open('marks.csv')))\n"
                 "print(sum(float(r['mark']) for r in rows[:n] if r['mark']))\n")
    r = await c.post(f"/api/v1/subjects/{w.subject_id}/questions", headers=w.prof_h, json={
        "title": "Top marks", "statement_md": "Sum the first n marks in marks.csv.", "difficulty": "easy",
        "reference_language": "python3", "reference_code": reference})
    qid = r.json()["id"]
    assert (await _upload(api, w.prof_h, qid, MARKS)).status_code == 201
    await c.post(f"/api/v1/questions/{qid}/tests", headers=w.prof_h,
                 json={"tests": [{"input": "1\n", "is_sample": True}, {"input": "4\n"}]})
    r = await c.post(f"/api/v1/questions/{qid}/generate-outputs", headers=w.prof_h)
    assert r.status_code == 200 and r.json()["ok"], r.text
    tests = (await c.get(f"/api/v1/questions/{qid}/tests", headers=w.prof_h)).json()
    assert [t["expected_preview"].strip() for t in tests] == ["91.0", "254.5"]
    r = await c.post(f"/api/v1/questions/{qid}/try", headers=w.prof_h,
                     json={"language": "python3", "code": reference})
    assert r.status_code == 200 and r.json()["fraction"] == 1.0


async def test_names_limits_replacing_and_removing(api):
    w = await build_exam(api, n_students=1, open_exam=False)
    qid = w.question_id
    # A browser may send a path: only the name is kept
    r = await _upload(api, w.prof_h, qid, b"x\n1\n", filename="C:\\Users\\prof\\data.csv")
    assert r.status_code == 201 and r.json()["name"] == "data.csv"
    for bad in ["main.py", ".hidden", "a b.csv", "x" * 120]:
        r = await _upload(api, w.prof_h, qid, b"1", filename=bad)
        assert r.status_code == 400, bad
    # Binary and text files
    r = await _upload(api, w.prof_h, qid, b"\x89PNG\x00\x01\x02", filename="logo.png")
    assert r.json()["kind"] == "binary" and r.json()["read_hint"] == 'open("logo.png", "rb").read()'
    r = await _upload(api, w.prof_h, qid, b"line one\nline two\n", filename="notes.txt")
    assert r.json()["kind"] == "text" and r.json()["meta"]["lines"] == 2

    # Same name replaces (and the old bytes are deleted)
    before = {p.name for p in api.state.files.root.iterdir()}
    r = await _upload(api, w.prof_h, qid, b"x\n1\n2\n", filename="data.csv")
    assert r.status_code == 201 and r.json()["rows"] == 2
    after = {p.name for p in api.state.files.root.iterdir()}
    assert len(after) == len(before) and after != before
    listed = (await api.client.get(f"/api/v1/questions/{qid}/files", headers=w.prof_h)).json()
    assert sorted(x["name"] for x in listed) == ["data.csv", "logo.png", "notes.txt"]

    # Size limit
    api.state.settings.max_data_file_bytes = 100
    r = await _upload(api, w.prof_h, qid, b"y" * 101, filename="big.csv")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "too_large"
    assert not any(p.name.startswith(".part-") for p in api.state.files.root.iterdir())
    api.state.settings.max_data_file_bytes = 200 * 1024 * 1024

    # While an exam using the question is live, files can be added but not changed or removed
    await api.client.post(f"/api/v1/exams/{w.exam_id}/open", headers=w.prof_h)
    data_id = next(x["id"] for x in listed if x["name"] == "data.csv")
    r = await api.client.delete(f"/api/v1/questions/{qid}/files/{data_id}", headers=w.prof_h)
    assert r.status_code == 409
    assert (await _upload(api, w.prof_h, qid, b"z", filename="data.csv")).status_code == 409
    assert (await _upload(api, w.prof_h, qid, b"z\n", filename="extra.txt")).status_code == 201
    await api.client.post(f"/api/v1/exams/{w.exam_id}/close", headers=w.prof_h)
    r = await api.client.delete(f"/api/v1/questions/{qid}/files/{data_id}", headers=w.prof_h)
    assert r.status_code == 204
    assert len([p for p in api.state.files.root.iterdir()]) == 3


async def test_only_editors_upload_and_others_see_nothing(api):
    w = await build_exam(api, n_students=1, open_exam=False)
    _, other_h = await api.staff(Role.professor)
    r = await _upload(api, other_h, w.question_id, MARKS)
    assert r.status_code == 404
    r = await _upload(api, w.admin, w.question_id, MARKS)  # admins never see question content
    assert r.status_code in (403, 404)


async def test_large_upload_passes_the_request_size_limit(api):
    """Other requests are capped at 8 MB; data file uploads have their own limit."""
    w = await build_exam(api, n_students=1, open_exam=False)
    rows = "\n".join(f"{i},{i * 7 % 101},student{i}" for i in range(450_000))
    content = ("id,score,name\n" + rows + "\n").encode()
    assert len(content) > 9 * 1024 * 1024
    r = await _upload(api, w.prof_h, w.question_id, content, filename="big.csv")
    assert r.status_code == 201, r.text[:300]
    body = r.json()
    assert body["rows"] == 450_000 and body["size_bytes"] == len(content)
    score = next(c for c in body["meta"]["columns"] if c["name"] == "score")
    assert score["type"] == "number" and score["min"] == 0 and score["max"] == 100
    assert len(body["meta"]["preview"]) == 50


# ------------------------------------------------------------------------- runners


def test_local_runner_places_files_and_reports_missing_ones(tmp_path):
    import asyncio

    data = tmp_path / "abcd"
    data.write_bytes(MARKS)
    f = DataFile(name="marks.csv", key="ab" * 16, path=str(data), size=len(MARKS))
    code = "print(open('marks.csv').read().count('\\n'))\n"
    (ok,) = asyncio.run(LocalDevExecutor().run_batch([ExecRequest("python3", 71, "main.py", code, "", 2000, 262144,
                                                                    files=(f,))]))
    assert ok.status is ExecStatus.ok and ok.stdout.strip() == "5"
    # The stored file cannot be changed by the program (read-only link, or a copy as root)
    data.chmod(0o444)
    writer = "open('marks.csv', 'w').write('hacked')\n"
    (res,) = asyncio.run(LocalDevExecutor().run_batch([ExecRequest("python3", 71, "main.py", writer, "", 2000,
                                                                    262144, files=(f,))]))
    assert data.read_bytes() == MARKS
    if os.name == "posix" and os.geteuid() != 0:
        assert res.status is ExecStatus.runtime_error
    gone = DataFile(name="x.csv", key="cd" * 16, path=str(tmp_path / "missing"), size=1)
    (bad,) = asyncio.run(LocalDevExecutor().run_batch([ExecRequest("python3", 71, "main.py", code, "", 2000, 262144,
                                                                    files=(gone,))]))
    assert bad.status is ExecStatus.internal_error and "missing" in bad.message


async def test_judge0_gets_small_files_inline_and_big_ones_by_link(tmp_path):
    small = tmp_path / "s"
    small.write_bytes(MARKS)
    big = tmp_path / "b"
    big.write_bytes(b"x" * 2000)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            subs = json.loads(request.content)["submissions"]
            sent.extend(subs)
            return httpx.Response(201, json=[{"token": f"t{i}"} for i in range(len(subs))])
        tokens = request.url.params["tokens"].split(",")
        stderr = base64.b64encode(
            b'Traceback (most recent call last):\n  File "/box/script.py", line 9, in <module>\n'
            b"    exec(compile(_aegis_source, 'main.py', 'exec'), {})\n"
            b'  File "main.py", line 2, in <module>\n    1/0\nZeroDivisionError: division by zero\n').decode()
        return httpx.Response(200, json={"submissions": [
            {"token": t, "status": {"id": 3}, "stdout": None, "stderr": stderr, "time": "0.01", "memory": 1000}
            for t in tokens]})

    judge = Judge0Executor("http://judge0", transport=httpx.MockTransport(handler), poll_interval=0.01,
                           data_dir="/usr/share/aegis-data", inline_files_max_bytes=1000)
    small_f = DataFile("marks.csv", "ab" * 16, str(small), len(MARKS))
    big_f = DataFile("big.csv", "cd" * 16, str(big), 2000)
    requests = [
        ExecRequest("python3", 71, "main.py", "print(1)\n", "", 1000, 262144, files=(small_f,)),
        ExecRequest("python3", 71, "main.py", "x = 1\n1/0\n", "", 1000, 262144, files=(big_f,)),
        ExecRequest("cpp", 54, "main.cpp", "int main(){}", "", 1000, 262144, files=(big_f,)),
        ExecRequest("cpp", 54, "main.cpp", "int main(){}", "", 1000, 262144, files=(small_f,)),
    ]
    results = await judge.run_batch(requests)
    await judge.aclose()

    # 1: small file inline as a zip; source untouched
    assert len(sent) == 3  # the big C++ one was refused before sending
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(sent[0]["additional_files"])))
    assert archive.namelist() == ["marks.csv"] and archive.read("marks.csv") == MARKS
    assert base64.b64decode(sent[0]["source_code"]).decode() == "print(1)\n"
    # 2: big file for Python: linked from the shared read-only folder, no upload
    wrapped = base64.b64decode(sent[1]["source_code"]).decode()
    assert "additional_files" not in sent[1]
    assert "/usr/share/aegis-data/" + "cd" * 16 in wrapped and "'big.csv'" in wrapped
    compile(wrapped, "w", "exec")
    # 3: big file for C++: a clear message, nothing sent
    assert results[2].status is ExecStatus.internal_error and "Python" in results[2].message
    # 4: small file for C++ travels inline
    assert "additional_files" in sent[2]
    # The wrapper's own traceback frame is not shown to students
    assert "_aegis_source" not in results[1].stderr and 'File "main.py", line 2' in results[1].stderr
    assert results[0].status is ExecStatus.ok and results[3].status is ExecStatus.ok


def test_wrapper_frame_pattern_keeps_everything_else():
    raw = ('Traceback (most recent call last):\n  File "/box/script.py", line 9, in <module>\n'
           "    exec(compile(_aegis_source, 'main.py', 'exec'), {})\n"
           "    ~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
           '  File "main.py", line 3, in <module>\n    f()\nValueError: x\n')
    assert _WRAPPER_FRAME.sub(r"\1", raw) == ('Traceback (most recent call last):\n  File "main.py", line 3, '
                                              "in <module>\n    f()\nValueError: x\n")


@pytest.mark.parametrize("name", ["data.csv", "Train_2024.tsv", "a-b.c.json"])
def test_good_names(name):
    from app.services.files import check_file_name

    assert check_file_name(name, {"main.py"}) == name
