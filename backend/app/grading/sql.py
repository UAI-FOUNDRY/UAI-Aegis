"""SQL questions, run on SQLite inside the sandbox.

Two modes (grader_config "mode"):
- "query" (default): the student writes one SELECT. Its result set is compared with the
  reference query's result.
- "script": the student writes statements that change data (INSERT/UPDATE/DELETE/CREATE...).
  Afterwards the professor's check_query runs, and its result is compared.

grader_config:
  schema_sql         CREATE TABLE statements shared by every test
  check_query        script mode only: the SELECT whose result is compared
  order_matters      compare rows in order (default false: any order)
  check_column_names also compare column names (default false)

Each test's input is the data for that test (INSERT statements), so hidden tests can
use different data from the samples and hard-coded answers fail. The answer runs inside
a small Python harness in the sandbox; the comparison happens on the server.
"""

from __future__ import annotations

import json
from typing import Any

from .executor import ExecRequest, ExecResult
from .registry import Check, Grader, RunTarget, TestSpec, Verdict, _base_config, register

_HARNESS = '''import json, sqlite3, sys
SCHEMA = {schema!r}
ANSWER = {answer!r}
CHECK = {check!r}
MAX_ROWS = 10000
data = sys.stdin.read()
db = sqlite3.connect(":memory:")
try:
    db.executescript(SCHEMA)
    db.executescript(data)
except sqlite3.Error as exc:
    print("Test data could not be loaded: " + str(exc), file=sys.stderr)
    sys.exit(3)

def guard(action, a, b, c, d):
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK

db.set_authorizer(guard)
try:
    if CHECK is None:
        cur = db.execute(ANSWER.strip().rstrip(";").strip())
    else:
        db.executescript(ANSWER)
        db.commit()
        cur = db.execute(CHECK)
except sqlite3.Error as exc:
    print("SQL error: " + str(exc), file=sys.stderr)
    sys.exit(1)
columns = [d[0] for d in (cur.description or [])]
rows = cur.fetchmany(MAX_ROWS + 1)
if len(rows) > MAX_ROWS:
    print("The query returned more than 10000 rows.", file=sys.stderr)
    sys.exit(1)
print(json.dumps({{"columns": columns, "rows": [[v.hex() if isinstance(v, bytes) else v for v in r] for r in rows]}}))
'''


def _parse(text: str) -> dict[str, Any]:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        raise ValueError("no result")
    data = json.loads(lines[-1])
    if not isinstance(data, dict) or "columns" not in data or "rows" not in data:
        raise ValueError("malformed result")
    return data


def _cell(value: Any) -> Any:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return round(float(value), 6)  # 3 == 3.0, tiny float noise ignored
    return value


def _rows(result: dict[str, Any], order_matters: bool) -> list[list[Any]]:
    rows = [[_cell(v) for v in row] for row in result["rows"]]
    return rows if order_matters else sorted(rows, key=lambda r: json.dumps(r, default=str))


class SqlGrader(Grader):
    key = "sql"
    description = "SQL on SQLite: a SELECT whose result is compared, or statements checked by a query."

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        out = _base_config(config, {"mode", "schema_sql", "check_query", "order_matters", "check_column_names"})
        mode = config.get("mode", "query")
        if mode not in ("query", "script"):
            raise ValueError("mode must be 'query' or 'script'")
        check_query = config.get("check_query")
        if mode == "script" and not (isinstance(check_query, str) and check_query.strip()):
            raise ValueError("script mode needs a check_query (the SELECT that shows the result)")
        schema = config.get("schema_sql", "")
        if not isinstance(schema, str) or len(schema) > 200_000:
            raise ValueError("schema_sql must be text under 200 KB")
        return {**out, "mode": mode, "schema_sql": schema,
                "check_query": check_query if mode == "script" else None,
                "order_matters": bool(config.get("order_matters", False)),
                "check_column_names": bool(config.get("check_column_names", False))}

    def validate_language(self, language_key: str) -> None:
        if language_key != "sql":
            raise ValueError("SQL questions must be answered in the 'sql' language")

    def prepare(self, target: RunTarget, test: TestSpec, config: dict[str, Any]) -> ExecRequest:
        if target.python is None:
            raise ValueError("the python3 language is needed to run SQL questions")
        source = _HARNESS.format(schema=config["schema_sql"], answer=target.source, check=config["check_query"])
        return ExecRequest(
            language_key=target.python.key, judge0_id=target.python.judge0_id, file_name=target.python.file_name,
            source=source, stdin=test.input, time_limit_ms=target.time_limit_ms,
            memory_limit_kb=target.memory_limit_kb,
        )

    def expected_from_output(self, stdout: str, config: dict[str, Any]) -> str:
        return json.dumps(_parse(stdout))

    def check(self, test: TestSpec, result: ExecResult, config: dict[str, Any]) -> Check:
        try:
            got = _parse(result.stdout)
            want = _parse(test.expected or "")
        except (ValueError, json.JSONDecodeError):
            return Check(Verdict.wrong_answer, 0.0, "no result was produced")
        if len(got["columns"]) != len(want["columns"]):
            return Check(Verdict.wrong_answer, 0.0,
                         f"expected {len(want['columns'])} column(s), got {len(got['columns'])}")
        if config["check_column_names"] and [c.lower() for c in got["columns"]] != [c.lower() for c in want["columns"]]:
            return Check(Verdict.wrong_answer, 0.0, "column names differ: " + ", ".join(want["columns"]))
        if len(got["rows"]) != len(want["rows"]):
            return Check(Verdict.wrong_answer, 0.0, f"expected {len(want['rows'])} row(s), got {len(got['rows'])}")
        if _rows(got, config["order_matters"]) != _rows(want, config["order_matters"]):
            return Check(Verdict.wrong_answer, 0.0, "the rows differ" + (" (order matters)" if config["order_matters"] else ""))
        return Check(Verdict.accepted, 1.0)


register(SqlGrader())
