"""How a program's output is compared with the expected output."""

from __future__ import annotations

import math
import re
from collections import Counter
from enum import StrEnum


class CompareMode(StrEnum):
    exact = "exact"  # byte-for-byte
    lines = "lines"  # ignore trailing spaces on each line and trailing blank lines (default)
    whitespace = "whitespace"  # any run of whitespace counts as one separator
    case_insensitive = "case_insensitive"  # like "lines", ignoring upper/lower case
    float = "float"  # numbers equal within a tolerance, other tokens exact
    unordered_lines = "unordered_lines"  # same lines in any order


_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _lines(text: str) -> list[str]:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _floats_equal(a: str, b: str, tolerance: float) -> bool:
    if _NUMBER.match(a) and _NUMBER.match(b):
        x, y = float(a), float(b)
        return math.isclose(x, y, rel_tol=tolerance, abs_tol=tolerance)
    return a == b


def outputs_match(actual: str, expected: str, mode: CompareMode | str = CompareMode.lines,
                  *, tolerance: float = 1e-6) -> bool:
    mode = CompareMode(mode)
    if mode is CompareMode.exact:
        return actual == expected
    if mode is CompareMode.lines:
        return _lines(actual) == _lines(expected)
    if mode is CompareMode.whitespace:
        return actual.split() == expected.split()
    if mode is CompareMode.case_insensitive:
        return [s.casefold() for s in _lines(actual)] == [s.casefold() for s in _lines(expected)]
    if mode is CompareMode.unordered_lines:
        return Counter(_lines(actual)) == Counter(_lines(expected))
    if mode is CompareMode.float:
        got, want = actual.split(), expected.split()
        return len(got) == len(want) and all(_floats_equal(a, b, tolerance) for a, b in zip(got, want, strict=False))
    raise ValueError(f"unknown compare mode {mode}")  # pragma: no cover
