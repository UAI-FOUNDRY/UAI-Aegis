"""Code execution and grading.

- executor.py: runs code (Judge0 sandbox in production, a local runner for development)
- compare.py:  output comparison modes
- registry.py: grader plug-ins; a new question type is a new grader module
- stdio.py:    input -> output programs (any language)
- sql.py:      SQL on SQLite (SELECT result, or statements checked by a query)
- ml.py:       Python solve(train, test) scored by a metric on hidden labels
- function.py: LeetCode-style: the student writes a function, its return value is compared
- html.py:     HTML pages checked against element/attribute/text checks (parsed, never rendered)
- plot.py:     matplotlib/seaborn charts, drawn off-screen and checked (chart kind, titles, labels...)
- checklist.py: shared verdicts for graders whose tests are lists of checks
"""

# Import built-in graders so they register themselves.
from . import function, html, ml, plot, sql, stdio  # noqa: F401
from .registry import get_grader, grader_keys

__all__ = ["get_grader", "grader_keys"]
