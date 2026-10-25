"""Grader plug-in registry.

A grader turns (student code + test cases) into per-test verdicts and a score from 0 to 1.
Each question names its grader by key. To add a new question type (function-based,
SQL, ML metric, ...), write a module with a Grader subclass, call register() on it,
and import it in grading/__init__.py. Nothing else in the system changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from .executor import Executor


class Verdict:
    accepted = "AC"
    wrong_answer = "WA"
    time_limit = "TLE"
    memory_limit = "MLE"
    runtime_error = "RE"
    compile_error = "CE"
    internal_error = "IE"  # our side failed (judge down, expected output missing)


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
    time_ms: int | None = None
    memory_kb: int | None = None
    stdout: str = ""  # truncated; shown to students only for sample tests
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GradeResult:
    fraction: float  # 0..1
    tests: list[TestOutcome] = field(default_factory=list)
    compile_output: str = ""

    @property
    def internal_error(self) -> bool:
        return any(t.verdict == Verdict.internal_error for t in self.tests)


@dataclass(frozen=True)
class RunTarget:
    language_key: str
    judge0_id: int
    file_name: str
    source: str
    time_limit_ms: int
    memory_limit_kb: int


class Grader(ABC):
    key: str = ""
    description: str = ""

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return the normalised config, or raise ValueError with a readable message."""

    @abstractmethod
    async def grade(
        self, executor: Executor, target: RunTarget, tests: list[TestSpec], config: dict[str, Any]
    ) -> GradeResult: ...


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
