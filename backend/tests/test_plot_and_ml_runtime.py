"""Plot questions (grading/plot.py), the local ML runtime (.venv-ml) and the BML lab datasets."""

import uuid

import pytest

from app import bml_lab
from app.config import Settings
from app.grading.plot import judge_all, parse_checks
from app.models import Role

HAS_ML_RUNTIME = Settings().ml_python_path is not None
needs_ml = pytest.mark.skipif(not HAS_ML_RUNTIME, reason="needs backend/.venv-ml with numpy, pandas, matplotlib...")

SUMMARY = {
    "figures": 2, "suptitles": ["", "EDA"], "figure_legend": False,
    "axes": [
        {"title": "Total Sales by Region", "xlabel": "Region", "ylabel": "Sales", "legend": True, "kinds": ["bar"],
         "bars": 4, "lines": 0, "points": 0, "wedges": 0, "xticks": ["East", "North", "South", "West"]},
        {"title": "", "xlabel": "sales", "ylabel": "Count", "legend": False, "kinds": ["hist"],
         "bars": 10, "lines": 0, "points": 0, "wedges": 0, "xticks": ["100", "200"]},
    ],
}


def test_plot_checks_judge_a_figure_summary():
    results = judge_all(SUMMARY, "chart bar\nchart hist\nchart pie\ntitle contains total sales\ntitle = EDA\n"
                                 "xlabel = Region\nylabel contains count\nlegend\nxticks include north, West\n"
                                 "count bars = 14\ncount axes >= 2\ncount figures = 3\n# comment")
    ok = {r["check"]: r["ok"] for r in results}
    assert ok == {"chart bar": True, "chart hist": True, "chart pie": False, "title contains total sales": True,
                  "title = EDA": True, "xlabel = Region": True, "ylabel contains count": True, "legend": True,
                  "xticks include north, West": True, "count bars = 14": True, "count axes >= 2": True,
                  "count figures = 3": False}
    assert [r["detail"] for r in results if not r["ok"]] == ["charts drawn: bar, hist", "found 2"]


@pytest.mark.parametrize("bad, why", [
    ("", "at least one check"),
    ("chart donut", "chart KIND"),
    ("title Sales", "title contains WORDS"),
    ("count slices = 3", "count WHAT = N"),
    ("legend please", "just: legend"),
    ("plot bar", "unknown check"),
])
def test_bad_plot_checks_are_explained(bad, why):
    with pytest.raises(ValueError, match=why):
        parse_checks(bad)


def test_bml_datasets_are_reproducible():
    assert bml_lab.loans_csv(11, 50) == bml_lab.loans_csv(11, 50)
    assert bml_lab.loans_csv(11, 50) != bml_lab.loans_csv(12, 50)
    practicals = [s.practical for s in bml_lab.SPECS]
    assert practicals == ["1", "2", "3", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"]  # 4 is theory
    loans = bml_lab.loans_csv(11, 400).splitlines()[1:]
    approved = sum(line.endswith(",yes") for line in loans) / len(loans)
    assert 0.25 < approved < 0.75  # balanced enough for F1 to mean something


async def _subject(api):
    _, h = await api.staff(Role.professor)
    r = await api.client.post("/api/v1/subjects", headers=h, json={
        "code": f"P{uuid.uuid4().hex[:6]}", "name": "BML", "language_keys": ["python3-ml", "python3"]})
    assert r.status_code == 201, r.text
    return h, r.json()["id"]


@needs_ml
async def test_ml_runtime_has_the_libraries(api):
    h, sid = await _subject(api)
    c = api.client
    q = (await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "Libraries", "statement_md": "x", "difficulty": "easy", "grader": "stdio", "time_limit_ms": 8000,
        "reference_language": "python3-ml",
        "reference_code": "import numpy, pandas, matplotlib, seaborn, sklearn\nprint(int(numpy.array([1, 2]).sum()))"
    })).json()
    await c.post(f"/api/v1/questions/{q['id']}/tests", headers=h, json={"tests": [{"input": "", "expected_output": "3"}]})
    v = (await c.post(f"/api/v1/questions/{q['id']}/validate", headers=h)).json()
    assert v["fraction"] == 1.0, v
    # Plain python3 answers still run with the API's own Python, which has no pandas.
    r = (await c.post(f"/api/v1/questions/{q['id']}/try", headers=h, json={
        "language": "python3", "code": "import pandas\nprint(3)"})).json()
    assert r["tests"][0]["verdict"] == "RE" and "pandas" in r["tests"][0]["stderr"]


