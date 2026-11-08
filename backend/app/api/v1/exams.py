"""Professor side of exams: setup, opening, live monitor, lock/resume/end, marks, results."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import ApiError, bad_request, conflict, not_found
from ...grading import get_grader
from ...models import (
    AccessGrant,
    Answer,
    Batch,
    BatchMember,
    Exam,
    ExamPoolQuestion,
    ExamSession,
    ExamStatus,
    Language,
    ProctorEvent,
    Question,
    Run,
    SessionQuestion,
    SessionStatus,
    Severity,
    Subject,
    SubjectBatch,
    SubjectLanguage,
    TestCase,
    User,
    utcnow,
)
from ...security.crypto import AAD
from ...security.tokens import new_access_code
from ...services import audit
from ...services.authz import Cap, caps_for, require_cap
from ...services.exams import (
    Marking,
    Selection,
    check_selection,
    finalize_session,
    grade_code,
    grade_session,
    languages_for_question,
    load_tests,
)
from ...services.live import RESYNC, notify
from ...services.proctoring import ExamPolicy, effective_policy, lock
from ...state import AppState
from ..deps import get_db, get_state, require_professor
from ..schemas import UserBrief

router = APIRouter(tags=["exams"])

FINISHED = (SessionStatus.submitted, SessionStatus.terminated)


# ------------------------------------------------------------------------------ schemas


class ExamIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    instructions_md: str = Field("", max_length=20_000)
    duration_minutes: int = Field(ge=5, le=600)
    join_window_minutes: int = Field(15, ge=0, le=600, description="0 = students may start any time while open.")
    selection: Selection = Field(default_factory=Selection)
    policy: ExamPolicy = Field(default_factory=ExamPolicy)
    marking: Marking = Field(default_factory=Marking)
    batch_ids: list[uuid.UUID] | None = Field(None, description="Limit to some of the subject's batches.")
    languages: list[str] | None = Field(None, max_length=20,
                                        description='Lock the exam to these languages, e.g. ["python3"].')
    question_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500, description="The question pool.")


class ExamPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    instructions_md: str | None = Field(None, max_length=20_000)
    duration_minutes: int | None = Field(None, ge=5, le=600)
    join_window_minutes: int | None = Field(None, ge=0, le=600)
    selection: Selection | None = None
    policy: ExamPolicy | None = None
    marking: Marking | None = None
    batch_ids: list[uuid.UUID] | None = None
    languages: list[str] | None = Field(None, max_length=20)


class PoolIn(BaseModel):
    question_ids: list[uuid.UUID] = Field(max_length=500)


class ExamOut(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    title: str
    instructions_md: str
    duration_minutes: int
    join_window_minutes: int
    status: ExamStatus
    selection: dict[str, Any]
    policy: dict[str, Any]
    marking: dict[str, Any]
    batch_ids: list[uuid.UUID] | None
    languages: list[str] | None
    question_ids: list[uuid.UUID]
    access_code: str | None
    opened_at: datetime | None
    closed_at: datetime | None
    results_released_at: datetime | None
    created_at: datetime
    updated_at: datetime
    my_caps: list[Cap]


class ReadinessOut(BaseModel):
    ready: bool
    problems: list[str]
    warnings: list[str]


class MonitorRow(BaseModel):
    student: UserBrief
    batch_code: str | None = None
    session_id: uuid.UUID | None
    status: str  # not_started | active | locked | submitted | terminated
    connected: bool
    strikes: int
    lock_reason: str | None
    device_change_pending: bool
    flagged: bool
    started_at: datetime | None
    deadline_at: datetime | None
    finished_at: datetime | None
    finish_reason: str | None
    last_heartbeat_at: datetime | None
    event_count: int
    grading_status: str | None


class MonitorOut(BaseModel):
    exam_id: uuid.UUID
    exam_status: ExamStatus
    server_now: datetime
    counts: dict[str, int]
    rows: list[MonitorRow]


class EventOut(BaseModel):
    id: uuid.UUID
    kind: str
    severity: Severity
    source: str
    details: dict[str, Any]
    client_ts: datetime | None
    created_at: datetime
    caused_lock: bool


class AnswerOut(BaseModel):
    question_id: uuid.UUID
    title: str
    ordinal: int
    marks: int
    auto_max: float
    manual_max: float
    language: str | None
    code: str | None
    revision: int | None
    updated_at: datetime | None
    is_final: bool
    receipt: str | None
    auto_fraction: float | None
    auto_details: dict[str, Any] | None
    manual_marks: float | None
    override_marks: float | None
    manual_note: str | None
    total: float


class RunBrief(BaseModel):
    id: uuid.UUID
    question_id: uuid.UUID
    kind: str
    language: str
    status: str
    fraction: float | None
    verdicts: dict[str, int]
    created_at: datetime


class SessionDetail(BaseModel):
    row: MonitorRow
    events: list[EventOut]
    answers: list[AnswerOut]
    runs: list[RunBrief]


class RunDetail(RunBrief):
    code: str
    results: list[dict[str, Any]]


class LockIn(BaseModel):
    reason: str = Field("locked_by_professor", min_length=1, max_length=60)


class ResumeIn(BaseModel):
    extra_minutes: int = Field(0, ge=0, le=240)
    reset_strikes: bool = True
    approve_new_device: bool = Field(True, description="If the student switched laptops, move them to the new one.")


class TerminateIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ExtendIn(BaseModel):
    minutes: int = Field(ge=1, le=240)
    reason: str = Field("", max_length=200, description="Shown in the student's timeline, e.g. 'laptop restarted'.")


class ExtendAllOut(BaseModel):
    extended: int  # sessions that got the extra time
    minutes: int


class MarksIn(BaseModel):
    manual_marks: float | None = Field(None, ge=0)
    override_marks: float | None = Field(None, ge=0, description="Replaces the computed total for this answer.")
    note: str | None = Field(None, max_length=2000)


class QuestionResult(BaseModel):
    question_id: uuid.UUID
    title: str
    total: float
    max: int


class ResultRow(BaseModel):
    student: UserBrief
    batch_id: uuid.UUID | None
    batch_code: str | None
    languages: list[str]
    session_id: uuid.UUID
    status: SessionStatus
    finish_reason: str | None
    flagged: bool
    grading_status: str
    total: float
    max_total: int
    questions: list[QuestionResult]


class AnswerRow(BaseModel):
    session_id: uuid.UUID
    student: UserBrief
    batch_code: str | None
    question_id: uuid.UUID
    question_title: str
    language: str
    code: str
    is_final: bool
    updated_at: datetime
    auto_fraction: float | None
    manual_marks: float | None
    override_marks: float | None
    total: float
    marks: int


class AnswerPage(BaseModel):
    total: int
    items: list[AnswerRow]


# ------------------------------------------------------------------------------ helpers


async def _exam_for(db: AsyncSession, user: User, exam_id: uuid.UUID, cap: Cap) -> tuple[Exam, set[Cap]]:
    exam = await db.get(Exam, exam_id)
    if exam is None:
        raise not_found()
    caps = await require_cap(db, user, exam.subject_id, cap, exam_id=exam.id)
    return exam, caps


async def _record(db, state: AppState, user: User | None, action: str, target_type: str, target_id, details=None):
    await audit.record(db, state.settings.audit_key_bytes, actor=user, action=action,
                       target_type=target_type, target_id=target_id, details=details)


async def _pool_ids(db: AsyncSession, exam_id: uuid.UUID) -> list[uuid.UUID]:
    return list((await db.scalars(select(ExamPoolQuestion.question_id).where(ExamPoolQuestion.exam_id == exam_id))).all())


async def _set_pool(db: AsyncSession, exam: Exam, question_ids: list[uuid.UUID]) -> None:
    ids = list(dict.fromkeys(question_ids))
    if ids:
        found = set((await db.scalars(select(Question.id).where(
            Question.id.in_(ids), Question.subject_id == exam.subject_id
        ))).all())
        if set(ids) - found:
            raise bad_request("unknown_question", "Every pool question must belong to this subject.")
    await db.execute(delete(ExamPoolQuestion).where(ExamPoolQuestion.exam_id == exam.id))
    for qid in ids:
        db.add(ExamPoolQuestion(exam_id=exam.id, question_id=qid))


async def _validate_batches(db: AsyncSession, subject_id: uuid.UUID, batch_ids: list[uuid.UUID] | None) -> None:
    if not batch_ids:
        return
    linked = set((await db.scalars(select(SubjectBatch.batch_id).where(SubjectBatch.subject_id == subject_id))).all())
    if set(batch_ids) - linked:
        raise bad_request("unknown_batch", "Exam batches must be linked to the subject first.")


async def _validate_languages(db: AsyncSession, subject_id: uuid.UUID, keys: list[str] | None) -> list[str] | None:
    """An exam's language lock must use languages the subject teaches."""
    if not keys:
        return None
    keys = sorted(set(keys))
    taught = set((await db.scalars(
        select(Language.key).join(SubjectLanguage, SubjectLanguage.language_id == Language.id)
        .where(SubjectLanguage.subject_id == subject_id, Language.is_enabled)
    )).all())
    missing = set(keys) - taught
    if missing:
        raise bad_request("unknown_language",
                          f"Not enabled for this subject: {', '.join(sorted(missing))}. Add them to the subject first.")
    return keys


