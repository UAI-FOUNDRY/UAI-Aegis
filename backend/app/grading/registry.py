"""Grader plug-in registry.

A grader turns (student code + test cases) into per-test verdicts and a score from 0 to 1.
Each question names its grader by key. To add a new question type, write a module with
a Grader subclass, override the hooks you need, call register() on it, and import it in
grading/__init__.py. Nothing else in the system changes.

The hooks:
- validate_config: check and normalise the question's grader_config
- prepare:         turn (student code, one test) into a program to run in the sandbox
- check:           judge one finished run against the test (on the server, never in
                   the sandbox, so hidden expected outputs and labels never reach it)
- expected_from_output: what to store as the expected output when a reference
                   solution's run is used to generate it
- validate_test_input / validate_expected: check a test a professor types in (e.g.
                   function questions need JSON arguments), so mistakes show up when
                   the test is saved instead of during the exam
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from .executor import ExecRequest, ExecResult, ExecStatus, Executor

SHOWN_OUTPUT_CHARS = 4000


class Verdict:
    accepted = "AC"
    partial = "PA"  # some credit, e.g. an ML score between thresholds
    wrong_answer = "WA"
    time_limit = "TLE"
    memory_limit = "MLE"
    runtime_error = "RE"
    compile_error = "CE"
    internal_error = "IE"  # our side failed (judge down, expected output missing)


_STATUS_TO_VERDICT = {
    ExecStatus.time_limit: Verdict.time_limit,
    ExecStatus.memory_limit: Verdict.memory_limit,
    ExecStatus.runtime_error: Verdict.runtime_error,
    ExecStatus.compile_error: Verdict.compile_error,
    ExecStatus.internal_error: Verdict.internal_error,
}


@dataclass
class TestSpec:
    __test__ = False

    id: str
    ordinal: int
    is_sample: bool
    group: str
    weight: int
    input: str
    expected: str | None


@dataclass
class TestOutcome:
    __test__ = False

    test_id: str
    ordinal: int
    is_sample: bool
    group: str
    weight: int
    verdict: str
    credit: float = 0.0  # 0..1 share of this test's weight earned
    time_ms: int | None = None
    memory_kb: int | None = None
    stdout: str = ""  # truncated; shown to students only for sample tests
    stderr: str = ""
    message: str = ""  # e.g. "accuracy 0.83 (full marks at 0.85)"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Check:
    verdict: str
    credit: float
    message: str = ""


@dataclass
class GradeResult:
    fraction: float  # 0..1
    tests: list[TestOutcome] = field(default_factory=list)
    compile_output: str = ""

    @property
    def internal_error(self) -> bool:
        return any(t.verdict == Verdict.internal_error for t in self.tests)


@dataclass(frozen=True)
class Runtime:
    """A language the sandbox can run (a row of the languages table)."""

    key: str
    judge0_id: int
    file_name: str


@dataclass(frozen=True)
class RunTarget:
    language_key: str
    judge0_id: int
    file_name: str
    source: str
    time_limit_ms: int
    memory_limit_kb: int
    # Plain Python runtime, for graders that wrap the answer in a Python harness (SQL).
    python: Runtime | None = None


def _base_config(config: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    unknown = set(config) - allowed - {"scoring"}
    if unknown:
        raise ValueError(f"unknown grader_config keys: {', '.join(sorted(unknown))}")
    scoring = config.get("scoring", "sum")
    if scoring not in ("sum", "groups_all_or_nothing"):
        raise ValueError("scoring must be 'sum' or 'groups_all_or_nothing'")
    return {"scoring": scoring}


class Grader(ABC):
    key: str = ""
    description: str = ""
    needs_expected: bool = True  # every test must have an expected output before an exam opens
    can_generate_expected: bool = True  # expected outputs can come from the reference solution

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return the normalised config, or raise ValueError with a readable message."""

    def validate_language(self, language_key: str) -> None:  # noqa: B027 - optional hook
        """Raise ValueError if answers in this language cannot be graded this way."""
        return None

    def prepare(self, target: RunTarget, test: TestSpec, config: dict[str, Any]) -> ExecRequest:
        return ExecRequest(
            language_key=target.language_key, judge0_id=target.judge0_id, file_name=target.file_name,
            source=target.source, stdin=test.input, time_limit_ms=target.time_limit_ms,
            memory_limit_kb=target.memory_limit_kb,
        )

    def expected_from_output(self, stdout: str, config: dict[str, Any]) -> str:
        return stdout

    def validate_test_input(self, text: str, config: dict[str, Any]) -> None:  # noqa: B027 - optional hook
        """Raise ValueError if a test input cannot be used with this question type."""
        return None

    def validate_expected(self, text: str, config: dict[str, Any]) -> str:
        """Check (and normalise) an expected output typed by a professor."""
        return text

    @abstractmethod
    def check(self, test: TestSpec, result: ExecResult, config: dict[str, Any]) -> Check:
        """Judge a run that finished normally (status ok)."""

    def shown_stdout(self, result: ExecResult) -> str:
        return result.stdout

    async def grade(
        self, executor: Executor, target: RunTarget, tests: list[TestSpec], config: dict[str, Any]
    ) -> GradeResult:
        config = self.validate_config(config or {})
        if not tests:
            return GradeResult(fraction=0.0)
        try:
            self.validate_language(target.language_key)
            requests = [self.prepare(target, t, config) for t in tests]
        except ValueError as exc:
            return GradeResult(0.0, [_outcome(t, Verdict.internal_error, 0.0, None, str(exc)) for t in tests])
        results = await executor.run_batch(requests)
        outcomes: list[TestOutcome] = []
        compile_output = ""
        for test, res in zip(tests, results, strict=True):
            if res.status is ExecStatus.ok:
                if self.needs_expected and test.expected is None:
                    check = Check(Verdict.internal_error, 0.0, "expected output missing")
                else:
                    check = self.check(test, res, config)
            else:
                check = Check(_STATUS_TO_VERDICT[res.status], 0.0)
                if res.status is ExecStatus.compile_error:
                    compile_output = res.compile_output[:SHOWN_OUTPUT_CHARS]
            outcomes.append(_outcome(test, check.verdict, check.credit, res, check.message,
                                     stdout=self.shown_stdout(res)))
        return GradeResult(fraction=score(outcomes, config["scoring"]), tests=outcomes, compile_output=compile_output)


