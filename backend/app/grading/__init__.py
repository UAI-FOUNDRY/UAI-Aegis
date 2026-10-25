"""Code execution and grading.

- executor.py: runs code (Judge0 sandbox in production, a local runner for development)
- compare.py: output comparison modes
- registry.py: grader plug-ins; a new question type is a new grader module
- stdio.py: the standard input -> output grader
"""

# Import built-in graders so they register themselves.
from . import stdio  # noqa: E402,F401
from .registry import get_grader, grader_keys

__all__ = ["get_grader", "grader_keys"]
