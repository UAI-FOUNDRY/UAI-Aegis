"""HTML questions: pages checked against element/attribute/text checks (grading/html.py)."""

import uuid

import pytest

from app.grading.html import parse_checks, run_checks
from app.models import Role

PAGE = """<!DOCTYPE html>
<html><head><title>Sign up</title></head>
<body>
  <h1 class="title main">Welcome to the lab</h1>
  <form id="signup" action="/join" method="post">
    <label for="e">Email</label> <input id="e" type="email" name="email" required>
    <input type="password" name="pw">
    <button type="submit">Join</button>
  </form>
  <ul class="menu"><li><a href="/a">A</a><li><a href="/b">B</a></ul>
  <img src="logo.png" alt="Logo"><img src="x.png" alt="">
</body></html>"""

CHECKS = """# a sign-up page
doctype
exists form#signup[method=post]
exists form input[type=email][required]
count ul.menu > li = 2
count img >= 2
text h1 contains welcome
text title = Sign up
attr img alt
attr form action = /join
exists a[href^=/]
missing table
exists h1.title.main, h2
"""


def _ok(results):
    return {r["check"]: r["ok"] for r in results}


def test_checks_pass_on_a_correct_page():
    results = run_checks(PAGE, CHECKS)
    assert len(results) == 12
    assert [r["check"] for r in results if not r["ok"]] == []


def test_optional_end_tags_close_like_a_browser():
    page = "<ul><li>a<li>b<ul><li>c<li>d</ul><li>e</ul><p>one<p>two<div>x</div><table><tr><td>1<td>2<tr><td>3</table>"
    assert _ok(run_checks(page, "count body > ul > li = 3\ncount ul ul > li = 2\ncount p = 2\nmissing p div\n"
                                "count tr = 2\ncount td = 3")) == {
        "count body > ul > li = 3": False,  # no <body> written: nothing is inside a body element
        "count ul ul > li = 2": True, "count p = 2": True, "missing p div": True,
        "count tr = 2": True, "count td = 3": True}
    assert _ok(run_checks(page, "count ul > li = 5")) == {"count ul > li = 5": True}


def test_checks_catch_mistakes():
    broken = PAGE.replace("<!DOCTYPE html>", "").replace('type="email"', 'type="text"').replace('alt="Logo"', "")
    ok = _ok(run_checks(broken, "doctype\nexists input[type=email]\nattr img alt\ncount li = 2"))
    assert ok == {"doctype": False, "exists input[type=email]": False, "attr img alt": False, "count li = 2": True}
    results = run_checks("<p>hi</p>", "count p = 3\ntext p contains bye")
    assert [r["detail"] for r in results] == ["found 1", "text was: hi"]


def test_selectors():
    page = '<div id="a"><p class="x">1</p><section><p class="x y">2</p></section></div><p>3</p>'
    assert _ok(run_checks(page, "count div p = 2\ncount div > p = 1\ncount p.x.y = 1\ncount * = 5\n"
                                 "count #a, p = 4")) == {"count div p = 2": True, "count div > p = 1": True,
                                                         "count p.x.y = 1": True, "count * = 5": True,
                                                         "count #a, p = 4": True}
    assert _ok(run_checks('<a href="https://x.org/y">', "exists a[href^=https]\nexists a[href$='/y']\n"
                                                        "exists a[href*=x.org]\nmissing a[href=http]")) == {
        "exists a[href^=https]": True, "exists a[href$='/y']": True, "exists a[href*=x.org]": True,
        "missing a[href=http]": True}


@pytest.mark.parametrize("bad, why", [
    ("", "at least one check"),
    ("exist form", "unknown check"),
    ("count li", "count SELECTOR = NUMBER"),
    ("exists form >", "ends with '>'"),
    ("exists input[type=email", "unclosed"),
    ("exists ##x", "cannot read"),
])
def test_bad_checks_are_explained(bad, why):
    with pytest.raises(ValueError, match=why):
        parse_checks(bad)


async def test_html_question_end_to_end(api):
    _, h = await api.staff(Role.professor)
    c = api.client
    r = await c.post("/api/v1/subjects", headers=h, json={
        "code": f"W{uuid.uuid4().hex[:6]}", "name": "Web lab", "language_keys": ["html", "python3"]})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "Sign-up page", "statement_md": "Build a sign-up form.", "difficulty": "easy",
        "grader": "html", "grader_config": {}, "reference_language": "html", "reference_code": PAGE})
    assert r.status_code == 201, r.text
    qid = r.json()["id"]

    # Checks are validated when saved, with the line number.
    bad = await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": [{"input": "exists form\ncont li = 2"}]})
    assert bad.status_code == 400 and "line 2" in bad.json()["detail"]["message"]

    r = await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": [
        {"input": "doctype\nexists form#signup\ntext h1 contains welcome", "is_sample": True},
        {"input": "exists input[type=email][required]\ncount ul.menu > li = 2\nattr img alt\nmissing table"},
    ]})
    assert r.status_code == 201, r.text

    # No expected outputs: the reference page must simply pass every check.
    assert (await c.post(f"/api/v1/questions/{qid}/generate-outputs", headers=h)).status_code == 400
    v = (await c.post(f"/api/v1/questions/{qid}/validate", headers=h)).json()
    assert v["fraction"] == 1.0 and [t["verdict"] for t in v["tests"]] == ["AC", "AC"]
    assert v["tests"][0]["stdout"].startswith("✓ doctype")

    async def attempt(code, language="html"):
        r = await c.post(f"/api/v1/questions/{qid}/try", headers=h, json={"language": language, "code": code})
        assert r.status_code == 200, r.text
        return r.json()

    partial = await attempt(PAGE.replace("<!DOCTYPE html>", "").replace(" required", ""))
    sample, hidden = partial["tests"]
    assert sample["verdict"] == "PA" and "Failed: doctype" in sample["message"]  # sample tests name the failure
    assert hidden["verdict"] == "PA" and hidden["message"] == "3 of 4 checks passed"  # hidden checks stay hidden
    assert 0 < partial["fraction"] < 1
    empty = await attempt("<p>nothing here</p>")
    assert empty["tests"][0]["verdict"] == "WA" and empty["fraction"] == 0.125  # only "missing table" holds
    assert {t["verdict"] for t in (await attempt("print(1)", language="python3"))["tests"]} == {"IE"}

    # All-or-nothing scoring per test
    r = await c.patch(f"/api/v1/questions/{qid}", headers=h, json={"grader_config": {"partial_credit": False}})
    assert r.status_code == 200, r.text
    strict = await attempt(PAGE.replace("<!DOCTYPE html>", ""))
    assert [t["verdict"] for t in strict["tests"]] == ["WA", "AC"]


async def test_html_language_is_refused_for_standard_questions(api):
    _, h = await api.staff(Role.professor)
    c = api.client
    sid = (await c.post("/api/v1/subjects", headers=h, json={
        "code": f"X{uuid.uuid4().hex[:6]}", "name": "x", "language_keys": ["html", "python3"]})).json()["id"]
    qid = (await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "Echo", "statement_md": "x", "difficulty": "easy", "grader": "stdio",
        "reference_language": "python3", "reference_code": "print(input())"})).json()["id"]
    await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": [{"input": "hi", "expected_output": "hi"}]})
    r = (await c.post(f"/api/v1/questions/{qid}/try", headers=h, json={"language": "html", "code": "<p>hi</p>"})).json()
    assert {t["verdict"] for t in r["tests"]} == {"IE"}
