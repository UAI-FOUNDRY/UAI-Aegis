"""Plot questions (matplotlib / seaborn): the student's code draws a chart; each test is a
list of checks on the figure it drew.

The checks never enter the sandbox. The sandbox runs the student's code and reports what
the figure contains (chart kinds, titles, labels, counts); the server then judges that
report against the test's checks. The student's code runs in the same process as the
report, so it must never be the place where verdicts are decided: code that rewrote the
judge or printed "all checks passed" would otherwise get full marks without drawing.

Answers are written in 'python3-ml' (the runtime with matplotlib and seaborn). The code runs
off-screen (Agg backend) in the sandbox; plt.show() does nothing. If the question has data,
it is written to a file (default data.csv) before the code runs, so students can do
pd.read_csv("data.csv"). Checks, one per line:

    chart KIND                    some chart of this kind was drawn: bar, hist, line, scatter,
                                  pie, box, violin, heatmap, area, kde
    title contains WORDS          a chart title (or the figure title) contains WORDS (any case)
    title = WORDS                 ... is exactly WORDS
    xlabel contains WORDS / xlabel = WORDS
    ylabel contains WORDS / ylabel = WORDS
    legend                        a legend is shown
    xticks include A, B, C        the x-axis tick labels include all of these (any case)
    count KIND = N                also ==, >=, <=, >, <. KIND: figures, axes, bars, lines,
                                  points, wedges
    # comment lines and blank lines are ignored

grader_config:
  data_csv        optional text written to data_file before the student's code runs
  data_file       file name for it (default data.csv)
  partial_credit  true (default): a test earns the share of its checks that pass
"""

from __future__ import annotations

import json
import re
from typing import Any

from .checklist import results_text, verdict_from_results
from .executor import ExecRequest, ExecResult
from .registry import Check, Grader, RunTarget, TestSpec, Verdict, _base_config, register

MAX_DATA_BYTES = 2 * 1024 * 1024
SUMMARY_MARK = "AEGIS_PLOT:"
_COUNTS = ("bars", "lines", "points", "wedges")
_MAX_AXES = 200
_MAX_TEXT = 500

