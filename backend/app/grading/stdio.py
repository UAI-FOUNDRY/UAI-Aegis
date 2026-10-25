"""The standard grader: feed each test's input on stdin, compare stdout with the
expected output. Works the same for every language, so one test set covers C, C++,
Java and Python answers alike."""

from __future__ import annotations

from typing import Any

from .compare import CompareMode, outputs_match
from .executor import ExecRequest, ExecStatus, Executor
from .registry import Grader, GradeResult, RunTarget, TestOutcome, TestSpec, Verdict, register

_SHOWN_OUTPUT_CHARS = 4000

_STATUS_TO_VERDICT = {
    ExecStatus.time_limit: Verdict.time_limit,
    ExecStatus.memory_limit: Verdict.memory_limit,
    ExecStatus.runtime_error: Verdict.runtime_error,
    ExecStatus.compile_error: Verdict.compile_error,
    ExecStatus.internal_error: Verdict.internal_error,
}


class StdioGrader(Grader):
    key = "stdio"
    description = "Program reads stdin and prints to stdout; output compared per test."

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        allowed = {"compare", "tolerance", "scoring"}
        unknown = set(config) - allowed
        if unknown:
            raise ValueError(f"unknown grader_config keys: {', '.join(sorted(unknown))}")
        compare = config.get("compare", CompareMode.lines.value)
        try:
            CompareMode(compare)
        except ValueError:
            raise ValueError(f"compare must be one of {[m.value for m in CompareMode]}") from None
        tolerance = float(config.get("tolerance", 1e-6))
        if not 0 <= tolerance <= 1:
            raise ValueError("tolerance must be between 0 and 1")
        scoring = config.get("scoring", "sum")
        if scoring not in ("sum", "groups_all_or_nothing"):
            raise ValueError("scoring must be 'sum' or 'groups_all_or_nothing'")
        return {"compare": compare, "tolerance": tolerance, "scoring": scoring}

    async def grade(
        self, executor: Executor, target: RunTarget, tests: list[TestSpec], config: dict[str, Any]
    ) -> GradeResult:
        config = self.validate_config(config or {})
        if not tests:
            return GradeResult(fraction=0.0)
        requests = [
            ExecRequest(
                language_key=target.language_key,
                judge0_id=target.judge0_id,
                file_name=target.file_name,
                source=target.source,
                stdin=t.input,
                time_limit_ms=target.time_limit_ms,
                memory_limit_kb=target.memory_limit_kb,
            )
            for t in tests
        ]
        results = await executor.run_batch(requests)
        outcomes: list[TestOutcome] = []
        compile_output = ""
        for test, res in zip(tests, results, strict=True):
            if res.status is ExecStatus.ok:
                if test.expected is None:
                    verdict = Verdict.internal_error
                elif outputs_match(res.stdout, test.expected, config["compare"], tolerance=config["tolerance"]):
                    verdict = Verdict.accepted
                else:
                    verdict = Verdict.wrong_answer
            else:
                verdict = _STATUS_TO_VERDICT[res.status]
                if res.status is ExecStatus.compile_error:
                    compile_output = res.compile_output[:_SHOWN_OUTPUT_CHARS]
            outcomes.append(
                TestOutcome(
                    test_id=test.id,
                    ordinal=test.ordinal,
                    is_sample=test.is_sample,
                    group=test.group,
                    weight=test.weight,
                    verdict=verdict,
                    time_ms=res.time_ms,
                    memory_kb=res.memory_kb,
                    stdout=res.stdout[:_SHOWN_OUTPUT_CHARS],
                    stderr=(res.stderr or res.message)[:_SHOWN_OUTPUT_CHARS],
                )
            )
        return GradeResult(fraction=score(outcomes, config["scoring"]), tests=outcomes,
                           compile_output=compile_output)


def score(outcomes: list[TestOutcome], scoring: str = "sum") -> float:
    total = sum(o.weight for o in outcomes)
    if total <= 0:
        return 0.0
    if scoring == "groups_all_or_nothing":
        groups: dict[str, list[TestOutcome]] = {}
        for o in outcomes:
            groups.setdefault(o.group, []).append(o)
        earned = sum(
            sum(o.weight for o in members)
            for members in groups.values()
            if all(o.verdict == Verdict.accepted for o in members)
        )
    else:
        earned = sum(o.weight for o in outcomes if o.verdict == Verdict.accepted)
    return round(earned / total, 6)


register(StdioGrader())
