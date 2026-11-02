"""Question pools and test cases.

Recommended workflow (most reliable, least typing):
1. Create the question with a reference solution.
2. Add test *inputs* only (or paste input/output pairs if you prefer).
3. POST /questions/{id}/generate-outputs: the reference solution produces every expected
   output and the time limit is measured from its slowest run.
4. Optionally POST /questions/{id}/try with a deliberately wrong or slow solution to
   confirm the tests catch it.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import ApiError, bad_request, not_found
from ...grading import get_grader, grader_keys
from ...grading.executor import ExecStatus
from ...grading.ml import split_dataset
from ...grading.registry import GradeResult, TestSpec
from ...models import (
    Difficulty,
    ExamPoolQuestion,
    Language,
    Question,
    SessionQuestion,
    Subject,
    TestCase,
    User,
    utcnow,
)
from ...security.crypto import AAD
from ...services import audit
from ...services.authz import Cap, caps_for, require_cap
from ...services.exams import build_target, grade_code, load_tests
from ...state import AppState
from ..deps import get_db, get_state, require_professor

router = APIRouter(tags=["questions"])

_PREVIEW_CHARS = 300


# ------------------------------------------------------------------------------ schemas


class QuestionIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    statement_md: str = Field(min_length=1, max_length=100_000)
    difficulty: Difficulty
    tags: list[str] = Field(default_factory=list, max_length=20)
    grader: str = "stdio"
    grader_config: dict[str, Any] = Field(default_factory=dict)
    marks: int = Field(10, ge=1, le=1000)
    time_limit_ms: int | None = Field(None, ge=100, le=15000)
    memory_limit_kb: int | None = Field(None, ge=16384, le=1048576)
    starter_code: dict[str, str] = Field(default_factory=dict)
    allowed_languages: list[str] | None = Field(None, max_length=50)
    reference_language: str | None = None
    reference_code: str | None = None


class QuestionPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    statement_md: str | None = Field(None, min_length=1, max_length=100_000)
    difficulty: Difficulty | None = None
    tags: list[str] | None = Field(None, max_length=20)
    grader: str | None = None
    grader_config: dict[str, Any] | None = None
    marks: int | None = Field(None, ge=1, le=1000)
    time_limit_ms: int | None = Field(None, ge=100, le=15000)
    memory_limit_kb: int | None = Field(None, ge=16384, le=1048576)
    starter_code: dict[str, str] | None = None
    allowed_languages: list[str] | None = Field(None, max_length=50)
    reference_language: str | None = None
    reference_code: str | None = None
    archived: bool | None = None


class QuestionSummary(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    title: str
    difficulty: Difficulty
    tags: list[str]
    marks: int
    grader: str
    test_count: int
    sample_count: int
    missing_expected: int
    archived: bool
    updated_at: datetime


class QuestionDetail(QuestionSummary):
    statement_md: str
    grader_config: dict[str, Any]
    time_limit_ms: int | None
    auto_time_limit_ms: int | None
    memory_limit_kb: int | None
    starter_code: dict[str, str]
    allowed_languages: list[str] | None
    reference_language: str | None
    reference_code: str | None  # only for professors who can edit
    version: int


class TestIn(BaseModel):
    __test__ = False

    input: str
    expected_output: str | None = None
    is_sample: bool = False
    group: str = Field("main", min_length=1, max_length=64)
    weight: int = Field(1, ge=0, le=1000)


class TestsIn(BaseModel):
    __test__ = False

    tests: list[TestIn] = Field(min_length=1, max_length=200)


class TestPatch(BaseModel):
    __test__ = False

    input: str | None = None
    expected_output: str | None = None
    is_sample: bool | None = None
    group: str | None = Field(None, min_length=1, max_length=64)
    weight: int | None = Field(None, ge=0, le=1000)


class TestOut(BaseModel):
    __test__ = False

    id: uuid.UUID
    ordinal: int
    is_sample: bool
    group: str
    weight: int
    has_expected: bool
    input_size: int
    expected_size: int
    input_preview: str
    expected_preview: str | None


class TestFull(TestOut):
    input: str
    expected_output: str | None


class TryIn(BaseModel):
    language: str
    code: str


class TestResultOut(BaseModel):
    test_id: str
    ordinal: int
    is_sample: bool
    group: str
    weight: int
    verdict: str
    credit: float
    message: str
    time_ms: int | None
    memory_kb: int | None
    stdout: str
    stderr: str


class GradeOut(BaseModel):
    fraction: float
    passed: int
    total: int
    compile_output: str
    tests: list[TestResultOut]


class GenerateReport(BaseModel):
    ok: bool
    generated: int
    auto_time_limit_ms: int | None
    slowest_ms: int | None
    failures: list[dict[str, Any]]


class GraderInfo(BaseModel):
    key: str
    description: str


# ------------------------------------------------------------------------------ helpers


def grade_out(result: GradeResult) -> GradeOut:
    tests = [TestResultOut(**t.to_dict()) for t in result.tests]
    return GradeOut(fraction=result.fraction, passed=sum(t.verdict == "AC" for t in tests), total=len(tests),
                    compile_output=result.compile_output, tests=tests)


async def _question_for(db: AsyncSession, user: User, question_id: uuid.UUID, cap: Cap) -> Question:
    question = await db.get(Question, question_id)
    if question is None:
        raise not_found()
    await require_cap(db, user, question.subject_id, cap)
    return question


async def _language(db: AsyncSession, key: str) -> Language:
    lang = await db.scalar(select(Language).where(Language.key == key, Language.is_enabled))
    if lang is None:
        raise bad_request("unknown_language", f"Language '{key}' is not available.")
    return lang


def _check_size(state: AppState, text: str | None, limit: int, what: str) -> None:
    if text is not None and len(text.encode("utf-8")) > limit:
        raise bad_request("too_large", f"{what} is larger than {limit // 1024} KB.")


async def _validate(db: AsyncSession, state: AppState, data: dict[str, Any], current: Question | None) -> dict:
    """Check grader, languages and sizes; returns the cleaned field values."""
    grader_key = data.get("grader", current.grader if current else "stdio")
    try:
        grader = get_grader(grader_key)
        if "grader_config" in data or "grader" in data:
            data["grader_config"] = grader.validate_config(
                data.get("grader_config", current.grader_config if current else {}) or {}
            )
    except ValueError as exc:
        raise bad_request("bad_grader_config", str(exc)) from None
    keys = set(data.get("starter_code") or {}) | set(data.get("allowed_languages") or [])
    if data.get("reference_language"):
        keys.add(data["reference_language"])
    if keys:
        known = set((await db.scalars(select(Language.key).where(Language.key.in_(keys)))).all())
        if keys - known:
            raise bad_request("unknown_language", f"Unknown language(s): {', '.join(sorted(keys - known))}")
    for lang, code in (data.get("starter_code") or {}).items():
        _check_size(state, code, state.settings.max_code_bytes, f"Starter code for {lang}")
    _check_size(state, data.get("reference_code"), state.settings.max_code_bytes, "Reference solution")
    if "tags" in data and data["tags"] is not None:
        data["tags"] = sorted({t.strip().lower() for t in data["tags"] if t.strip()})
        if any(len(t) > 40 for t in data["tags"]):
            raise bad_request("bad_tag", "Tags must be 40 characters or fewer.")
    return data


async def _summary(db: AsyncSession, q: Question) -> dict[str, Any]:
    row = (await db.execute(
        select(
            func.count(TestCase.id),
            func.count(TestCase.id).filter(TestCase.is_sample),
            func.count(TestCase.id).filter(TestCase.expected_enc.is_(None)),
        ).where(TestCase.question_id == q.id)
    )).one()
    return dict(id=q.id, subject_id=q.subject_id, title=q.title, difficulty=q.difficulty, tags=q.tags or [],
                marks=q.marks, grader=q.grader, test_count=row[0], sample_count=row[1], missing_expected=row[2],
                archived=q.archived, updated_at=q.updated_at)


async def _detail(db: AsyncSession, state: AppState, user: User, q: Question) -> QuestionDetail:
    can_edit = Cap.edit in await caps_for(db, user, q.subject_id)
    reference = None
    if can_edit and q.reference_enc:
        reference = state.cipher.decrypt_str(q.reference_enc, AAD.reference(q.id))
    return QuestionDetail(
        **await _summary(db, q), statement_md=q.statement_md, grader_config=q.grader_config or {},
        time_limit_ms=q.time_limit_ms, auto_time_limit_ms=q.auto_time_limit_ms, memory_limit_kb=q.memory_limit_kb,
        starter_code=q.starter_code or {}, allowed_languages=q.allowed_languages,
        reference_language=q.reference_language, reference_code=reference, version=q.version,
    )


def _test_out(state: AppState, t: TestCase, full: bool = False) -> TestOut | TestFull:
    text_in = state.cipher.decrypt_str(t.input_enc, AAD.test_input(t.id))
    text_out = state.cipher.decrypt_str(t.expected_enc, AAD.test_expected(t.id)) if t.expected_enc else None
    base = dict(
        id=t.id, ordinal=t.ordinal, is_sample=t.is_sample, group=t.group_name, weight=t.weight,
        has_expected=text_out is not None, input_size=len(text_in), expected_size=len(text_out or ""),
        input_preview=text_in[:_PREVIEW_CHARS],
        expected_preview=text_out[:_PREVIEW_CHARS] if text_out is not None else None,
    )
    return TestFull(**base, input=text_in, expected_output=text_out) if full else TestOut(**base)


def _check_test(question: Question, text_in: str, expected: str | None, label: str) -> str | None:
    """Let the question type check a test as it is saved (e.g. function questions take a
    JSON list of arguments). Returns the expected output, possibly normalised."""
    grader = get_grader(question.grader)
    try:
        config = grader.validate_config(question.grader_config or {})
        grader.validate_test_input(text_in, config)
        return grader.validate_expected(expected, config) if expected is not None else None
    except ValueError as exc:
        raise bad_request("bad_test", f"{label}: {exc}") from None


async def _record(db, state: AppState, user: User, action: str, question: Question, details=None):
    await audit.record(db, state.settings.audit_key_bytes, actor=user, action=action, target_type="question",
                       target_id=question.id, details={"subject_id": str(question.subject_id), **(details or {})})


def _touch(q: Question) -> None:
    q.version += 1
    q.updated_at = utcnow()


# ---------------------------------------------------------------------------- questions


@router.get("/graders", response_model=list[GraderInfo])
async def list_graders(_: User = Depends(require_professor)):
    return [GraderInfo(key=k, description=get_grader(k).description) for k in grader_keys()]


@router.get("/subjects/{subject_id}/questions", response_model=list[QuestionSummary])
async def list_questions(subject_id: uuid.UUID, include_archived: bool = False,
                         db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    await require_cap(db, user, subject_id, Cap.view)
    query = select(Question).where(Question.subject_id == subject_id).order_by(Question.difficulty, Question.title)
    if not include_archived:
        query = query.where(Question.archived.is_(False))
    return [QuestionSummary(**await _summary(db, q)) for q in (await db.scalars(query)).all()]


@router.post("/subjects/{subject_id}/questions", response_model=QuestionDetail, status_code=201)
async def create_question(subject_id: uuid.UUID, body: QuestionIn, state: AppState = Depends(get_state),
                          db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    await require_cap(db, user, subject_id, Cap.edit)
    if await db.get(Subject, subject_id) is None:
        raise not_found()
    data = await _validate(db, state, body.model_dump(), None)
    reference_code = data.pop("reference_code")
    question = Question(id=uuid.uuid4(), subject_id=subject_id, created_by=user.id, **data)
    if reference_code:
        if not question.reference_language:
            raise bad_request("reference_language_required", "Say which language the reference solution is in.")
        question.reference_enc = state.cipher.encrypt(reference_code, AAD.reference(question.id))
    db.add(question)
    await _record(db, state, user, "question.created", question, {"title": question.title})
    await db.commit()
    return await _detail(db, state, user, question)


@router.get("/questions/{question_id}", response_model=QuestionDetail)
async def get_question(question_id: uuid.UUID, state: AppState = Depends(get_state),
                       db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    question = await _question_for(db, user, question_id, Cap.view)
    return await _detail(db, state, user, question)


@router.patch("/questions/{question_id}", response_model=QuestionDetail)
async def update_question(question_id: uuid.UUID, body: QuestionPatch, state: AppState = Depends(get_state),
                          db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    question = await _question_for(db, user, question_id, Cap.edit)
    data = await _validate(db, state, body.model_dump(exclude_unset=True), question)
    if "reference_code" in data:
        code = data.pop("reference_code")
        question.reference_enc = state.cipher.encrypt(code, AAD.reference(question.id)) if code else None
    for k, v in data.items():
        setattr(question, k, v)
    if question.reference_enc and not question.reference_language:
        raise bad_request("reference_language_required", "Say which language the reference solution is in.")
    _touch(question)
    await _record(db, state, user, "question.updated", question, {"fields": sorted(body.model_fields_set)})
    await db.commit()
    return await _detail(db, state, user, question)


@router.delete("/questions/{question_id}")
async def delete_question(question_id: uuid.UUID, state: AppState = Depends(get_state),
                          db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)) -> dict:
    """Deletes an unused question. A question already used in an exam is archived
    instead, so past answers and marks stay intact."""
    question = await _question_for(db, user, question_id, Cap.edit)
    used = await db.scalar(select(func.count()).select_from(ExamPoolQuestion).where(
        ExamPoolQuestion.question_id == question.id)) or await db.scalar(
        select(func.count()).select_from(SessionQuestion).where(SessionQuestion.question_id == question.id))
    if used:
        question.archived = True
        _touch(question)
        await _record(db, state, user, "question.archived", question)
        await db.commit()
        return {"result": "archived"}
    await _record(db, state, user, "question.deleted", question, {"title": question.title})
    await db.delete(question)
    await db.commit()
    return {"result": "deleted"}


# -------------------------------------------------------------------------------- tests


@router.get("/questions/{question_id}/tests", response_model=list[TestOut])
async def list_tests(question_id: uuid.UUID, state: AppState = Depends(get_state),
                     db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    question = await _question_for(db, user, question_id, Cap.view)
    tests = (await db.scalars(select(TestCase).where(TestCase.question_id == question.id)
                              .order_by(TestCase.ordinal))).all()
    return [_test_out(state, t) for t in tests]


@router.get("/questions/{question_id}/tests/{test_id}", response_model=TestFull)
async def get_test(question_id: uuid.UUID, test_id: uuid.UUID, state: AppState = Depends(get_state),
                   db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    question = await _question_for(db, user, question_id, Cap.view)
    test = await db.get(TestCase, test_id)
    if test is None or test.question_id != question.id:
        raise not_found()
    return _test_out(state, test, full=True)


@router.post("/questions/{question_id}/tests", response_model=list[TestOut], status_code=201)
async def add_tests(question_id: uuid.UUID, body: TestsIn, state: AppState = Depends(get_state),
                    db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    question = await _question_for(db, user, question_id, Cap.edit)
    limit = state.settings.max_test_bytes
    for t in body.tests:
        _check_size(state, t.input, limit, "A test input")
        _check_size(state, t.expected_output, limit, "An expected output")
    for n, t in enumerate(body.tests, start=1):
        t.expected_output = _check_test(question, t.input, t.expected_output, f"Test {n} in this upload")
    start = (await db.scalar(select(func.max(TestCase.ordinal)).where(TestCase.question_id == question.id))) or 0
    created = []
    for i, t in enumerate(body.tests, start=1):
        test = TestCase(id=uuid.uuid4(), question_id=question.id, ordinal=start + i, is_sample=t.is_sample,
                        group_name=t.group, weight=t.weight)
        test.input_enc = state.cipher.encrypt(t.input, AAD.test_input(test.id))
        if t.expected_output is not None:
            test.expected_enc = state.cipher.encrypt(t.expected_output, AAD.test_expected(test.id))
        db.add(test)
        created.append(test)
    _touch(question)
    await _record(db, state, user, "question.tests_added", question, {"count": len(created)})
    await db.commit()
    return [_test_out(state, t) for t in created]


@router.patch("/questions/{question_id}/tests/{test_id}", response_model=TestOut)
async def update_test(question_id: uuid.UUID, test_id: uuid.UUID, body: TestPatch,
                      state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                      user: User = Depends(require_professor)):
    question = await _question_for(db, user, question_id, Cap.edit)
    test = await db.get(TestCase, test_id)
    if test is None or test.question_id != question.id:
        raise not_found()
    changes = body.model_dump(exclude_unset=True)
    if "input" in changes or changes.get("expected_output") is not None:
        current_input = changes.get("input")
        if current_input is None:
            current_input = state.cipher.decrypt_str(test.input_enc, AAD.test_input(test.id))
        checked = _check_test(question, current_input, changes.get("expected_output"), f"Test {test.ordinal}")
        if changes.get("expected_output") is not None:
            changes["expected_output"] = checked
    if "input" in changes:
        _check_size(state, changes["input"], state.settings.max_test_bytes, "A test input")
        test.input_enc = state.cipher.encrypt(changes["input"], AAD.test_input(test.id))
    if "expected_output" in changes:
        value = changes["expected_output"]
        _check_size(state, value, state.settings.max_test_bytes, "An expected output")
        test.expected_enc = state.cipher.encrypt(value, AAD.test_expected(test.id)) if value is not None else None
    for field, attr in (("is_sample", "is_sample"), ("group", "group_name"), ("weight", "weight")):
        if field in changes:
            setattr(test, attr, changes[field])
    _touch(question)
    await _record(db, state, user, "question.test_updated", question, {"test_id": str(test.id)})
    await db.commit()
    return _test_out(state, test)


@router.delete("/questions/{question_id}/tests/{test_id}", status_code=204, response_model=None)
async def delete_test(question_id: uuid.UUID, test_id: uuid.UUID, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)) -> None:
    question = await _question_for(db, user, question_id, Cap.edit)
    test = await db.get(TestCase, test_id)
    if test is None or test.question_id != question.id:
        raise not_found()
    await db.delete(test)
    _touch(question)
    await _record(db, state, user, "question.test_deleted", question, {"test_id": str(test_id)})
    await db.commit()


# ------------------------------------------------------------- reference solution tools


@router.post("/questions/{question_id}/generate-outputs", response_model=GenerateReport)
async def generate_outputs(question_id: uuid.UUID, only_missing: bool = Query(False),
                           state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                           user: User = Depends(require_professor)):
    """Run the reference solution on every test input and save its output as the expected
    output. Also measures the slowest run and sets the automatic time limit. Nothing is
    saved if the reference solution fails on any test."""
    question = await _question_for(db, user, question_id, Cap.edit)
    grader = get_grader(question.grader)
    if not grader.can_generate_expected:
        raise bad_request("not_applicable",
                          "This question type has no expected outputs to generate (ML uses uploaded data; "
                          "HTML tests are checks). Use Validate instead.")
    if not question.reference_enc or not question.reference_language:
        raise bad_request("no_reference", "Add a reference solution first.")
    lang = await _language(db, question.reference_language)
    code = state.cipher.decrypt_str(question.reference_enc, AAD.reference(question.id))
    tests = (await db.scalars(select(TestCase).where(TestCase.question_id == question.id)
                              .order_by(TestCase.ordinal))).all()
    if not tests:
        raise bad_request("no_tests", "Add test inputs first.")
    targets = [t for t in tests if not (only_missing and t.expected_enc)]
    config = grader.validate_config(question.grader_config or {})
    run_target = await build_target(state, question, lang, code, time_limit_ms=state.settings.max_time_limit_ms)
    try:
        grader.validate_language(lang.key)
        requests = [grader.prepare(run_target, TestSpec(
            id=str(t.id), ordinal=t.ordinal, is_sample=t.is_sample, group=t.group_name, weight=t.weight,
            input=state.cipher.decrypt_str(t.input_enc, AAD.test_input(t.id)), expected=None,
        ), config) for t in targets]
    except ValueError as exc:
        raise bad_request("bad_reference", str(exc)) from None
    results = await state.executor.run_batch(requests) if requests else []
    failures: list[dict[str, Any]] = []
    expected: list[str] = []
    for t, r in zip(targets, results, strict=True):
        if r.status is not ExecStatus.ok:
            failures.append({"ordinal": t.ordinal, "status": r.status.value,
                             "message": (r.compile_output or r.stderr or r.message)[:2000]})
            continue
        try:
            expected.append(grader.expected_from_output(r.stdout, config))
        except ValueError as exc:
            failures.append({"ordinal": t.ordinal, "status": "bad_output", "message": str(exc)})
    if failures:
        return GenerateReport(ok=False, generated=0, auto_time_limit_ms=question.auto_time_limit_ms,
                              slowest_ms=None, failures=failures)
    for t, value in zip(targets, expected, strict=True):
        t.expected_enc = state.cipher.encrypt(value, AAD.test_expected(t.id))
    slowest = max((r.time_ms or 0 for r in results), default=0)
    if results:
        settings = state.settings
        base = math.ceil(slowest * settings.time_limit_factor / lang.time_multiplier)
        question.auto_time_limit_ms = max(settings.min_time_limit_ms, min(base, settings.max_time_limit_ms))
    _touch(question)
    await _record(db, state, user, "question.outputs_generated", question, {"tests": len(targets)})
    await db.commit()
    return GenerateReport(ok=True, generated=len(targets), auto_time_limit_ms=question.auto_time_limit_ms,
                          slowest_ms=slowest, failures=[])


@router.post("/questions/{question_id}/datasets", response_model=TestOut, status_code=201)
async def add_dataset(
    question_id: uuid.UUID,
    train: UploadFile = File(..., description="Training data CSV, with the target column"),
    test: UploadFile = File(..., description="Evaluation data CSV, with the target column"),
    is_sample: bool = Form(False, description="Sample datasets are visible to students"),
    weight: int = Form(1, ge=0, le=1000),
    group: str = Form("main", min_length=1, max_length=64),
    state: AppState = Depends(get_state),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_professor),
):
    """ML questions: add one train/test split as a test case. The test labels are kept
    on the server and never sent to students or to the sandbox."""
    question = await _question_for(db, user, question_id, Cap.edit)
    if question.grader != "ml":
        raise bad_request("not_ml", "Datasets are for questions using the 'ml' grader.")
    config = get_grader("ml").validate_config(question.grader_config or {})
    limit = state.settings.max_test_bytes
    raw_train, raw_test = await train.read(limit + 1), await test.read(limit + 1)
    if len(raw_train) + len(raw_test) > limit:
        raise bad_request("too_large", f"Train + test must be under {limit // 1024} KB together.")
    try:
        stdin, labels = split_dataset(raw_train.decode("utf-8-sig"), raw_test.decode("utf-8-sig"), config["target"])
    except UnicodeDecodeError:
        raise bad_request("bad_encoding", "Save the CSV files as UTF-8.") from None
    except ValueError as exc:
        raise bad_request("bad_dataset", str(exc)) from None
    ordinal = ((await db.scalar(select(func.max(TestCase.ordinal)).where(TestCase.question_id == question.id))) or 0) + 1
    case = TestCase(id=uuid.uuid4(), question_id=question.id, ordinal=ordinal, is_sample=is_sample,
                    group_name=group, weight=weight)
    case.input_enc = state.cipher.encrypt(stdin, AAD.test_input(case.id))
    case.expected_enc = state.cipher.encrypt(labels, AAD.test_expected(case.id))
    db.add(case)
    _touch(question)
    await _record(db, state, user, "question.dataset_added", question, {"test_id": str(case.id)})
    await db.commit()
    return _test_out(state, case)


async def _grade_against_all(db: AsyncSession, state: AppState, question: Question, language_key: str,
                             code: str) -> GradeOut:
    _check_size(state, code, state.settings.max_code_bytes, "Code")
    lang = await _language(db, language_key)
    tests = await load_tests(db, state.cipher, question.id)
    if not tests:
        raise bad_request("no_tests", "This question has no tests yet.")
    if get_grader(question.grader).needs_expected and any(t.expected is None for t in tests):
        raise ApiError(409, "missing_expected", "Some tests have no expected output. Generate outputs first.")
    return grade_out(await grade_code(state, question, lang, code, tests))


@router.post("/questions/{question_id}/validate", response_model=GradeOut)
async def validate_question(question_id: uuid.UUID, state: AppState = Depends(get_state),
                            db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    """The reference solution must pass every test. Exams refuse to open otherwise."""
    question = await _question_for(db, user, question_id, Cap.edit)
    if not question.reference_enc or not question.reference_language:
        raise bad_request("no_reference", "Add a reference solution first.")
    code = state.cipher.decrypt_str(question.reference_enc, AAD.reference(question.id))
    return await _grade_against_all(db, state, question, question.reference_language, code)


@router.post("/questions/{question_id}/try", response_model=GradeOut)
async def try_solution(question_id: uuid.UUID, body: TryIn, state: AppState = Depends(get_state),
                       db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    """Run any code against all tests, e.g. a deliberately wrong or slow solution, to
    check the tests are strong enough to catch it."""
    question = await _question_for(db, user, question_id, Cap.edit)
    return await _grade_against_all(db, state, question, body.language, body.code)