def _outcome(test: TestSpec, verdict: str, credit: float, res: ExecResult | None, message: str = "",
             stdout: str = "") -> TestOutcome:
    return TestOutcome(
        test_id=test.id, ordinal=test.ordinal, is_sample=test.is_sample, group=test.group, weight=test.weight,
        verdict=verdict, credit=max(0.0, min(1.0, credit)),
        time_ms=res.time_ms if res else None, memory_kb=res.memory_kb if res else None,
        stdout=stdout[:SHOWN_OUTPUT_CHARS],
        stderr=((res.stderr or res.message) if res else "")[:SHOWN_OUTPUT_CHARS],
        message=message[:500],
    )


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
        earned = sum(o.weight * o.credit for o in outcomes)
    return round(earned / total, 6)


_GRADERS: dict[str, Grader] = {}


def register(grader: Grader) -> Grader:
    if not grader.key or grader.key in _GRADERS:
        raise ValueError(f"grader key {grader.key!r} is empty or already registered")
    _GRADERS[grader.key] = grader
    return grader


def get_grader(key: str) -> Grader:
    try:
        return _GRADERS[key]
    except KeyError:
        raise ValueError(f"unknown grader {key!r}; available: {', '.join(sorted(_GRADERS))}") from None


def grader_keys() -> list[str]:
    return sorted(_GRADERS)
