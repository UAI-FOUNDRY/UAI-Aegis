import base64
import json

import httpx

from app.grading.executor import ExecRequest, ExecStatus, Judge0Executor, LocalDevExecutor
from app.models import Role


async def _subject(api):
    prof, h = await api.staff(Role.professor)
    r = await api.client.post("/api/v1/subjects", headers=h, json={"code": "DSA-L", "name": "DSA Lab",
                                                                   "language_keys": ["python3", "cpp"]})
    return h, r.json()["id"]


async def test_generate_outputs_sets_time_limit_and_try_catches_wrong_code(api):
    h, sid = await _subject(api)
    c = api.client
    r = await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "Reverse", "statement_md": "Reverse the line.", "difficulty": "medium", "tags": ["Strings", " io "],
        "reference_language": "python3", "reference_code": "print(input()[::-1])",
        "grader_config": {"compare": "lines"}})
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["tags"] == ["io", "strings"] and q["reference_code"] == "print(input()[::-1])"
    await c.post(f"/api/v1/questions/{q['id']}/tests", headers=h, json={"tests": [
        {"input": "abc\n", "is_sample": True}, {"input": "racecar\n"}, {"input": "x" * 1000 + "\n", "group": "big"}]})
    r = await c.post(f"/api/v1/questions/{q['id']}/generate-outputs", headers=h)
    rep = r.json()
    assert rep["ok"] and rep["generated"] == 3 and rep["auto_time_limit_ms"] >= 1000
    tests = (await c.get(f"/api/v1/questions/{q['id']}/tests", headers=h)).json()
    assert tests[0]["expected_preview"] == "cba\n"

    assert (await c.post(f"/api/v1/questions/{q['id']}/validate", headers=h)).json()["fraction"] == 1.0
    wrong = (await c.post(f"/api/v1/questions/{q['id']}/try", headers=h,
                          json={"language": "python3", "code": "print(input())"})).json()
    assert wrong["passed"] == 2 and [t["verdict"] for t in wrong["tests"]] == ["WA", "AC", "AC"]
    slow = (await c.post(f"/api/v1/questions/{q['id']}/try", headers=h,
                         json={"language": "python3", "code": "while True: pass"})).json()
    assert {t["verdict"] for t in slow["tests"]} == {"TLE"}
    broken = (await c.post(f"/api/v1/questions/{q['id']}/try", headers=h,
                           json={"language": "python3", "code": "print(undefined_name)"})).json()
    assert {t["verdict"] for t in broken["tests"]} == {"RE"}


async def test_failing_reference_saves_nothing(api):
    h, sid = await _subject(api)
    c = api.client
    q = (await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "Div", "statement_md": "a/b", "difficulty": "easy", "reference_language": "python3",
        "reference_code": "a,b=map(int,input().split())\nprint(a//b)"})).json()
    await c.post(f"/api/v1/questions/{q['id']}/tests", headers=h, json={"tests": [{"input": "4 2"}, {"input": "1 0"}]})
    rep = (await c.post(f"/api/v1/questions/{q['id']}/generate-outputs", headers=h)).json()
    assert rep["ok"] is False and rep["failures"][0]["ordinal"] == 2
    tests = (await c.get(f"/api/v1/questions/{q['id']}/tests", headers=h)).json()
    assert not any(t["has_expected"] for t in tests)


async def test_bad_grader_config_and_unknown_language_rejected(api):
    h, sid = await _subject(api)
    c = api.client
    base = {"title": "T", "statement_md": "S", "difficulty": "easy"}
    r = await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={**base, "grader_config": {"compare": "fuzzy"}})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_grader_config"
    r = await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={**base, "starter_code": {"cobol": "x"}})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "unknown_language"
    graders = (await c.get("/api/v1/graders", headers=h)).json()
    assert [g["key"] for g in graders] == ["ml", "sql", "stdio"]


