"""Function questions (LeetCode style): the student writes a function, its return value is graded."""

import json
import shutil
import subprocess
import uuid

import pytest

from app.grading import get_grader
from app.grading.executor import ExecResult, ExecStatus
from app.grading.function import values_match
from app.grading.registry import RunTarget, TestSpec
from app.models import Role

REFERENCE = """def two_sum(nums, target):
    seen = {}
    for i, n in enumerate(nums):
        if target - n in seen:
            return (seen[target - n], i)   # a tuple is returned as a list
        seen[n] = i
"""
CONFIG = {"function_name": "two_sum", "params": ["nums", "target"]}
TESTS = [{"input": "[[2, 7, 11, 15], 9]", "is_sample": True}, {"input": "[[3, 2, 4], 6]", "is_sample": True},
         {"input": "[[3, 3], 6]"}, {"input": "[[-1, -2, -3, -4, -5], -8]"}]


async def _question(api, prof_h, subject_id):
    c = api.client
    r = await c.post(f"/api/v1/subjects/{subject_id}/questions", headers=prof_h, json={
        "title": "Two sum", "statement_md": "Return the two indices.", "difficulty": "easy", "marks": 10,
        "grader": "function", "grader_config": CONFIG, "reference_language": "python3", "reference_code": REFERENCE})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_professor_workflow_validates_tests_and_generates_expected_values(api):
    prof, h = await api.staff(Role.professor)
    c = api.client
    sid = (await c.post("/api/v1/subjects", headers=h, json={"code": "DSA", "name": "DSA Lab",
                                                             "language_keys": ["python3", "c"]})).json()["id"]
    qid = await _question(api, h, sid)

    # Malformed tests are refused when saved, not discovered during the exam
    r = await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": [{"input": "2 7 11 15\n9"}]})
    assert r.status_code == 400 and "JSON" in r.json()["detail"]["message"]
    r = await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": [{"input": "[[1, 2]]"}]})
    assert r.status_code == 400 and "expected 2 argument(s)" in r.json()["detail"]["message"]
    r = await c.post(f"/api/v1/questions/{qid}/tests", headers=h,
                     json={"tests": [{"input": "[[1, 2], 3]", "expected_output": "[0, 1"}]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_test"

    r = await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": TESTS})
    assert r.status_code == 201, r.text
    rep = (await c.post(f"/api/v1/questions/{qid}/generate-outputs", headers=h)).json()
    assert rep["ok"], rep
    tests = (await c.get(f"/api/v1/questions/{qid}/tests", headers=h)).json()
    assert [json.loads(t["expected_preview"]) for t in tests] == [[0, 1], [1, 2], [0, 1], [2, 4]]
    test_id = tests[0]["id"]
    r = await c.patch(f"/api/v1/questions/{qid}/tests/{test_id}", headers=h, json={"input": "not json"})
    assert r.status_code == 400

    assert (await c.post(f"/api/v1/questions/{qid}/validate", headers=h)).json()["fraction"] == 1.0
    wrong = (await c.post(f"/api/v1/questions/{qid}/try", headers=h, json={
        "language": "python3", "code": "def two_sum(nums, target):\n    return [0, 1]\n"})).json()
    assert [t["verdict"] for t in wrong["tests"]] == ["AC", "WA", "AC", "WA"]
    printed = (await c.post(f"/api/v1/questions/{qid}/try", headers=h, json={
        "language": "python3", "code": "def two_sum(nums, target):\n    print([0, 1])\n"})).json()
    assert {t["verdict"] for t in printed["tests"]} == {"WA"}  # printing is not returning
    # The function's name does not matter: only whether it works
    for code in (
        "def lookup(seen, x):\n    return seen.get(x)\n\n"
        "def find_pair(nums, target):\n    seen = {}\n    for i, n in enumerate(nums):\n"
        "        if lookup(seen, target - n) is not None:\n            return [seen[target - n], i]\n"
        "        seen[n] = i\n",
        "class Solution:\n    def twoSum(self, nums, target):\n        seen = {}\n"
        "        for i, n in enumerate(nums):\n            if target - n in seen:\n"
        "                return [seen[target - n], i]\n            seen[n] = i\n",
    ):
        other = (await c.post(f"/api/v1/questions/{qid}/try", headers=h,
                              json={"language": "python3", "code": code})).json()
        assert other["fraction"] == 1.0, other
    nothing = (await c.post(f"/api/v1/questions/{qid}/try", headers=h, json={
        "language": "python3", "code": "x = 1\n"})).json()
    assert {t["verdict"] for t in nothing["tests"]} == {"WA"}
    assert {t["message"] for t in nothing["tests"]} == {"no value was returned"}
    # C cannot answer a function question
    r = await c.post(f"/api/v1/questions/{qid}/try", headers=h, json={"language": "c", "code": "int main(){}"})
    assert {t["verdict"] for t in r.json()["tests"]} == {"IE"}


async def test_empty_editor_and_safe_feedback(api):
    c = api.client
    _, admin_h = await api.staff(Role.admin)
    prof, h = await api.staff(Role.professor)
    student = await api.student()
    batch = (await c.post("/api/v1/admin/batches", headers=admin_h,
                          json={"code": f"F{uuid.uuid4().hex[:5]}", "name": "F"})).json()["id"]
    await c.post(f"/api/v1/admin/batches/{batch}/members", headers=admin_h, json={"emails": [student.user.email]})
    sid = (await c.post("/api/v1/subjects", headers=h, json={"code": "DSA2", "name": "DSA", "language_keys": ["python3"],
                                                             "batch_ids": [batch]})).json()["id"]
    qid = await _question(api, h, sid)
    await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": TESTS})
    assert (await c.post(f"/api/v1/questions/{qid}/generate-outputs", headers=h)).json()["ok"]
    exam = (await c.post(f"/api/v1/subjects/{sid}/exams", headers=h, json={
        "title": "DSA practical", "duration_minutes": 30, "question_ids": [qid], "selection": {"mode": "fixed"},
        "languages": ["python3"], "policy": {"reveal_hidden_results": True}})).json()
    readiness = (await c.get(f"/api/v1/exams/{exam['id']}/readiness", headers=h)).json()
    assert readiness["ready"], readiness
    code = (await c.post(f"/api/v1/exams/{exam['id']}/open", headers=h)).json()["access_code"]

    view = (await api.signed(student, "POST", f"/api/v1/student/exams/{exam['id']}/start", {"access_code": code})).json()
    (q,) = view["questions"]
    assert q["starter_code"] == {}  # the editor starts empty
    assert json.loads(q["samples"][0]["input"]) == [[2, 7, 11, 15], 9]

    base = f"/api/v1/student/sessions/{view['session_id']}/answers/{qid}"
    wrong = "def two_sum(nums, target):\n    print('trying', nums)\n    return [0, 1]\n"
    run = (await api.signed(student, "POST", f"{base}/run", {"language": "python3", "code": wrong})).json()
    first, second = run["tests"]
    assert first["verdict"] == "AC" and first["stdout"] == "[0, 1]" and "trying" in first["stderr"]
    assert second["verdict"] == "WA" and second["expected_output"] == "[1, 2]"
    sub = (await api.signed(student, "POST", f"{base}/submit", {"language": "python3", "code": wrong})).json()
    hidden = [t for t in sub["tests"] if not t["is_sample"]]
    assert [t["verdict"] for t in hidden] == ["AC", "WA"]
    # Hidden feedback never includes the expected value or the student's output
    assert all(t["message"] == "wrong return value" or t["message"] is None for t in hidden)
    assert all(t.get("stdout") in (None, "") and t.get("expected_output") is None for t in hidden)


def test_config_is_validated():
    g = get_grader("function")
    for bad in ({"function_name": "os.system"}, {"function_name": "class"}, {"function_name": "__import__"},
                {"function_name": "require"}, {"params": ["a", "a"]}, {"params": ["x y"]}, {"compare": "fuzzy"}):
        with pytest.raises(ValueError):
            g.validate_config(bad)
    assert g.validate_config({})["function_name"] is None


def test_value_comparison_modes():
    assert values_match([0, 1], [0, 1]) and values_match(2, 2.0)
    assert not values_match(True, 1) and not values_match([0, 1], [1, 0])
    assert values_match([[1, 2], [0, 3]], [[0, 3], [1, 2]], "unordered")
    assert not values_match(0.1 + 0.2, 0.3) and values_match(0.1 + 0.2, 0.3, "float")
    assert values_match({"a": [1.0, "x"]}, {"a": [1, "x"]}) and not values_match({"a": 1}, {"b": 1})


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_javascript_harness_runs_in_node():
    g = get_grader("function")
    config = g.validate_config(CONFIG)
    spec = TestSpec("t", 1, True, "main", 1, "[[3, 2, 4], 6]", "[1, 2]")
    for source in (
        "function two_sum(nums, target) { console.log('dbg'); const m = new Map();"
        " for (let i = 0; i < nums.length; i++) { if (m.has(target - nums[i])) return [m.get(target - nums[i]), i];"
        " m.set(nums[i], i); } }",
        "const two_sum = async (nums, target) => { process.stdout.write('noise\\n'); return [1, 2]; };",
        # any name, a helper, or a LeetCode-style class
        "const lookup = (m, x) => m.get(x);\nconst pairUp = (nums, target) => { const m = new Map();"
        " for (let i = 0; i < nums.length; i++) { const j = lookup(m, target - nums[i]);"
        " if (j !== undefined) return [j, i]; m.set(nums[i], i); } };",
        "class Solution { twoSum(nums, target) { return [1, 2]; } }",
    ):
        req = g.prepare(RunTarget("javascript", 63, "main.js", source, 2000, 262144), spec, config)
        out = subprocess.run(["node", "-e", req.source], input=req.stdin, capture_output=True, text=True, timeout=20)
        result = ExecResult(ExecStatus.ok, stdout=out.stdout, stderr=out.stderr)
        assert g.check(spec, result, config).verdict == "AC", (out.stdout, out.stderr)
        assert out.stdout.count("\n") == 1  # the student's output went to stderr