# Parsing and judging checks: plain Python, run only on the server. The figure itself is
# summarised in the sandbox by _SANDBOX below.
_CHECKS = r'''
import json
import re
import sys

KINDS = {"bar", "hist", "line", "scatter", "pie", "box", "violin", "heatmap", "area", "kde"}
COUNTABLE = {"figures", "axes", "bars", "lines", "points", "wedges"}
_OPS = {"=": lambda a, b: a == b, "==": lambda a, b: a == b, ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}
MAX_CHECKS = 100


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_checks(text):
    checks = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        word = words[0].lower()
        try:
            if word == "chart":
                if len(words) != 2 or words[1].lower() not in KINDS:
                    raise ValueError("write: chart KIND, KIND one of " + ", ".join(sorted(KINDS)))
                check = {"kind": "chart", "chart": words[1].lower()}
            elif word in ("title", "xlabel", "ylabel"):
                m = re.match(r"^\w+\s+(contains|=)\s+(.+)$", line)
                if not m:
                    raise ValueError("write: %s contains WORDS  (or %s = WORDS)" % (word, word))
                check = {"kind": word, "op": m.group(1), "value": _unquote(m.group(2))}
            elif word == "legend":
                if len(words) != 1:
                    raise ValueError("write just: legend")
                check = {"kind": "legend"}
            elif word == "xticks":
                m = re.match(r"^xticks\s+include\s+(.+)$", line)
                if not m:
                    raise ValueError("write: xticks include A, B, C")
                values = [_unquote(v) for v in m.group(1).split(",") if v.strip()]
                if not values:
                    raise ValueError("list at least one tick label")
                check = {"kind": "xticks", "values": values}
            elif word == "count":
                m = re.match(r"^count\s+(\w+)\s*(==|=|>=|<=|>|<)\s*(\d+)$", line)
                if not m or m.group(1).lower() not in COUNTABLE:
                    raise ValueError("write: count WHAT = N, WHAT one of " + ", ".join(sorted(COUNTABLE)))
                check = {"kind": "count", "what": m.group(1).lower(), "op": m.group(2), "n": int(m.group(3))}
            else:
                raise ValueError("unknown check '%s' (use chart, title, xlabel, ylabel, legend, xticks or count)"
                                 % word)
        except ValueError as exc:
            raise ValueError("line %d: %s" % (number, exc))
        check["line"] = line
        checks.append(check)
    if not checks:
        raise ValueError("write at least one check, e.g.: chart bar")
    if len(checks) > MAX_CHECKS:
        raise ValueError("at most %d checks per test" % MAX_CHECKS)
    return checks


def _norm(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def judge(check, summary):
    """summary: {"figures": n, "suptitles": [...], "axes": [{title, xlabel, ylabel, legend, kinds,
    bars, lines, points, wedges, xticks}], "figure_legend": bool}"""
    axes = summary["axes"]
    kind = check["kind"]
    if kind == "chart":
        ok = any(check["chart"] in ax["kinds"] for ax in axes)
        drawn = sorted({k for ax in axes for k in ax["kinds"]})
        return ok, "" if ok else "charts drawn: " + (", ".join(drawn) or "none")
    if kind in ("title", "xlabel", "ylabel"):
        texts = [ax[kind] for ax in axes if ax[kind]]
        if kind == "title":
            texts += [t for t in summary["suptitles"] if t]
        want = _norm(check["value"])
        if check["op"] == "contains":
            ok = any(want.lower() in _norm(t).lower() for t in texts)
        else:
            ok = any(_norm(t) == want for t in texts)
        return ok, "" if ok else ("%s was: %s" % (kind, " | ".join(_norm(t)[:50] for t in texts[:3])) if texts
                                  else "no %s set" % kind)
    if kind == "legend":
        ok = summary["figure_legend"] or any(ax["legend"] for ax in axes)
        return ok, "" if ok else "no legend"
    if kind == "xticks":
        have = {_norm(t).lower() for ax in axes for t in ax["xticks"]}
        missing = [v for v in check["values"] if _norm(v).lower() not in have]
        return not missing, "" if not missing else "missing: " + ", ".join(missing)
    if kind == "count":
        what = check["what"]
        if what == "figures":
            n = summary["figures"]
        elif what == "axes":
            n = len(axes)
        else:
            n = sum(ax[what] for ax in axes)
        ok = _OPS[check["op"]](n, check["n"])
        return ok, "" if ok else "found %d" % n
    return False, "unknown check"


def judge_all(summary, checks_text):
    return [dict(check=c["line"], ok=bool(ok), detail=detail)
            for c in parse_checks(checks_text) for ok, detail in [judge(c, summary)]]
'''