@needs_ml
async def test_plot_question_end_to_end(api):
    h, sid = await _subject(api)
    c = api.client
    reference = (
        "import pandas as pd\nimport matplotlib.pyplot as plt\nimport seaborn as sns\n"
        "df = pd.read_csv('data.csv')\nt = df.groupby('region')['sales'].sum()\n"
        "plt.bar(t.index, t.values)\nplt.title('Total Sales by Region')\nplt.xlabel('Region')\nplt.ylabel('Sales')\n"
        "plt.show()\nplt.figure()\nsns.histplot(df['sales'])\n"
    )
    r = await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "Sales chart", "statement_md": "x", "difficulty": "easy", "grader": "plot", "time_limit_ms": 8000,
        "grader_config": {"data_csv": bml_lab.sales_csv(31)}, "reference_language": "python3-ml",
        "reference_code": reference})
    assert r.status_code == 201, r.text
    qid = r.json()["id"]
    bad = await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": [{"input": "chart donut"}]})
    assert bad.status_code == 400 and "line 1" in bad.json()["detail"]["message"]
    await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": [
        {"input": "chart bar\ntitle contains total sales", "is_sample": True},
        {"input": "xlabel = Region\nylabel = Sales\nxticks include North, South\nchart hist\ncount figures = 2"},
    ]})
    v = (await c.post(f"/api/v1/questions/{qid}/validate", headers=h)).json()
    assert v["fraction"] == 1.0, v
    assert "✓ chart bar" in v["tests"][0]["stdout"]

    async def attempt(code, language="python3-ml"):
        r = await c.post(f"/api/v1/questions/{qid}/try", headers=h, json={"language": language, "code": code})
        assert r.status_code == 200, r.text
        return r.json()

    no_labels = await attempt(reference.replace("plt.xlabel('Region')\n", "").replace("plt.title('Total Sales by Region')\n", ""))
    sample, hidden = no_labels["tests"]
    assert sample["verdict"] == "PA" and "Failed: title contains total sales" in sample["message"]
    assert hidden["verdict"] == "PA" and hidden["message"] == "4 of 5 checks passed"
    pie = await attempt("import matplotlib.pyplot as plt\nplt.pie([1, 2, 3])\n")
    assert pie["tests"][0]["verdict"] == "WA" and "charts drawn: pie" in pie["tests"][0]["message"]
    crash = await attempt("import pandas as pd\npd.read_csv('missing.csv')\n")
    assert crash["tests"][0]["verdict"] == "RE"
    assert {t["verdict"] for t in (await attempt("print(1)", language="python3"))["tests"]} == {"IE"}


@needs_ml
async def test_plot_answers_cannot_mark_themselves(api):
    """The student's code runs in the same process as the figure report. Checks are judged on
    the server, so replacing the judge or printing a fake "all passed" earns nothing."""
    h, sid = await _subject(api)
    c = api.client
    r = await c.post(f"/api/v1/subjects/{sid}/questions", headers=h, json={
        "title": "Bar", "statement_md": "x", "difficulty": "easy", "grader": "plot", "time_limit_ms": 8000,
        "reference_language": "python3-ml",
        "reference_code": "import matplotlib.pyplot as plt\nplt.bar(['a', 'b'], [1, 2])\nplt.title('Sales')\n"})
    qid = r.json()["id"]
    await c.post(f"/api/v1/questions/{qid}/tests", headers=h, json={"tests": [
        {"input": "chart bar\ntitle contains sales\ncount bars = 2"}]})
    assert (await c.post(f"/api/v1/questions/{qid}/validate", headers=h)).json()["fraction"] == 1.0
    for cheat in (
        # 1. rewrite the judge that used to run in the sandbox
        "import sys\nm = sys.modules['__main__']\n"
        "m.judge_all = lambda s, t: [dict(check=l, ok=True, detail='') for l in t.splitlines() if l.strip()]\n",
        # 2. print a fake result and stop before the real report
        "import sys, os, json\nsys.__stdout__.write(json.dumps({'results': [{'check': 'x', 'ok': True}]}) + '\\n')\n"
        "sys.__stdout__.flush()\nos._exit(0)\n",
        # 3. read the checks to fake a matching report: the checks are not in the sandbox
        "import sys, os\nchecks = sys.stdin.read()\nassert checks == '', checks\n"
        "sys.__stdout__.write('AEGIS_PLOT:{\"axes\": []}\\n'); sys.__stdout__.flush(); os._exit(0)\n",
    ):
        out = (await c.post(f"/api/v1/questions/{qid}/try", headers=h,
                            json={"language": "python3-ml", "code": cheat})).json()
        assert out["fraction"] == 0.0, out
