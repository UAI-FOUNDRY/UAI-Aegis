"""Function questions, LeetCode/HackerRank style.

The student writes only a function; no input parsing or printing:

    def solve(nums, target):          # Python
        ...
        return [i, j]

    function solve(nums, target) {    // JavaScript
        ...
        return [i, j];
    }

Each test's input is the list of arguments as JSON, e.g. ``[[2, 7, 11, 15], 9]``, and the
expected output is the returned value as JSON, e.g. ``[0, 1]``. Expected values are
normally generated from the reference solution. A small harness inside the sandbox calls
the function and prints the returned value; the comparison happens on the server, so
hidden expected values never reach the sandbox.

grader_config:
  function_name  name of the function to call (default "solve")
  params         optional parameter names, e.g. ["nums", "target"]: used for the starter
                 code students see, and every test must have that many arguments
  compare        "exact" (default; 2 == 2.0), "float" (numbers within tolerance) or
                 "unordered" (the returned list may be in any order)
  tolerance      for "float", default 1e-6

Languages: Python and JavaScript. C, C++ and Java need typed signatures; use a stdio
question for those.
"""

from __future__ import annotations

import json
import keyword
import math
import re
from typing import Any

from .executor import ExecRequest, ExecResult
from .registry import Check, Grader, RunTarget, TestSpec, Verdict, _base_config, register

RESULT_MARK = "AEGIS_RETURN:"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_JS_RESERVED = {
    "break", "case", "catch", "class", "const", "continue", "debugger", "default", "delete", "do", "else",
    "export", "extends", "false", "finally", "for", "function", "if", "import", "in", "instanceof", "let",
    "new", "null", "return", "super", "switch", "this", "throw", "true", "try", "typeof", "var", "void",
    "while", "with", "yield", "await", "enum", "static", "arguments", "eval", "require", "module", "process",
}
COMPARE_MODES = ("exact", "float", "unordered")

# The student's prints go to stderr (shown as their log), so only the harness writes the
# result line. The function name is validated as a plain identifier before it gets here.
_PY_HARNESS = '''import contextlib, json, sys
_args = json.loads(sys.stdin.read())
_source = {source!r}
_ns = {{"__name__": "solution"}}


def _plain(value, depth=0):
    if depth > 200:
        raise ValueError("the returned value is nested too deeply")
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        value = value.tolist()  # numpy arrays
    elif hasattr(value, "item") and not isinstance(value, (str, bytes, list, tuple, dict)):
        value = value.item()  # numpy scalars
    if isinstance(value, (list, tuple)):
        return [_plain(v, depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [_plain(v, depth + 1) for v in value]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, default=str))
    if isinstance(value, dict):
        return {{str(k): _plain(v, depth + 1) for k, v in value.items()}}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


with contextlib.redirect_stdout(sys.stderr):
    exec(compile(_source, "solution.py", "exec"), _ns)
    _fn = _ns.get({name!r})
    if not callable(_fn):
        print("Define a function {name}(...) that returns the answer.")
        raise SystemExit(1)
    _result = _plain(_fn(*_args))
print({mark!r} + json.dumps(_result))
'''

_JS_PRELUDE = '''const __aegisOut = process.stdout.write.bind(process.stdout);
console.log = (...a) => process.stderr.write(a.map(String).join(" ") + "\\n");
process.stdout.write = (chunk, ...rest) => process.stderr.write(chunk, ...rest);
'''

_JS_FOOTER = '''
;(() => {{
  const __args = JSON.parse(require("fs").readFileSync(0, "utf8"));
  if (typeof {name} !== "function") {{
    process.stderr.write("Define a function {name}(...) that returns the answer.\\n");
    process.exit(1);
  }}
  Promise.resolve({name}(...__args)).then((r) => {{
    __aegisOut({mark} + JSON.stringify(r === undefined ? null : r) + "\\n");
  }}, (e) => {{
    process.stderr.write(String((e && e.stack) || e) + "\\n");
    process.exit(1);
  }});
}})();
'''


def _family(language_key: str) -> str | None:
    if language_key.startswith("python"):
        return "python"
    if language_key in ("javascript", "nodejs"):
        return "javascript"
    return None


def parse_arguments(text: str) -> list[Any]:
    try:
        args = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("the input must be JSON: the list of arguments, e.g. [[2, 7, 11, 15], 9]") from None
    if not isinstance(args, list):
        raise ValueError("the input must be a JSON list of arguments, e.g. [[2, 7, 11, 15], 9]")
    return args


def _returned(stdout: str) -> Any:
    lines = [line for line in stdout.splitlines() if line.startswith(RESULT_MARK)]
    if not lines:
        raise ValueError("the function did not return a value")
    return json.loads(lines[-1][len(RESULT_MARK):])


def _same(got: Any, want: Any, mode: str, tolerance: float) -> bool:
    if isinstance(want, bool) or isinstance(got, bool):
        return type(got) is type(want) and got == want  # true is not 1
    if isinstance(want, int | float) and isinstance(got, int | float):
        if mode == "float":
            return math.isclose(got, want, rel_tol=tolerance, abs_tol=tolerance)
        return got == want
    if isinstance(want, list) and isinstance(got, list):
        return len(got) == len(want) and all(_same(g, w, mode, tolerance) for g, w in zip(got, want, strict=True))
    if isinstance(want, dict) and isinstance(got, dict):
        return got.keys() == want.keys() and all(_same(got[k], want[k], mode, tolerance) for k in want)
    return got == want


