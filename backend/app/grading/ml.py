"""Machine-learning questions, scored by a metric on held-out data.

The student writes a Python function:

    def solve(train_csv: str, test_csv: str) -> list:
        # train on train_csv (has the target column), predict for every row of test_csv
        return predictions

The sandbox only ever receives the training data and the test *features*. The test
labels stay on the server, where the predictions are scored, so students cannot read
the answers even with a modified program.

grader_config:
  task        "classification" or "regression"
  target      name of the label column in the uploaded CSVs
  metric      classification: accuracy, f1_macro, precision_macro, recall_macro
              regression: r2 (higher is better), rmse, mae (lower is better)
  thresholds  [{"at": 0.85, "credit": 1.0}, {"at": 0.75, "credit": 0.5}]
              the best threshold reached gives that share of the test's marks.
              For rmse/mae, "at" is the largest error allowed.

Tests are added with POST /questions/{id}/datasets (train.csv + test.csv). Libraries
available to students (numpy, pandas, scikit-learn...) depend on the Python runtime the
'python3-ml' language points to; scoring itself needs none of them.
"""

from __future__ import annotations

import csv
import io
import json
import math
from typing import Any

from .executor import ExecRequest, ExecResult
from .registry import Check, Grader, RunTarget, TestSpec, Verdict, _base_config, register

RESULT_MARK = "AEGIS_RESULT:"
CLASSIFICATION = {"accuracy", "f1_macro", "precision_macro", "recall_macro"}
REGRESSION_HIGHER = {"r2"}
REGRESSION_LOWER = {"rmse", "mae"}

_HARNESS = '''import contextlib, json, os, sys
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):  # sandbox thread limit
    os.environ.setdefault(_v, "1")
_payload = json.loads(sys.stdin.read())
_source = {source!r}
_ns = {{"__name__": "solution"}}
with contextlib.redirect_stdout(sys.stderr):  # the student's prints go to the log, not the result
    exec(compile(_source, "solution.py", "exec"), _ns)
    _solve = _ns.get("solve")
    if not callable(_solve):
        print("Define a function solve(train_csv, test_csv) that returns one prediction per test row.")
        raise SystemExit(1)
    _preds = _solve(_payload["train"], _payload["test"])
_preds = [p.item() if hasattr(p, "item") else p for p in list(_preds)]
print({mark!r} + json.dumps(_preds, default=str))
'''


def split_dataset(train_text: str, test_text: str, target: str) -> tuple[str, str]:
    """From uploaded train.csv and test.csv (both with the target column) build the
    sandbox input (train + test features) and the hidden labels (expected output)."""
    train = list(csv.reader(io.StringIO(train_text)))
    test = list(csv.reader(io.StringIO(test_text)))
    if len(train) < 2 or len(test) < 2:
        raise ValueError("each CSV needs a header row and at least one data row")
    if target not in train[0]:
        raise ValueError(f"train.csv has no '{target}' column")
    if target not in test[0]:
        raise ValueError(f"test.csv has no '{target}' column")
    col = test[0].index(target)
    labels = [row[col] for row in test[1:] if row]
    features = io.StringIO()
    writer = csv.writer(features, lineterminator="\n")
    for row in test:
        if row:
            writer.writerow(row[:col] + row[col + 1:])
    return json.dumps({"train": train_text, "test": features.getvalue()}), json.dumps(labels)


def _label(value: Any) -> str:
    if isinstance(value, bool):
        return str(int(value))
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return text
    if math.isfinite(number) and number.is_integer():
        return str(int(number))
    return repr(round(number, 9))