async def batch_codes(db: AsyncSession, exam: Exam) -> dict[uuid.UUID, str]:
    """Each enrolled student's batch for this exam (their first matching batch, by code)."""
    allowed = select(SubjectBatch.batch_id).where(SubjectBatch.subject_id == exam.subject_id)
    query = (select(BatchMember.user_id, Batch.code).join(Batch, Batch.id == BatchMember.batch_id)
             .where(BatchMember.batch_id.in_(allowed)).order_by(Batch.code))
    if exam.batch_ids:
        query = query.where(BatchMember.batch_id.in_([uuid.UUID(str(b)) for b in exam.batch_ids]))
    codes: dict[uuid.UUID, str] = {}
    for user_id, code in (await db.execute(query)).all():
        codes.setdefault(user_id, code)
    return codes


async def _exam_out(db: AsyncSession, state: AppState, exam: Exam, caps: set[Cap]) -> ExamOut:
    code = None
    if exam.access_code_enc and exam.status == ExamStatus.live and Cap.proctor in caps:
        code = state.cipher.decrypt_str(exam.access_code_enc, AAD.access_code(exam.id))
    return ExamOut(
        id=exam.id, subject_id=exam.subject_id, title=exam.title, instructions_md=exam.instructions_md,
        duration_minutes=exam.duration_minutes, join_window_minutes=exam.join_window_minutes, status=exam.status,
        selection=Selection.model_validate(exam.selection or {}).model_dump(mode="json"),
        policy=effective_policy(exam.policy).model_dump(mode="json"),
        marking=Marking.model_validate(exam.marking or {}).model_dump(), batch_ids=exam.batch_ids,
        languages=exam.languages,
        question_ids=await _pool_ids(db, exam.id), access_code=code, opened_at=exam.opened_at,
        closed_at=exam.closed_at, results_released_at=exam.results_released_at, created_at=exam.created_at,
        updated_at=exam.updated_at, my_caps=sorted(caps),
    )