# Runs in the sandbox: draws the student's figure off-screen and summarises it.
_SANDBOX = r'''
import contextlib
import functools
import json
import os
import sys
import warnings

os.environ["MPLBACKEND"] = "Agg"
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):  # sandbox thread limit
    os.environ.setdefault(_v, "1")
if not os.access(os.environ.get("MPLCONFIGDIR") or os.path.expanduser("~"), os.W_OK):
    import tempfile
    os.environ["MPLCONFIGDIR"] = tempfile.mkdtemp()  # matplotlib needs a writable cache folder
warnings.filterwarnings("ignore")
if DATA is not None:
    with open(DATA_FILE, "w", encoding="utf-8") as _f:
        _f.write(DATA)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.collections import PathCollection
from matplotlib.container import BarContainer
from matplotlib.patches import Wedge

_KINDS = {}


def _tag(ax, kind):
    if isinstance(ax, Axes):
        _KINDS.setdefault(id(ax), set()).add(kind)


def _wrap_axes(name, kind):
    original = getattr(Axes, name, None)
    if original is None:
        return

    @functools.wraps(original)  # seaborn looks at method names (e.g. "plot") to pick colours
    def wrapper(self, *args, **kwargs):
        _tag(self, kind)
        return original(self, *args, **kwargs)

    setattr(Axes, name, wrapper)


for _name, _kind in [("bar", "bar"), ("barh", "bar"), ("hist", "hist"), ("scatter", "scatter"), ("plot", "line"),
                     ("pie", "pie"), ("boxplot", "box"), ("bxp", "box"), ("violinplot", "violin"),
                     ("imshow", "heatmap"), ("pcolormesh", "heatmap"), ("fill_between", "area"),
                     ("stackplot", "area")]:
    _wrap_axes(_name, _kind)

try:  # seaborn draws through the Axes methods above; these add what the chart *means*
    import seaborn as _sns

    def _wrap_sns(name, kind):
        original = getattr(_sns, name, None)
        if original is None:
            return

        @functools.wraps(original)
        def wrapper(*args, **kwargs):
            result = original(*args, **kwargs)
            targets = [result] if isinstance(result, Axes) else list(getattr(getattr(result, "axes", None), "flat", []))
            for ax in targets or [plt.gca()]:
                _tag(ax, kind)
            return result

        setattr(_sns, name, wrapper)

    for _name, _kind in [("histplot", "hist"), ("displot", "hist"), ("kdeplot", "kde"), ("countplot", "bar"),
                         ("barplot", "bar"), ("boxplot", "box"), ("violinplot", "violin"), ("heatmap", "heatmap"),
                         ("scatterplot", "scatter"), ("lineplot", "line"), ("regplot", "scatter"),
                         ("pairplot", "scatter")]:
        _wrap_sns(_name, _kind)
except Exception:  # seaborn not installed: matplotlib checks still work
    pass

plt.show = lambda *args, **kwargs: None  # nothing to show off-screen


def summarise():
    figures = [plt.figure(n) for n in plt.get_fignums()]
    axes, suptitles, figure_legend = [], [], False
    for fig in figures:
        with contextlib.suppress(Exception):
            fig.canvas.draw()  # fills in tick labels
        suptitle = getattr(fig, "_suptitle", None)
        suptitles.append(suptitle.get_text() if suptitle else "")
        figure_legend = figure_legend or bool(getattr(fig, "legends", []))
        for ax in fig.axes:
            axes.append({
                "title": " ".join(t for t in (ax.get_title("left"), ax.get_title(), ax.get_title("right")) if t),
                "xlabel": ax.get_xlabel(),
                "ylabel": ax.get_ylabel(),
                "legend": ax.get_legend() is not None,
                "kinds": sorted(_KINDS.get(id(ax), set())),
                "bars": sum(len(c) for c in ax.containers if isinstance(c, BarContainer)),
                "lines": len(ax.get_lines()),
                "points": sum(len(c.get_offsets()) for c in ax.collections if isinstance(c, PathCollection)),
                "wedges": sum(1 for p in ax.patches if isinstance(p, Wedge)),
                "xticks": [t.get_text() for t in ax.get_xticklabels() if t.get_text()],
            })
    return {"figures": len(figures), "suptitles": suptitles, "axes": axes, "figure_legend": figure_legend}


with contextlib.redirect_stdout(sys.stderr):  # the student's prints go to the log, not the result
    exec(compile(SOURCE, "solution.py", "exec"), {"__name__": "__main__"})
print(MARK + json.dumps(summarise()))
'''

_NS: dict[str, Any] = {}
exec(compile(_CHECKS, "plot_checks", "exec"), _NS)  # noqa: S102 - our own constant code, no user input
parse_checks = _NS["parse_checks"]
judge_all = _NS["judge_all"]


def harness(source: str, config: dict[str, Any]) -> str:
    """The Python program run in the sandbox for one answer (no checks in it)."""
    return ("SOURCE = " + repr(source) + "\nDATA = " + repr(config.get("data_csv"))
            + "\nDATA_FILE = " + repr(config["data_file"]) + "\nMARK = " + repr(SUMMARY_MARK) + "\n" + _SANDBOX)


def _text(value: Any) -> str:
    return value[:_MAX_TEXT] if isinstance(value, str) else ""


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**7 else 0


