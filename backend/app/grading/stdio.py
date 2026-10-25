"""The standard grader: feed each test's input on stdin, compare stdout with the
expected output. Works the same for every language, so one test set covers C, C++,
Java and Python answers alike."""

from __future__ import annotations

from typing import Any

from .compare import CompareMode, outputs_match
from .executor import ExecResult
from .registry import Check, Grader, TestSpec, Verdict, _base_config, register


class StdioGrader(Grader):
    key = "stdio"
    description = "Program reads stdin and prints to stdout; output compared per test."

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        out = _base_config(config, {"compare", "tolerance"})
        compare = config.get("compare", CompareMode.lines.value)
        try:
            CompareMode(compare)
        except ValueError:
            raise ValueError(f"compare must be one of {[m.value for m in CompareMode]}") from None
        tolerance = float(config.get("tolerance", 1e-6))
        if not 0 <= tolerance <= 1:
            raise ValueError("tolerance must be between 0 and 1")
        return {**out, "compare": compare, "tolerance": tolerance}

    def validate_language(self, language_key: str) -> None:
        if language_key == "sql":
            raise ValueError("SQL answers need a question with the 'sql' grader")

    def check(self, test: TestSpec, result: ExecResult, config: dict[str, Any]) -> Check:
        if outputs_match(result.stdout, test.expected or "", config["compare"], tolerance=config["tolerance"]):
            return Check(Verdict.accepted, 1.0)
        return Check(Verdict.wrong_answer, 0.0)


register(StdioGrader())