async def _enrolled_students(db: AsyncSession, exam: Exam) -> list[User]:
    batch_ids = select(SubjectBatch.batch_id).where(SubjectBatch.subject_id == exam.subject_id)
    query = (
        select(User).join(BatchMember, BatchMember.user_id == User.id)
        .where(BatchMember.batch_id.in_(batch_ids), User.is_active)
    )
    if exam.batch_ids:
        query = query.where(BatchMember.batch_id.in_([uuid.UUID(str(b)) for b in exam.batch_ids]))
    return list((await db.scalars(query.distinct().order_by(User.full_name))).all())


def _row(student: User, session: ExamSession | None, events: int, policy: ExamPolicy, now: datetime,
         batch_code: str | None = None) -> MonitorRow:
    if session is None:
        return MonitorRow(student=UserBrief.model_validate(student), batch_code=batch_code, session_id=None,
                          status="not_started",
                          connected=False, strikes=0, lock_reason=None, device_change_pending=False, flagged=False,
                          started_at=None, deadline_at=None, finished_at=None, finish_reason=None,
                          last_heartbeat_at=None, event_count=0, grading_status=None)
    connected = bool(session.last_heartbeat_at and
                     now - session.last_heartbeat_at <= timedelta(seconds=policy.heartbeat_timeout_seconds))
    return MonitorRow(
        student=UserBrief.model_validate(student), batch_code=batch_code, session_id=session.id,
        status=session.status.value,
        connected=connected and session.status not in FINISHED, strikes=session.strikes,
        lock_reason=session.lock_reason, device_change_pending=session.pending_device_id is not None,
        flagged=session.flagged, started_at=session.started_at, deadline_at=session.deadline_at,
        finished_at=session.finished_at, finish_reason=session.finish_reason,
        last_heartbeat_at=session.last_heartbeat_at, event_count=events, grading_status=session.grading_status,
    )


def _marks(question: Question, marking: Marking, answer: Answer | None) -> tuple[float, float, float]:
    auto_max = question.marks * marking.auto_percent / 100
    manual_max = question.marks - auto_max
    if answer is None:
        return 0.0, auto_max, manual_max
    if answer.override_marks is not None:
        return round(answer.override_marks, 2), auto_max, manual_max
    auto = (answer.auto_fraction or 0.0) * auto_max
    manual = min(answer.manual_marks or 0.0, manual_max)
    return round(auto + manual, 2), auto_max, manual_max


async def _session_in_exam(db: AsyncSession, exam: Exam, session_id: uuid.UUID, *, for_update=False) -> ExamSession:
    session = await db.get(ExamSession, session_id, with_for_update=for_update or None,
                           populate_existing=for_update)
    if session is None or session.exam_id != exam.id:
        raise not_found()
    return session


def _first_bad_input(grader, question: Question, tests) -> str | None:
    """e.g. a function question whose test inputs are not JSON argument lists."""
    try:
        config = grader.validate_config(question.grader_config or {})
    except ValueError as exc:
        return f"has an invalid grader setting: {exc}"
    for t in tests:
        try:
            grader.validate_test_input(t.input, config)
        except ValueError as exc:
            return f"test {t.ordinal}: {exc}"
    return None


async def check_ready(db: AsyncSession, state: AppState, exam: Exam) -> ReadinessOut:
    problems: list[str] = []
    warnings: list[str] = []
    pool = (await db.scalars(select(Question).join(ExamPoolQuestion, ExamPoolQuestion.question_id == Question.id)
                             .where(ExamPoolQuestion.exam_id == exam.id))).all()
    if not pool:
        problems.append("The question pool is empty.")
    for q in pool:
        name = f"'{q.title}'"
        if q.archived:
            problems.append(f"{name} is archived; remove it from the pool.")
        counts = (await db.execute(select(
            func.count(TestCase.id), func.count(TestCase.id).filter(TestCase.expected_enc.is_(None)),
            func.count(TestCase.id).filter(TestCase.is_sample.is_(False)),
        ).where(TestCase.question_id == q.id))).one()
        grader = get_grader(q.grader)
        missing_expected = counts[1] if grader.needs_expected else 0
        if counts[0] == 0:
            problems.append(f"{name} has no tests.")
        elif missing_expected:
            problems.append(f"{name} has {counts[1]} test(s) without an expected output.")
        elif counts[2] == 0:
            warnings.append(f"{name} has only sample tests; marks will depend on visible tests.")
        if not await languages_for_question(db, q, exam):
            locked = f" in {', '.join(exam.languages)}" if exam.languages else ""
            problems.append(f"{name} cannot be answered{locked}. Check the question's and the exam's languages.")
        tests = await load_tests(db, state.cipher, q.id) if counts[0] else []
        bad_input = _first_bad_input(grader, q, tests)
        if bad_input:
            problems.append(f"{name} {bad_input}")
        elif q.reference_enc and q.reference_language and counts[0] and not missing_expected:
            lang = await db.scalar(select(Language).where(Language.key == q.reference_language, Language.is_enabled))
            if lang is not None:
                result = await grade_code(state, q, lang, state.cipher.decrypt_str(q.reference_enc, AAD.reference(q.id)),
                                          tests)
                if result.fraction < 1:
                    problems.append(f"{name}: the reference solution fails some tests.")
        elif not q.reference_enc:
            warnings.append(f"{name} has no reference solution, so its tests were not double-checked.")
    if pool:
        error = check_selection(list(pool), Selection.model_validate(exam.selection or {}))
        if error:
            problems.append(error)
    if not await _enrolled_students(db, exam):
        problems.append("No students are enrolled. Link a batch with students to the subject.")
    return ReadinessOut(ready=not problems, problems=problems, warnings=warnings)


# ------------------------------------------------------------------------------- setup


