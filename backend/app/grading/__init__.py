"""Code execution and grading.

- executor.py: runs code (Judge0 sandbox in production, a local runner for development)
- compare.py:  output comparison modes
- registry.py: grader plug-ins; a new question type is a new grader module
- stdio.py:    input -> output programs (any language)
- sql.py:      SQL on SQLite (SELECT result, or statements checked by a query)
- ml.py:       Python solve(train, test) scored by a metric on hidden labels
"""

# Import built-in graders so they register themselves.
from . import ml, sql, stdio  # noqa: F401
from .registry import get_grader, grader_keys

__all__ = ["get_grader", "grader_keys"]