def _sort_key(value: Any) -> str:
    def norm(v: Any) -> Any:
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, float):
            return round(v, 9)
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items()}
        return v

    return json.dumps(norm(value), sort_keys=True, default=str)


def values_match(got: Any, want: Any, mode: str = "exact", tolerance: float = 1e-6) -> bool:
    if mode == "unordered" and isinstance(got, list) and isinstance(want, list):
        if len(got) != len(want):
            return False
        return all(_same(g, w, "exact", tolerance)
                   for g, w in zip(sorted(got, key=_sort_key), sorted(want, key=_sort_key), strict=True))
    return _same(got, want, mode, tolerance)


class FunctionGrader(Grader):
    key = "function"
    description = "Student writes a function (Python or JavaScript); its return value is compared per test."

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        out = _base_config(config, {"function_name", "params", "compare", "tolerance"})
        name = config.get("function_name", "solve")
        if not isinstance(name, str) or not _IDENTIFIER.match(name) or keyword.iskeyword(name) \
                or name in _JS_RESERVED or name.startswith("_"):
            raise ValueError("function_name must be a plain name like solve or two_sum")
        params = config.get("params")
        if params is not None:
            if not isinstance(params, list) or len(params) > 20 or not all(
                    isinstance(p, str) and _IDENTIFIER.match(p) and not keyword.iskeyword(p)
                    and p not in _JS_RESERVED for p in params):
                raise ValueError('params must be a list of plain names, e.g. ["nums", "target"]')
            if len(set(params)) != len(params):
                raise ValueError("params must not repeat a name")
        compare = config.get("compare", "exact")
        if compare not in COMPARE_MODES:
            raise ValueError(f"compare must be one of {list(COMPARE_MODES)}")
        tolerance = float(config.get("tolerance", 1e-6))
        if not 0 <= tolerance <= 1:
            raise ValueError("tolerance must be between 0 and 1")
        return {**out, "function_name": name, "params": params, "compare": compare, "tolerance": tolerance}

    def validate_language(self, language_key: str) -> None:
        if _family(language_key) is None:
            raise ValueError("Function questions are answered in Python or JavaScript; "
                             "use a stdio question for C, C++ or Java")

    def validate_test_input(self, text: str, config: dict[str, Any]) -> None:
        args = parse_arguments(text)
        params = config.get("params")
        if params is not None and len(args) != len(params):
            raise ValueError(f"expected {len(params)} argument(s) ({', '.join(params)}), got {len(args)}")

    def validate_expected(self, text: str, config: dict[str, Any]) -> str:
        try:
            return json.dumps(json.loads(text))
        except json.JSONDecodeError:
            raise ValueError('the expected output must be JSON, e.g. [0, 1] or "abc" or 42') from None

    def default_starter(self, language_key: str, config: dict[str, Any]) -> str | None:
        name = config.get("function_name", "solve")
        params = ", ".join(config.get("params") or [])
        family = _family(language_key)
        if family == "python":
            return f"def {name}({params}):\n    # return the answer (do not print it)\n    pass\n"
        if family == "javascript":
            return f"function {name}({params}) {{\n    // return the answer (do not print it)\n}}\n"
        return None

    def prepare(self, target: RunTarget, test: TestSpec, config: dict[str, Any]) -> ExecRequest:
        name = config["function_name"]
        family = _family(target.language_key)
        if family == "python":
            source = _PY_HARNESS.format(source=target.source, name=name, mark=RESULT_MARK)
        elif family == "javascript":
            source = _JS_PRELUDE + target.source + _JS_FOOTER.format(name=name, mark=json.dumps(RESULT_MARK))
        else:
            raise ValueError(f"'{target.language_key}' cannot run function questions")
        parse_arguments(test.input)  # a malformed test is our error, not the student's
        return ExecRequest(
            language_key=target.language_key, judge0_id=target.judge0_id, file_name=target.file_name,
            source=source, stdin=test.input, time_limit_ms=target.time_limit_ms,
            memory_limit_kb=target.memory_limit_kb,
        )

    def expected_from_output(self, stdout: str, config: dict[str, Any]) -> str:
        try:
            return json.dumps(_returned(stdout))
        except json.JSONDecodeError:
            raise ValueError("the reference solution's return value is not JSON") from None

    def shown_stdout(self, result: ExecResult) -> str:
        try:
            return json.dumps(_returned(result.stdout))  # only ever shown for sample tests
        except (ValueError, json.JSONDecodeError):
            return ""

    def check(self, test: TestSpec, result: ExecResult, config: dict[str, Any]) -> Check:
        try:
            got = _returned(result.stdout)
        except (ValueError, json.JSONDecodeError):
            return Check(Verdict.wrong_answer, 0.0, "the function did not return a value")
        try:
            want = json.loads(test.expected or "null")
        except json.JSONDecodeError:
            return Check(Verdict.internal_error, 0.0, "the expected value is not valid JSON")
        if values_match(got, want, config["compare"], config["tolerance"]):
            return Check(Verdict.accepted, 1.0)
        # No expected value in the message: it is also shown for hidden tests.
        return Check(Verdict.wrong_answer, 0.0, "wrong return value")


register(FunctionGrader())