@router.get("/subjects/{subject_id}/exams", response_model=list[ExamOut])
async def list_exams(subject_id: uuid.UUID, state: AppState = Depends(get_state),
                     db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    subject_caps = await caps_for(db, user, subject_id)
    exams = (await db.scalars(select(Exam).where(Exam.subject_id == subject_id).order_by(Exam.created_at.desc()))).all()
    if Cap.view not in subject_caps:
        granted = set((await db.scalars(select(AccessGrant.exam_id).where(
            AccessGrant.subject_id == subject_id, AccessGrant.user_id == user.id, AccessGrant.exam_id.is_not(None)
        ))).all())
        if not granted:
            raise not_found()
        exams = [e for e in exams if e.id in granted]
    return [await _exam_out(db, state, e, await caps_for(db, user, subject_id, e.id)) for e in exams]


@router.post("/subjects/{subject_id}/exams", response_model=ExamOut, status_code=201)
async def create_exam(subject_id: uuid.UUID, body: ExamIn, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    caps = await require_cap(db, user, subject_id, Cap.edit)
    if await db.get(Subject, subject_id) is None:
        raise not_found()
    await _validate_batches(db, subject_id, body.batch_ids)
    languages = await _validate_languages(db, subject_id, body.languages)
    exam = Exam(
        id=uuid.uuid4(), subject_id=subject_id, title=body.title.strip(), instructions_md=body.instructions_md,
        duration_minutes=body.duration_minutes, join_window_minutes=body.join_window_minutes,
        selection=body.selection.model_dump(mode="json"), policy=body.policy.model_dump(mode="json"),
        marking=body.marking.model_dump(), batch_ids=[str(b) for b in body.batch_ids] if body.batch_ids else None,
        languages=languages,
        created_by=user.id,
    )
    db.add(exam)
    await db.flush()
    await _set_pool(db, exam, body.question_ids)
    await _record(db, state, user, "exam.created", "exam", exam.id, {"title": exam.title})
    await db.commit()
    return await _exam_out(db, state, exam, caps)


@router.get("/exams/{exam_id}", response_model=ExamOut)
async def get_exam(exam_id: uuid.UUID, state: AppState = Depends(get_state),
                   db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    exam, caps = await _exam_for(db, user, exam_id, Cap.view)
    return await _exam_out(db, state, exam, caps)


@router.patch("/exams/{exam_id}", response_model=ExamOut)
async def update_exam(exam_id: uuid.UUID, body: ExamPatch, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    exam, caps = await _exam_for(db, user, exam_id, Cap.edit)
    changes = body.model_dump(exclude_unset=True)
    if exam.status == ExamStatus.closed:
        raise conflict("exam_closed", "A closed exam cannot be changed.")
    if exam.status == ExamStatus.live:
        locked_fields = set(changes) - {"title", "instructions_md", "join_window_minutes", "policy"}
        if locked_fields:
            raise conflict("exam_live", f"While live, these cannot change: {', '.join(sorted(locked_fields))}.")
    if "batch_ids" in changes:
        await _validate_batches(db, exam.subject_id, body.batch_ids)
        exam.batch_ids = [str(b) for b in body.batch_ids] if body.batch_ids else None
    if "languages" in changes:
        exam.languages = await _validate_languages(db, exam.subject_id, body.languages)
    for field in ("title", "instructions_md", "duration_minutes", "join_window_minutes"):
        if field in changes:
            setattr(exam, field, changes[field])
    if body.selection is not None:
        exam.selection = body.selection.model_dump(mode="json")
    if body.policy is not None:
        exam.policy = body.policy.model_dump(mode="json")
    if body.marking is not None:
        exam.marking = body.marking.model_dump()
    exam.updated_at = utcnow()
    await _record(db, state, user, "exam.updated", "exam", exam.id, {"fields": sorted(changes)})
    await db.commit()
    return await _exam_out(db, state, exam, caps)


@router.put("/exams/{exam_id}/pool", response_model=ExamOut)
async def set_pool(exam_id: uuid.UUID, body: PoolIn, state: AppState = Depends(get_state),
                   db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    exam, caps = await _exam_for(db, user, exam_id, Cap.edit)
    if exam.status != ExamStatus.draft:
        raise conflict("exam_not_draft", "The pool can only change before the exam opens.")
    await _set_pool(db, exam, body.question_ids)
    exam.updated_at = utcnow()
    await _record(db, state, user, "exam.pool_set", "exam", exam.id, {"questions": len(body.question_ids)})
    await db.commit()
    return await _exam_out(db, state, exam, caps)


@router.get("/exams/{exam_id}/readiness", response_model=ReadinessOut)
async def readiness(exam_id: uuid.UUID, state: AppState = Depends(get_state),
                    db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    """Everything that would stop the exam from opening, checked without opening it."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.view)
    return await check_ready(db, state, exam)


@router.post("/exams/{exam_id}/open", response_model=ExamOut)
async def open_exam(exam_id: uuid.UUID, state: AppState = Depends(get_state),
                    db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    """Open the exam and create its access code. Show the code in the lab; students
    need it to start, so nobody can begin from outside the room or before you say so."""
    exam, caps = await _exam_for(db, user, exam_id, Cap.proctor)
    if exam.status != ExamStatus.draft:
        raise conflict("exam_not_draft", "Only a draft exam can be opened.")
    report = await check_ready(db, state, exam)
    if not report.ready:
        raise ApiError(409, "exam_not_ready", "The exam is not ready to open.", problems=report.problems)
    exam.status = ExamStatus.live
    exam.opened_at = utcnow()
    exam.access_code_enc = state.cipher.encrypt(new_access_code(), AAD.access_code(exam.id))
    exam.updated_at = exam.opened_at
    await _record(db, state, user, "exam.opened", "exam", exam.id)
    await db.commit()
    await notify(state, exam.id, "exam_opened")
    return await _exam_out(db, state, exam, caps)


@router.post("/exams/{exam_id}/rotate-code", response_model=ExamOut)
async def rotate_code(exam_id: uuid.UUID, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    """Replace the access code, e.g. if it was shared outside the room."""
    exam, caps = await _exam_for(db, user, exam_id, Cap.proctor)
    if exam.status != ExamStatus.live:
        raise conflict("exam_not_live", "The exam is not open.")
    exam.access_code_enc = state.cipher.encrypt(new_access_code(), AAD.access_code(exam.id))
    await _record(db, state, user, "exam.code_rotated", "exam", exam.id)
    await db.commit()
    return await _exam_out(db, state, exam, caps)


@router.post("/exams/{exam_id}/close", response_model=ExamOut)
async def close_exam(exam_id: uuid.UUID, state: AppState = Depends(get_state),
                     db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    """End the exam for everyone: unfinished sessions are submitted with their saved code."""
    exam, caps = await _exam_for(db, user, exam_id, Cap.proctor)
    if exam.status != ExamStatus.live:
        raise conflict("exam_not_live", "The exam is not open.")
    exam.status = ExamStatus.closed
    exam.closed_at = utcnow()
    finished = []
    sessions = (await db.scalars(select(ExamSession).where(
        ExamSession.exam_id == exam.id, ExamSession.status.in_([SessionStatus.active, SessionStatus.locked])
    ).with_for_update())).all()
    for session in sessions:
        if await finalize_session(db, state.cipher, session, "exam_closed", now=exam.closed_at):
            finished.append(session.id)
    await _record(db, state, user, "exam.closed", "exam", exam.id, {"auto_submitted": len(finished)})
    await db.commit()
    await notify(state, exam.id, "exam_closed")
    for session_id in finished:
        state.tasks.spawn(grade_session(state, session_id))
    return await _exam_out(db, state, exam, caps)


@router.post("/exams/{exam_id}/release-results", response_model=ExamOut)
async def release_results(exam_id: uuid.UUID, state: AppState = Depends(get_state),
                          db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    exam, caps = await _exam_for(db, user, exam_id, Cap.edit)
    if exam.status != ExamStatus.closed:
        raise conflict("exam_not_closed", "Close the exam before releasing results.")
    exam.results_released_at = utcnow()
    await _record(db, state, user, "exam.results_released", "exam", exam.id)
    await db.commit()
    return await _exam_out(db, state, exam, caps)


@router.post("/exams/{exam_id}/regrade")
async def regrade(exam_id: uuid.UUID, everyone: bool = Query(False, description="Re-grade all, not only failed ones."),
                  state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                  user: User = Depends(require_professor)) -> dict:
    """Re-run final grading, e.g. after fixing a wrong test. Manual marks are kept."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.edit)
    query = select(ExamSession).where(ExamSession.exam_id == exam.id, ExamSession.status.in_(FINISHED))
    if not everyone:
        query = query.where(ExamSession.grading_status.in_(["error", "pending"]))
    sessions = (await db.scalars(query)).all()
    for s in sessions:
        s.grading_status = "pending"
    await _record(db, state, user, "exam.regraded", "exam", exam.id, {"sessions": len(sessions), "all": everyone})
    await db.commit()
    await notify(state, exam.id, "regrading")
    for s in sessions:
        state.tasks.spawn(grade_session(state, s.id))
    return {"queued": len(sessions)}


# ----------------------------------------------------------------------------- monitor


@router.get("/exams/{exam_id}/monitor", response_model=MonitorOut)
async def monitor(exam_id: uuid.UUID, batch_code: str | None = Query(None, max_length=64),
                  db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    """Every enrolled student's status. For instant updates open the live WebSocket
    instead of polling (POST /exams/{id}/live-ticket, then /exams/{id}/live)."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.view)
    return await monitor_snapshot(db, exam, batch_code)


def monitor_counts(rows: list[MonitorRow]) -> dict[str, int]:
    counts = Counter(r.status for r in rows)
    counts["disconnected"] = sum(1 for r in rows if r.status in ("active", "locked") and not r.connected)
    return dict(counts)


async def monitor_snapshot(db: AsyncSession, exam: Exam, batch_code: str | None = None) -> MonitorOut:
    policy = effective_policy(exam.policy)
    now = utcnow()
    students = {s.id: s for s in await _enrolled_students(db, exam)}
    sessions = {s.student_id: s for s in (await db.scalars(select(ExamSession).where(ExamSession.exam_id == exam.id))).all()}
    for student_id in set(sessions) - set(students):  # e.g. moved out of the batch after starting
        student = await db.get(User, student_id)
        if student:
            students[student_id] = student
    event_counts = dict((await db.execute(
        select(ProctorEvent.session_id, func.count()).where(
            ProctorEvent.session_id.in_([s.id for s in sessions.values()])
        ).group_by(ProctorEvent.session_id)
    )).all()) if sessions else {}
    enrolled_codes = await batch_codes(db, exam)
    session_codes = await _batch_code_map(db, {x.batch_id for x in sessions.values() if x.batch_id})

    def code_for(student_id: uuid.UUID) -> str | None:
        session = sessions.get(student_id)
        if session is not None and session.batch_id:
            return session_codes.get(session.batch_id)
        return enrolled_codes.get(student_id)

    rows = [_row(st, sessions.get(sid), event_counts.get(sessions[sid].id, 0) if sid in sessions else 0, policy, now,
                 batch_code=code_for(sid))
            for sid, st in students.items()]
    if batch_code:
        rows = [r for r in rows if r.batch_code == batch_code]
    rows.sort(key=lambda r: ({"locked": 0, "active": 1, "not_started": 2}.get(r.status, 3), r.student.full_name))
    return MonitorOut(exam_id=exam.id, exam_status=exam.status, server_now=now, counts=monitor_counts(rows), rows=rows)


async def monitor_row(db: AsyncSession, exam: Exam, session: ExamSession) -> MonitorRow:
    """One student's monitor row, exactly as it appears in the full monitor."""
    student = await db.get(User, session.student_id)
    events = await db.scalar(select(func.count()).select_from(ProctorEvent)
                             .where(ProctorEvent.session_id == session.id)) or 0
    if session.batch_id:
        code = (await _batch_code_map(db, {session.batch_id})).get(session.batch_id)
    else:
        code = (await batch_codes(db, exam)).get(session.student_id)
    return _row(student, session, events, effective_policy(exam.policy), utcnow(), batch_code=code)


@router.get("/exams/{exam_id}/sessions/{session_id}", response_model=SessionDetail)
async def session_detail(exam_id: uuid.UUID, session_id: uuid.UUID, state: AppState = Depends(get_state),
                         db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    exam, _ = await _exam_for(db, user, exam_id, Cap.view)
    session = await _session_in_exam(db, exam, session_id)
    student = await db.get(User, session.student_id)
    policy = effective_policy(exam.policy)
    marking = Marking.model_validate(exam.marking or {})
    events = (await db.scalars(select(ProctorEvent).where(ProctorEvent.session_id == session.id)
                               .order_by(ProctorEvent.created_at))).all()
    assigned = (await db.execute(select(Question, SessionQuestion.ordinal)
                                 .join(SessionQuestion, SessionQuestion.question_id == Question.id)
                                 .where(SessionQuestion.session_id == session.id)
                                 .order_by(SessionQuestion.ordinal))).all()
    answers = {a.question_id: a for a in (await db.scalars(select(Answer).where(Answer.session_id == session.id))).all()}
    answer_out = []
    for q, ordinal in assigned:
        a = answers.get(q.id)
        total, auto_max, manual_max = _marks(q, marking, a)
        answer_out.append(AnswerOut(
            question_id=q.id, title=q.title, ordinal=ordinal, marks=q.marks, auto_max=auto_max, manual_max=manual_max,
            language=a.language if a else None,
            code=state.cipher.decrypt_str(a.code_enc, AAD.answer(session.id, q.id)) if a else None,
            revision=a.revision if a else None, updated_at=a.updated_at if a else None,
            is_final=a.is_final if a else False, receipt=a.receipt if a else None,
            auto_fraction=a.auto_fraction if a else None, auto_details=a.auto_details if a else None,
            manual_marks=a.manual_marks if a else None, override_marks=a.override_marks if a else None,
            manual_note=a.manual_note if a else None, total=total,
        ))
    runs = (await db.scalars(select(Run).where(Run.session_id == session.id).order_by(Run.created_at))).all()
    return SessionDetail(
        row=_row(student, session, len(events), policy, utcnow()),
        events=[EventOut.model_validate(e, from_attributes=True) for e in events],
        answers=answer_out,
        runs=[RunBrief(id=r.id, question_id=r.question_id, kind=r.kind.value, language=r.language, status=r.status,
                       fraction=r.fraction, verdicts=dict(Counter(t.get("verdict") for t in r.results or [])),
                       created_at=r.created_at) for r in runs],
    )


@router.get("/exams/{exam_id}/sessions/{session_id}/runs/{run_id}", response_model=RunDetail)
async def run_detail(exam_id: uuid.UUID, session_id: uuid.UUID, run_id: uuid.UUID,
                     state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                     user: User = Depends(require_professor)):
    exam, _ = await _exam_for(db, user, exam_id, Cap.view)
    session = await _session_in_exam(db, exam, session_id)
    run = await db.get(Run, run_id)
    if run is None or run.session_id != session.id:
        raise not_found()
    return RunDetail(id=run.id, question_id=run.question_id, kind=run.kind.value, language=run.language,
                     status=run.status, fraction=run.fraction,
                     verdicts=dict(Counter(t.get("verdict") for t in run.results or [])), created_at=run.created_at,
                     code=state.cipher.decrypt_str(run.code_enc, AAD.run(run.id)), results=run.results or [])


# ---------------------------------------------------------------------- proctor actions


def _staff_event(session: ExamSession, kind: str, details: dict | None = None) -> ProctorEvent:
    return ProctorEvent(session_id=session.id, kind=kind, severity=Severity.info, source="staff",
                        details=details or {})


@router.post("/exams/{exam_id}/sessions/{session_id}/lock", response_model=MonitorRow)
async def lock_session(exam_id: uuid.UUID, session_id: uuid.UUID, body: LockIn,
                       state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                       user: User = Depends(require_professor)):
    exam, _ = await _exam_for(db, user, exam_id, Cap.proctor)
    session = await _session_in_exam(db, exam, session_id, for_update=True)
    if session.status != SessionStatus.active:
        raise conflict("not_active", "Only an active session can be locked.")
    lock(session, f"staff:{body.reason}")
    db.add(_staff_event(session, "staff_lock", {"by": user.email, "reason": body.reason}))
    await _record(db, state, user, "session.locked", "exam_session", session.id, {"reason": body.reason})
    await db.commit()
    await notify(state, exam.id, "locked", session.id)
    return await monitor_row(db, exam, session)


@router.post("/exams/{exam_id}/sessions/{session_id}/resume", response_model=MonitorRow)
async def resume_session(exam_id: uuid.UUID, session_id: uuid.UUID, body: ResumeIn,
                         state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                         user: User = Depends(require_professor)):
    """Let a locked student continue. Optionally add time (after a false alarm) and move
    the session to a new laptop if they switched devices."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.proctor)
    session = await _session_in_exam(db, exam, session_id, for_update=True)
    if session.status != SessionStatus.locked:
        raise conflict("not_locked", "Only a locked session can be resumed.")
    if exam.status != ExamStatus.live:
        raise conflict("exam_not_live", "The exam is closed.")
    now = utcnow()
    new_deadline = session.deadline_at + timedelta(minutes=body.extra_minutes)
    if new_deadline <= now:
        raise conflict("time_over", "This student's time is over. Add extra minutes to resume.")
    details: dict[str, Any] = {"by": user.email, "extra_minutes": body.extra_minutes,
                               "reset_strikes": body.reset_strikes, "lock_reason": session.lock_reason}
    if session.pending_device_id is not None:
        details["device_change"] = "approved" if body.approve_new_device else "rejected"
        if body.approve_new_device:
            session.device_id = session.pending_device_id
        session.pending_device_id = None
    session.deadline_at = new_deadline
    session.status = SessionStatus.active
    session.lock_reason = None
    session.locked_at = None
    if body.reset_strikes:
        session.strikes = 0
    db.add(_staff_event(session, "staff_resume", details))
    await _record(db, state, user, "session.resumed", "exam_session", session.id, details)
    await db.commit()
    await notify(state, exam.id, "resumed", session.id)
    return await monitor_row(db, exam, session)


@router.post("/exams/{exam_id}/sessions/{session_id}/extend", response_model=MonitorRow)
async def extend_session(exam_id: uuid.UUID, session_id: uuid.UUID, body: ExtendIn,
                         state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                         user: User = Depends(require_professor)):
    """Give one student extra time (working or paused): e.g. their laptop failed, they
    lost time moving to another one, or they have an accommodation. Their app picks up
    the new end time within seconds."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.proctor)
    session = await _session_in_exam(db, exam, session_id, for_update=True)
    if exam.status != ExamStatus.live:
        raise conflict("exam_not_live", "The exam is closed.")
    if session.status not in (SessionStatus.active, SessionStatus.locked):
        raise conflict("session_finished", "This student has already finished; time can't be added.")
    session.deadline_at = session.deadline_at + timedelta(minutes=body.minutes)
    details = {"by": user.email, "extra_minutes": body.minutes, "reason": body.reason}
    db.add(_staff_event(session, "staff_extend", details))
    await _record(db, state, user, "session.extended", "exam_session", session.id, details)
    await db.commit()
    await notify(state, exam.id, "extended", session.id)
    return await monitor_row(db, exam, session)


@router.post("/exams/{exam_id}/extend", response_model=ExtendAllOut)
async def extend_exam(exam_id: uuid.UUID, body: ExtendIn, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    """Give everyone who is still working (or paused) extra time, e.g. after a power cut
    or a network outage in the lab. Students who start later get the full duration anyway."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.proctor)
    if exam.status != ExamStatus.live:
        raise conflict("exam_not_live", "The exam is closed.")
    sessions = (await db.scalars(select(ExamSession).where(
        ExamSession.exam_id == exam.id,
        ExamSession.status.in_((SessionStatus.active, SessionStatus.locked))).with_for_update())).all()
    details = {"by": user.email, "extra_minutes": body.minutes, "reason": body.reason, "whole_exam": True}
    for session in sessions:
        session.deadline_at = session.deadline_at + timedelta(minutes=body.minutes)
        db.add(_staff_event(session, "staff_extend", details))
    await _record(db, state, user, "exam.extended", "exam", exam.id, {**details, "sessions": len(sessions)})
    await db.commit()
    await notify(state, exam.id, RESYNC)
    return ExtendAllOut(extended=len(sessions), minutes=body.minutes)


@router.post("/exams/{exam_id}/sessions/{session_id}/terminate", response_model=MonitorRow)
async def terminate_session(exam_id: uuid.UUID, session_id: uuid.UUID, body: TerminateIn,
                            state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                            user: User = Depends(require_professor)):
    """End this student's exam now. Their saved code is submitted and graded; any
    misconduct penalty is decided separately by the professor."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.proctor)
    session = await _session_in_exam(db, exam, session_id, for_update=True)
    if session.status in FINISHED:
        raise conflict("session_finished", "This session has already ended.")
    await finalize_session(db, state.cipher, session, "terminated")
    session.flagged = True
    db.add(_staff_event(session, "staff_terminate", {"by": user.email, "reason": body.reason}))
    await _record(db, state, user, "session.terminated", "exam_session", session.id, {"reason": body.reason})
    await db.commit()
    await notify(state, exam.id, "terminated", session.id)
    state.tasks.spawn(grade_session(state, session.id))
    return await monitor_row(db, exam, session)


# ------------------------------------------------------------------------------- marks


@router.put("/exams/{exam_id}/sessions/{session_id}/answers/{question_id}/marks", response_model=AnswerOut)
async def set_marks(exam_id: uuid.UUID, session_id: uuid.UUID, question_id: uuid.UUID, body: MarksIn,
                    state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                    user: User = Depends(require_professor)):
    """Manual marks (e.g. for logic in half-written code) or a full override. Every change
    is kept in the audit log with the old and new values."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.grade)
    session = await _session_in_exam(db, exam, session_id)
    if session.status not in FINISHED:
        raise conflict("session_not_finished", "Marks can be given after the student's exam ends.")
    question = await db.get(Question, question_id)
    answer = await db.scalar(select(Answer).where(Answer.session_id == session.id, Answer.question_id == question_id))
    if question is None or answer is None:
        raise not_found("No answer was saved for this question.")
    marking = Marking.model_validate(exam.marking or {})
    _, auto_max, manual_max = _marks(question, marking, answer)
    if body.manual_marks is not None and body.manual_marks > manual_max + 1e-9:
        raise bad_request("too_many_marks", f"Manual marks for this question are out of {manual_max:g}.")
    if body.override_marks is not None and body.override_marks > question.marks + 1e-9:
        raise bad_request("too_many_marks", f"This question is out of {question.marks}.")
    if body.override_marks is not None and not (body.note or "").strip():
        raise bad_request("note_required", "Add a note explaining the override.")
    before = {"manual_marks": answer.manual_marks, "override_marks": answer.override_marks}
    answer.manual_marks = body.manual_marks
    answer.override_marks = body.override_marks
    answer.manual_note = body.note
    answer.graded_by = user.id
    answer.graded_at = utcnow()
    await _record(db, state, user, "answer.marks_set", "answer", answer.id, {
        "session_id": str(session.id), "question_id": str(question_id), "before": before,
        "after": {"manual_marks": body.manual_marks, "override_marks": body.override_marks},
    })
    await db.commit()
    total, _, _ = _marks(question, marking, answer)
    ordinal = await db.scalar(select(SessionQuestion.ordinal).where(
        SessionQuestion.session_id == session.id, SessionQuestion.question_id == question_id)) or 0
    return AnswerOut(
        question_id=question.id, title=question.title, ordinal=ordinal, marks=question.marks, auto_max=auto_max,
        manual_max=manual_max, language=answer.language,
        code=state.cipher.decrypt_str(answer.code_enc, AAD.answer(session.id, question.id)),
        revision=answer.revision, updated_at=answer.updated_at, is_final=answer.is_final, receipt=answer.receipt,
        auto_fraction=answer.auto_fraction, auto_details=answer.auto_details, manual_marks=answer.manual_marks,
        override_marks=answer.override_marks, manual_note=answer.manual_note, total=total,
    )


async def _batch_code_map(db: AsyncSession, batch_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not batch_ids:
        return {}
    return dict((await db.execute(select(Batch.id, Batch.code).where(Batch.id.in_(batch_ids)))).all())


async def compute_results(db: AsyncSession, exam: Exam, sessions: list[ExamSession]) -> list[ResultRow]:
    marking = Marking.model_validate(exam.marking or {})
    codes = await _batch_code_map(db, {s.batch_id for s in sessions if s.batch_id})
    rows = []
    for session in sessions:
        student = await db.get(User, session.student_id)
        assigned = (await db.execute(select(Question).join(SessionQuestion, SessionQuestion.question_id == Question.id)
                                     .where(SessionQuestion.session_id == session.id)
                                     .order_by(SessionQuestion.ordinal))).scalars().all()
        answers = {a.question_id: a for a in (await db.scalars(
            select(Answer).where(Answer.session_id == session.id))).all()}
        per_q = [QuestionResult(question_id=q.id, title=q.title, total=_marks(q, marking, answers.get(q.id))[0],
                                max=q.marks) for q in assigned]
        rows.append(ResultRow(
            student=UserBrief.model_validate(student), batch_id=session.batch_id,
            batch_code=codes.get(session.batch_id), languages=sorted({a.language for a in answers.values()}),
            session_id=session.id, status=session.status,
            finish_reason=session.finish_reason, flagged=session.flagged, grading_status=session.grading_status,
            total=round(sum(r.total for r in per_q), 2), max_total=sum(r.max for r in per_q), questions=per_q,
        ))
    return rows


@router.get("/exams/{exam_id}/results", response_model=list[ResultRow])
async def results(exam_id: uuid.UUID, batch_id: uuid.UUID | None = None,
                  language: str | None = Query(None, max_length=32),
                  db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    """Marks per student. Filter by batch (division) or by the language students answered in."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.view)
    query = select(ExamSession).where(ExamSession.exam_id == exam.id)
    if batch_id:
        query = query.where(ExamSession.batch_id == batch_id)
    if language:
        query = query.where(ExamSession.id.in_(select(Answer.session_id).where(Answer.language == language)))
    rows = await compute_results(db, exam, list((await db.scalars(query)).all()))
    return sorted(rows, key=lambda r: (r.batch_code or "", r.student.full_name))


@router.get("/exams/{exam_id}/answers", response_model=AnswerPage)
async def list_answers(
    exam_id: uuid.UUID,
    language: str | None = Query(None, max_length=32, description="e.g. python3: only Python answers"),
    batch_id: uuid.UUID | None = None,
    question_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    state: AppState = Depends(get_state),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_professor),
):
    """Every saved answer of an exam with its code and marks, for checking answers one
    after another. Filter by language, batch and question."""
    exam, _ = await _exam_for(db, user, exam_id, Cap.view)
    marking = Marking.model_validate(exam.marking or {})
    query = (select(Answer, ExamSession, Question, User)
             .join(ExamSession, ExamSession.id == Answer.session_id)
             .join(Question, Question.id == Answer.question_id)
             .join(User, User.id == ExamSession.student_id)
             .where(ExamSession.exam_id == exam.id))
    if language:
        query = query.where(Answer.language == language)
    if batch_id:
        query = query.where(ExamSession.batch_id == batch_id)
    if question_id:
        query = query.where(Answer.question_id == question_id)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await db.execute(query.order_by(Question.title, User.full_name).limit(limit).offset(offset))).all()
    codes = await _batch_code_map(db, {sess.batch_id for _, sess, _, _ in rows if sess.batch_id})
    items = [AnswerRow(
        session_id=sess.id, student=UserBrief.model_validate(student), batch_code=codes.get(sess.batch_id),
        question_id=q.id, question_title=q.title, language=a.language,
        code=state.cipher.decrypt_str(a.code_enc, AAD.answer(sess.id, q.id)), is_final=a.is_final,
        updated_at=a.updated_at, auto_fraction=a.auto_fraction, manual_marks=a.manual_marks,
        override_marks=a.override_marks, total=_marks(q, marking, a)[0], marks=q.marks,
    ) for a, sess, q, student in rows]
    return AnswerPage(total=total or 0, items=items)
