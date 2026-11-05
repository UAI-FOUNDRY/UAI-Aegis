"""Shared by graders whose tests are lists of checks (html, plot).

The sandbox harness prints one JSON line: {"results": [{"check": ..., "ok": ..., "detail": ...}]}.
These helpers turn it into a verdict and a readable ✓/✗ list.
"""

from __future__ import annotations

import json
from typing import Any

from .registry import Check, TestSpec, Verdict


def results_from_stdout(stdout: str) -> list[dict[str, Any]]:
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    if not lines:
        raise ValueError("no result")
    data = json.loads(lines[-1])
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("malformed result")
    return data["results"]


def checklist_text(stdout: str) -> str:
    """'✓ check' / '✗ check' lines instead of the harness's JSON (falls back to the raw output)."""
    try:
        return "\n".join(("✓ " if r["ok"] else "✗ ") + r["check"] for r in results_from_stdout(stdout))
    except (ValueError, json.JSONDecodeError, KeyError, TypeError):
        return stdout


def checklist_verdict(test: TestSpec, stdout: str, partial_credit: bool, what: str = "the answer") -> Check:
    """All checks pass = AC. Otherwise partial credit (PA) or WA. Failed checks are named only
    for sample tests, so a hidden test's checks stay hidden."""
    try:
        results = results_from_stdout(stdout)
    except (ValueError, json.JSONDecodeError):
        return Check(Verdict.wrong_answer, 0.0, f"{what} could not be checked")
    return verdict_from_results(test, results, partial_credit)


def results_text(results: list[dict[str, Any]]) -> str:
    return "\n".join(("✓ " if r["ok"] else "✗ ") + r["check"] for r in results)


def verdict_from_results(test: TestSpec, results: list[dict[str, Any]], partial_credit: bool) -> Check:
    total = len(results)
    passed = sum(1 for r in results if r.get("ok"))
    if total == 0:
        return Check(Verdict.internal_error, 0.0, "the test has no checks")
    if passed == total:
        return Check(Verdict.accepted, 1.0, f"all {total} checks passed")
    if test.is_sample:
        failed = [f"{r['check']}" + (f" ({r['detail']})" if r.get("detail") else "") for r in results if not r.get("ok")]
        message = f"{passed} of {total} checks passed. Failed: " + "; ".join(failed)
    else:
        message = f"{passed} of {total} checks passed"
    if partial_credit and passed:
        return Check(Verdict.partial, passed / total, message)
    return Check(Verdict.wrong_answer, 0.0, message)