def read_summary(stdout: str) -> dict[str, Any]:
    """The figure report printed by the sandbox, rebuilt with only the fields and types the
    judge uses (the student's code could print anything)."""
    lines = [line for line in stdout.splitlines() if line.startswith(SUMMARY_MARK)]
    if not lines:
        raise ValueError("no figure report")
    raw = json.loads(lines[-1][len(SUMMARY_MARK):])
    if not isinstance(raw, dict) or not isinstance(raw.get("axes"), list):
        raise ValueError("malformed figure report")
    axes = []
    for ax in raw["axes"][:_MAX_AXES]:
        if not isinstance(ax, dict):
            raise ValueError("malformed figure report")
        kinds = ax.get("kinds") if isinstance(ax.get("kinds"), list) else []
        ticks = ax.get("xticks") if isinstance(ax.get("xticks"), list) else []
        axes.append({
            "title": _text(ax.get("title")), "xlabel": _text(ax.get("xlabel")), "ylabel": _text(ax.get("ylabel")),
            "legend": ax.get("legend") is True,
            "kinds": [k for k in kinds if isinstance(k, str)][:20],
            "xticks": [_text(t) for t in ticks[:500] if isinstance(t, str)],
            **{name: _count(ax.get(name)) for name in _COUNTS},
        })
    suptitles = raw.get("suptitles") if isinstance(raw.get("suptitles"), list) else []
    return {"figures": _count(raw.get("figures")), "suptitles": [_text(t) for t in suptitles[:_MAX_AXES]],
            "figure_legend": raw.get("figure_legend") is True, "axes": axes}


def describe(summary: dict[str, Any]) -> str:
    """A short, readable account of what was drawn (shown instead of the raw report)."""
    if not summary["axes"]:
        return "No chart was drawn."
    parts = []
    for i, ax in enumerate(summary["axes"], start=1):
        bits = [", ".join(ax["kinds"]) or "empty axes"]
        for name in ("title", "xlabel", "ylabel"):
            if ax[name]:
                bits.append(f"{name} '{ax[name][:60]}'")
        parts.append(f"Chart {i}: " + "; ".join(bits))
    return "\n".join(parts)


class PlotGrader(Grader):
    key = "plot"
    description = "Charts with matplotlib/seaborn: the code draws a figure; each test is a list of checks on it."
    needs_expected = False
    can_generate_expected = False

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        out = _base_config(config, {"data_csv", "data_file", "partial_credit"})
        data = config.get("data_csv")
        if data is not None and (not isinstance(data, str) or len(data.encode("utf-8")) > MAX_DATA_BYTES):
            raise ValueError("data_csv must be text under 2 MB")
        data_file = config.get("data_file") or "data.csv"
        if not isinstance(data_file, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", data_file) or ".." in data_file:
            raise ValueError("data_file must be a simple file name such as data.csv")
        return {**out, "data_csv": data or None, "data_file": data_file,
                "partial_credit": bool(config.get("partial_credit", True))}

    def validate_language(self, language_key: str) -> None:
        if language_key != "python3-ml":
            raise ValueError("plot questions must be answered in 'python3-ml' (it has matplotlib and seaborn)")

    def validate_test_input(self, text: str, config: dict[str, Any]) -> None:
        parse_checks(text)

    def prepare(self, target: RunTarget, test: TestSpec, config: dict[str, Any]) -> ExecRequest:
        parse_checks(test.input)  # a malformed test is our error, not the student's
        return ExecRequest(
            language_key=target.language_key, judge0_id=target.judge0_id, file_name=target.file_name,
            source=harness(target.source, config), stdin="",  # the checks stay on the server
            time_limit_ms=target.time_limit_ms, memory_limit_kb=target.memory_limit_kb,
        )

    def _judge(self, test: TestSpec, result: ExecResult) -> list[dict[str, Any]] | None:
        try:
            return judge_all(read_summary(result.stdout), test.input)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def shown_output(self, test: TestSpec, result: ExecResult, config: dict[str, Any]) -> str:
        results = self._judge(test, result)
        if results is None:
            return ""
        return results_text(results) + "\n\n" + describe(read_summary(result.stdout))

    def check(self, test: TestSpec, result: ExecResult, config: dict[str, Any]) -> Check:
        results = self._judge(test, result)
        if results is None:
            return Check(Verdict.wrong_answer, 0.0, "the chart could not be checked")
        return verdict_from_results(test, results, config["partial_credit"])


register(PlotGrader())