def _number(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return number


def _f1_parts(y_true: list[str], y_pred: list[str]) -> list[tuple[float, float, float]]:
    parts = []
    for cls in sorted(set(y_true) | set(y_pred)):
        tp = sum(t == cls and p == cls for t, p in zip(y_true, y_pred, strict=True))
        fp = sum(t != cls and p == cls for t, p in zip(y_true, y_pred, strict=True))
        fn = sum(t == cls and p != cls for t, p in zip(y_true, y_pred, strict=True))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        parts.append((precision, recall, f1))
    return parts


def compute_metric(metric: str, y_true: list[Any], y_pred: list[Any]) -> float:
    if metric in CLASSIFICATION:
        t, p = [_label(v) for v in y_true], [_label(v) for v in y_pred]
        if metric == "accuracy":
            return sum(a == b for a, b in zip(t, p, strict=True)) / len(t)
        parts = _f1_parts(t, p)
        index = {"precision_macro": 0, "recall_macro": 1, "f1_macro": 2}[metric]
        return sum(part[index] for part in parts) / len(parts)
    t, p = [_number(v) for v in y_true], [_number(v) for v in y_pred]
    errors = [a - b for a, b in zip(t, p, strict=True)]
    if metric == "mae":
        return sum(abs(e) for e in errors) / len(errors)
    if metric == "rmse":
        return math.sqrt(sum(e * e for e in errors) / len(errors))
    mean = sum(t) / len(t)
    ss_tot = sum((v - mean) ** 2 for v in t)
    ss_res = sum(e * e for e in errors)
    return 1.0 - ss_res / ss_tot if ss_tot else (1.0 if ss_res == 0 else 0.0)


class MlGrader(Grader):
    key = "ml"
    description = "Python solve(train_csv, test_csv) -> predictions, scored by a metric on hidden labels."
    can_generate_expected = False  # labels come from the uploaded test set

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        out = _base_config(config, {"task", "target", "metric", "thresholds"})
        task = config.get("task", "classification")
        if task not in ("classification", "regression"):
            raise ValueError("task must be 'classification' or 'regression'")
        target = config.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target (the label column name) is required")
        metric = config.get("metric", "accuracy" if task == "classification" else "r2")
        allowed = CLASSIFICATION if task == "classification" else REGRESSION_HIGHER | REGRESSION_LOWER
        if metric not in allowed:
            raise ValueError(f"metric for {task} must be one of: {', '.join(sorted(allowed))}")
        raw = config.get("thresholds")
        if not isinstance(raw, list) or not raw:
            raise ValueError('thresholds are required, e.g. [{"at": 0.85, "credit": 1.0}]')
        thresholds = []
        for item in raw:
            try:
                at, credit = float(item["at"]), float(item.get("credit", 1.0))
            except (KeyError, TypeError, ValueError):
                raise ValueError('each threshold needs a number "at" and an optional "credit" (0-1)') from None
            if not 0 < credit <= 1:
                raise ValueError("credit must be between 0 and 1")
            thresholds.append({"at": at, "credit": credit})
        thresholds.sort(key=lambda th: -th["credit"])
        return {**out, "task": task, "target": target.strip(), "metric": metric, "thresholds": thresholds}

    def validate_language(self, language_key: str) -> None:
        if not language_key.startswith("python"):
            raise ValueError("ML questions must be answered in Python")

    def prepare(self, target: RunTarget, test: TestSpec, config: dict[str, Any]) -> ExecRequest:
        return ExecRequest(
            language_key=target.language_key, judge0_id=target.judge0_id, file_name=target.file_name,
            source=_HARNESS.format(source=target.source, mark=RESULT_MARK), stdin=test.input,
            time_limit_ms=target.time_limit_ms, memory_limit_kb=target.memory_limit_kb,
        )

    def check(self, test: TestSpec, result: ExecResult, config: dict[str, Any]) -> Check:
        lines = [line for line in result.stdout.splitlines() if line.startswith(RESULT_MARK)]
        try:
            predictions = json.loads(lines[-1][len(RESULT_MARK):])
            labels = json.loads(test.expected or "[]")
        except (IndexError, json.JSONDecodeError):
            return Check(Verdict.wrong_answer, 0.0, "solve() did not return a list of predictions")
        if not isinstance(predictions, list) or len(predictions) != len(labels):
            got = len(predictions) if isinstance(predictions, list) else 0
            return Check(Verdict.wrong_answer, 0.0, f"expected {len(labels)} predictions, got {got}")
        metric = config["metric"]
        try:
            value = compute_metric(metric, labels, predictions)
        except (ValueError, TypeError, ZeroDivisionError):
            return Check(Verdict.wrong_answer, 0.0, "predictions must be numbers for a regression question")
        lower_is_better = metric in REGRESSION_LOWER
        credit = 0.0
        for th in config["thresholds"]:  # best credit first
            if (value <= th["at"]) if lower_is_better else (value >= th["at"]):
                credit = th["credit"]
                break
        best = config["thresholds"][0]["at"]
        message = f"{metric} {value:.4f} (full marks at {'<=' if lower_is_better else '>='} {best:g})"
        verdict = Verdict.accepted if credit >= 1 else Verdict.partial if credit > 0 else Verdict.wrong_answer
        return Check(verdict, credit, message)


register(MlGrader())