async def test_local_executor_limits():
    ex = LocalDevExecutor()
    req = dict(language_key="python3", judge0_id=71, file_name="main.py", stdin="", time_limit_ms=1000,
               memory_limit_kb=262144)
    flood, ok, cpp = await ex.run_batch([
        ExecRequest(source="while True: print('x' * 1000)", **req),
        ExecRequest(source="print('hi')", **req),
        ExecRequest(**{**req, "language_key": "cpp"}, source="int main(){}"),
    ])
    assert flood.status in (ExecStatus.runtime_error, ExecStatus.time_limit)
    assert ok.status is ExecStatus.ok and ok.stdout == "hi\n"
    assert cpp.status is ExecStatus.internal_error and "Judge0" in cpp.message


async def test_judge0_executor_against_a_fake_judge0():
    calls = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Auth-Token"] == "secret"
        if request.method == "POST":
            subs = json.loads(request.content)["submissions"]
            assert base64.b64decode(subs[0]["source_code"]).decode() == "print(input())"
            assert subs[0]["language_id"] == 71 and subs[0]["cpu_time_limit"] == 2.0
            return httpx.Response(201, json=[{"token": f"t{i}"} for i in range(len(subs))])
        calls["polls"] += 1
        if calls["polls"] == 1:
            return httpx.Response(200, json={"submissions": [{"token": "t0", "status": {"id": 2}}, None, None]})
        b64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731
        return httpx.Response(200, json={"submissions": [
            {"token": "t0", "status": {"id": 3}, "stdout": b64("hello\n"), "time": "0.012", "memory": 3000},
            {"token": "t1", "status": {"id": 5}, "time": "2.1", "memory": 3000},
            {"token": "t2", "status": {"id": 6}, "compile_output": b64("error: expected ';'")},
        ]})

    ex = Judge0Executor("http://judge0", token="secret", poll_interval=0, transport=httpx.MockTransport(handler))
    req = dict(language_key="python3", judge0_id=71, file_name="main.py", source="print(input())", stdin="hello",
               time_limit_ms=2000, memory_limit_kb=262144)
    ok, tle, ce = await ex.run_batch([ExecRequest(**req)] * 3)
    await ex.aclose()
    assert ok.status is ExecStatus.ok and ok.stdout == "hello\n" and ok.time_ms == 12
    assert tle.status is ExecStatus.time_limit
    assert ce.status is ExecStatus.compile_error and "expected" in ce.compile_output


async def test_judge0_down_is_an_internal_error_not_a_crash():
    def handler(request):
        raise httpx.ConnectError("refused")

    ex = Judge0Executor("http://judge0", transport=httpx.MockTransport(handler))
    (res,) = await ex.run_batch([ExecRequest("python3", 71, "main.py", "print(1)", "", 1000, 65536)])
    await ex.aclose()
    assert res.status is ExecStatus.internal_error


def test_local_executor_works_on_any_event_loop():
    """Regressions: (1) uvicorn --reload on Windows uses a SelectorEventLoop, which cannot
    start subprocesses, so every run failed with a 500; (2) under uvloop, a program that
    exited without reading its input (print(0)) crashed grading. The runner now uses
    threads, so it works on every loop."""
    import asyncio

    async def scenario():
        ex = LocalDevExecutor()
        req = dict(language_key="python3", judge0_id=71, file_name="main.py", time_limit_ms=2000,
                   memory_limit_kb=262144)
        return await ex.run_batch([
            ExecRequest(source="print(0)", stdin="x" * 500_000, **req),
            ExecRequest(source="print(sum(map(int, input().split())))", stdin="2 3\n", **req),
        ])

    factories = [asyncio.SelectorEventLoop, asyncio.new_event_loop]
    try:
        import uvloop

        factories.append(uvloop.new_event_loop)
    except ImportError:  # uvloop is not available on Windows
        pass
    for factory in factories:
        with asyncio.Runner(loop_factory=factory) as runner:
            ignored, summed = runner.run(scenario())
        assert (ignored.status, ignored.stdout) == (ExecStatus.ok, "0\n"), factory
        assert (summed.status, summed.stdout) == (ExecStatus.ok, "5\n"), factory
