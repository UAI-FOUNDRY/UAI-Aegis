"""Student side, used only by the UAI Aegis desktop app.

Every exam call is signed by the laptop's device key (see security/signing.py) and is
checked against the server-side session: its owner, its bound device, its lock state
and its deadline. A modified or cracked app still cannot read another student's work,
write after time is up, or continue while locked.
"""

from __future__ import annotations

import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import ApiError, bad_request, conflict, forbidden, not_found, too_many
from ...grading import get_grader
from ...grading.registry import Verdict
from ...models import (
    Answer,
    Batch,
    BatchMember,
    ClientRelease,
    Device,
    Exam,
    ExamPoolQuestion,
    ExamSession,
    ExamStatus,
    Language,
    Question,
    Run,
    RunKind,
    SessionQuestion,
    SessionStatus,
    Subject,
    SubjectBatch,
    TestCase,
    User,
    utcnow,
)
from ...security.crypto import AAD
from ...security.signing import b64url_decode, registration_message, verify_signature
from ...services import audit
from ...services.exams import (
    Selection,
    SelectionError,
    finalize_session,
    grade_code,
    grade_session,
    languages_for_question,
    load_tests,
    past_deadline,
    pick_questions,
    time_limit_for,
)
from ...services.live import notify
from ...services.proctoring import EVENT_KIND, ExamPolicy, apply_event, effective_policy, lock
from ...state import AppState
from ..deps import get_db, get_state, require_student, signed_device
from .exams import compute_results

router = APIRouter(prefix="/student", tags=["student"])

FINISHED = (SessionStatus.submitted, SessionStatus.terminated)
MAX_DEVICES = 5


# ------------------------------------------------------------------------------ schemas


class DeviceIn(BaseModel):
    public_key: str = Field(min_length=40, max_length=64, description="Ed25519 public key, raw 32 bytes, base64url")
    proof: str = Field(min_length=40, max_length=128, description="Signature of the registration message")
    label: str = Field(min_length=1, max_length=120)
    platform: str = Field(pattern=r"^(windows|macos|linux)$")


class DeviceOut(BaseModel):
    device_id: uuid.UUID


class StudentExam(BaseModel):
    exam_id: uuid.UUID
    title: str
    subject_code: str
    subject_name: str
    status: ExamStatus
    duration_minutes: int
    opened_at: datetime | None
    join_closes_at: datetime | None
    session_id: uuid.UUID | None
    session_status: SessionStatus | None
    results_released: bool


class StartIn(BaseModel):
    access_code: str | None = Field(None, max_length=32)
    client_version: str | None = Field(None, max_length=32)
    client_build_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")


class LanguageBrief(BaseModel):
    key: str
    name: str
    monaco_id: str


class SampleOut(BaseModel):
    ordinal: int
    input: str
    expected_output: str


class DraftOut(BaseModel):
    language: str
    code: str
    revision: int
    updated_at: datetime


class StudentQuestion(BaseModel):
    id: uuid.UUID
    ordinal: int
    title: str
    statement_md: str
    difficulty: str
    marks: int
    languages: list[LanguageBrief]
    time_limits_ms: dict[str, int]
    starter_code: dict[str, str]
    samples: list[SampleOut]
    hidden_test_count: int
    draft: DraftOut | None


class Receipt(BaseModel):
    question_id: uuid.UUID
    receipt: str


class SessionView(BaseModel):
    session_id: uuid.UUID
    exam_id: uuid.UUID
    exam_title: str
    instructions_md: str
    status: SessionStatus
    lock_reason: str | None
    strikes: int
    max_strikes: int
    device_change_pending: bool
    server_now: datetime
    started_at: datetime
    deadline_at: datetime
    seconds_left: int
    finish_reason: str | None
    questions: list[StudentQuestion]
    receipts: list[Receipt]


class SaveIn(BaseModel):
    language: str = Field(min_length=1, max_length=32)
    code: str
    base_revision: int | None = Field(None, ge=0, description="Revision the app last saw; rejects stale saves.")


class SaveOut(BaseModel):
    revision: int
    saved_at: datetime


class CodeIn(BaseModel):
    language: str = Field(min_length=1, max_length=32)
    code: str


class TestFeedback(BaseModel):
    ordinal: int
    is_sample: bool
    verdict: str
    time_ms: int | None
    input: str | None = None  # samples only
    expected_output: str | None = None  # samples only
    stdout: str | None = None  # samples only
    stderr: str | None = None  # samples only
    message: str | None = None  # e.g. SQL row-count difference or an ML score


class RunOut(BaseModel):
    passed: int
    total: int
    compile_output: str
    tests: list[TestFeedback]
    hidden_summary: str | None = None


class HeartbeatIn(BaseModel):
    focused: bool = True
    fullscreen: bool = True
    displays: int = Field(1, ge=0, le=16)
    client_version: str | None = Field(None, max_length=32)


class EventIn(BaseModel):
    kind: str = Field(max_length=48)
    client_ts: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EventsIn(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=20)


class StatusOut(BaseModel):
    status: SessionStatus
    lock_reason: str | None
    strikes: int
    strikes_left: int
    server_now: datetime
    deadline_at: datetime
    seconds_left: int
    device_change_pending: bool


class FinishOut(BaseModel):
    status: SessionStatus
    receipts: list[Receipt]


class StudentResult(BaseModel):
    exam_id: uuid.UUID
    exam_title: str
    total: float
    max_total: int
    questions: list[dict[str, Any]]


# ------------------------------------------------------------------------------ helpers


async def _record(db, state: AppState, actor: User | None, action: str, session: ExamSession, details=None):
    await audit.record(db, state.settings.audit_key_bytes, actor=actor, action=action, target_type="exam_session",
                       target_id=session.id, details={"exam_id": str(session.exam_id), **(details or {})})


async def _exam_batch(db: AsyncSession, user: User, exam: Exam) -> uuid.UUID | None:
    """The batch the student takes this exam with (first match by code), or None if
    they are not enrolled."""
    query = (select(Batch.id)
             .join(BatchMember, BatchMember.batch_id == Batch.id)
             .join(SubjectBatch, SubjectBatch.batch_id == Batch.id)
             .where(BatchMember.user_id == user.id, SubjectBatch.subject_id == exam.subject_id)
             .order_by(Batch.code))
    allowed = {str(b) for b in exam.batch_ids} if exam.batch_ids else None
    for batch_id in (await db.scalars(query)).all():
        if allowed is None or str(batch_id) in allowed:
            return batch_id
    return None


async def _is_enrolled(db: AsyncSession, user: User, exam: Exam) -> bool:
    return await _exam_batch(db, user, exam) is not None


async def _session_for(
    db: AsyncSession, user: User, device: Device, session_id: uuid.UUID, *, allow_pending: bool = False
) -> tuple[ExamSession, Exam]:
    """The caller's own session, from the laptop it is bound to. Anyone else's session
    looks exactly like a missing one (404)."""
    session = await db.get(ExamSession, session_id, with_for_update=True)
    if session is None or session.student_id != user.id:
        raise not_found()
    if session.device_id != device.id and not (allow_pending and session.pending_device_id == device.id):
        raise conflict("device_mismatch", "This exam is open on another laptop. Ask your professor to move it.")
    return session, await db.get(Exam, session.exam_id)


def _seconds_left(session: ExamSession, now: datetime) -> int:
    if session.status in FINISHED:
        return 0
    return max(0, int((session.deadline_at - now).total_seconds()))


def _status(session: ExamSession, policy: ExamPolicy, now: datetime) -> StatusOut:
    return StatusOut(status=session.status, lock_reason=session.lock_reason, strikes=session.strikes,
                     strikes_left=max(0, policy.max_strikes - session.strikes), server_now=now,
                     deadline_at=session.deadline_at, seconds_left=_seconds_left(session, now),
                     device_change_pending=session.pending_device_id is not None)


async def _time_up(db: AsyncSession, state: AppState, session: ExamSession) -> None:
    if await finalize_session(db, state.cipher, session, "time_up"):
        await _record(db, state, None, "session.auto_submitted", session, {"reason": session.finish_reason})
        await db.commit()
        await notify(state, session.exam_id, "submitted", session.id)
        state.tasks.spawn(grade_session(state, session.id))


async def _ensure_writable(db: AsyncSession, state: AppState, session: ExamSession, exam: Exam) -> None:
    if session.status == SessionStatus.locked:
        raise ApiError(423, "session_locked", "Your exam is paused. Wait for your professor.",
                       lock_reason=session.lock_reason)
    if session.status in FINISHED:
        raise conflict("session_finished", "Your exam has ended.")
    if exam.status == ExamStatus.closed:  # e.g. started in the instant the exam was being closed
        if await finalize_session(db, state.cipher, session, "exam_closed"):
            await _record(db, state, None, "session.auto_submitted", session, {"reason": "exam_closed"})
            await db.commit()
            await notify(state, session.exam_id, "submitted", session.id)
            state.tasks.spawn(grade_session(state, session.id))
        raise conflict("session_finished", "Your exam has ended.")
    if past_deadline(session, exam, utcnow()):
        await _time_up(db, state, session)
        raise conflict("time_over", "Time is up. Your saved work has been submitted.")


async def _question_in_session(db: AsyncSession, session: ExamSession, question_id: uuid.UUID) -> Question:
    assigned = await db.get(SessionQuestion, (session.id, question_id))
    question = await db.get(Question, question_id) if assigned else None
    if question is None:
        raise not_found()
    return question


async def _language_for(db: AsyncSession, question: Question, exam: Exam, key: str) -> Language:
    """The exam's language lock is enforced here, on the server: a modified app cannot
    submit Java in a Python-only exam."""
    for lang in await languages_for_question(db, question, exam):
        if lang.key == key:
            return lang
    raise bad_request("language_not_allowed", f"'{key}' is not allowed in this exam.")


def _check_code(state: AppState, code: str) -> None:
    if len(code.encode("utf-8")) > state.settings.max_code_bytes:
        raise bad_request("code_too_large", f"Code must be under {state.settings.max_code_bytes // 1024} KB.")


async def _save(db: AsyncSession, state: AppState, session: ExamSession, question: Question, language: str,
                code: str, base_revision: int | None = None) -> Answer:
    answer = await db.scalar(select(Answer).where(Answer.session_id == session.id,
                                                  Answer.question_id == question.id))
    if base_revision is not None and answer is not None and answer.revision != base_revision:
        raise conflict("stale_revision", "A newer version of this answer is saved. Reload it first.",
                       revision=answer.revision)
    if answer is None:
        answer = Answer(id=uuid.uuid4(), session_id=session.id, question_id=question.id, revision=0)
        db.add(answer)
    answer.language = language
    answer.code_enc = state.cipher.encrypt(code, AAD.answer(session.id, question.id))
    answer.revision += 1
    answer.updated_at = utcnow()
    return answer


async def _receipts(db: AsyncSession, session: ExamSession) -> list[Receipt]:
    answers = (await db.scalars(select(Answer).where(Answer.session_id == session.id, Answer.receipt.is_not(None)))).all()
    return [Receipt(question_id=a.question_id, receipt=a.receipt) for a in answers]


def _starters(question: Question, langs: list[Language]) -> dict[str, str]:
    """The professor's starter code, or the question type's default (e.g. an empty
    `def solve(nums, target):` for function questions)."""
    grader = get_grader(question.grader)
    try:
        config = grader.validate_config(question.grader_config or {})
    except ValueError:
        config = {}
    out = {}
    for lang in langs:
        code = (question.starter_code or {}).get(lang.key) or grader.default_starter(lang.key, config)
        if code:
            out[lang.key] = code
    return out


async def _view(db: AsyncSession, state: AppState, session: ExamSession, exam: Exam, device: Device) -> SessionView:
    now = utcnow()
    policy = effective_policy(exam.policy)
    on_pending_device = session.device_id != device.id
    questions: list[StudentQuestion] = []
    if session.status not in FINISHED and not on_pending_device:
        rows = (await db.execute(select(Question, SessionQuestion.ordinal)
                                 .join(SessionQuestion, SessionQuestion.question_id == Question.id)
                                 .where(SessionQuestion.session_id == session.id)
                                 .order_by(SessionQuestion.ordinal))).all()
        for q, ordinal in rows:
            langs = await languages_for_question(db, q, exam)
            samples = await load_tests(db, state.cipher, q.id, samples_only=True)
            hidden = await db.scalar(select(func.count()).select_from(TestCase).where(
                TestCase.question_id == q.id, TestCase.is_sample.is_(False)))
            answer = await db.scalar(select(Answer).where(Answer.session_id == session.id, Answer.question_id == q.id))
            questions.append(StudentQuestion(
                id=q.id, ordinal=ordinal, title=q.title, statement_md=q.statement_md, difficulty=q.difficulty.value,
                marks=q.marks,
                languages=[LanguageBrief(key=lang.key, name=lang.name, monaco_id=lang.monaco_id) for lang in langs],
                time_limits_ms={lang.key: time_limit_for(q, lang, state.settings) for lang in langs},
                starter_code=_starters(q, langs),
                samples=[SampleOut(ordinal=s.ordinal, input=s.input, expected_output=s.expected or "")
                         for s in samples],
                hidden_test_count=hidden or 0,
                draft=DraftOut(language=answer.language,
                               code=state.cipher.decrypt_str(answer.code_enc, AAD.answer(session.id, q.id)),
                               revision=answer.revision, updated_at=answer.updated_at) if answer else None,
            ))
    return SessionView(
        session_id=session.id, exam_id=exam.id, exam_title=exam.title, instructions_md=exam.instructions_md,
        status=session.status, lock_reason=session.lock_reason, strikes=session.strikes,
        max_strikes=policy.max_strikes, device_change_pending=session.pending_device_id is not None,
        server_now=now, started_at=session.started_at, deadline_at=session.deadline_at,
        seconds_left=_seconds_left(session, now), finish_reason=session.finish_reason, questions=questions,
        receipts=await _receipts(db, session) if session.status in FINISHED else [],
    )


async def _check_client(db: AsyncSession, state: AppState, device: Device, body: StartIn) -> None:
    if not state.settings.require_known_client:
        return
    if not body.client_version or not body.client_build_sha256:
        raise forbidden("Update UAI Aegis to the latest version.")
    release = await db.scalar(select(ClientRelease).where(
        ClientRelease.platform == device.platform, ClientRelease.version == body.client_version,
        ClientRelease.build_sha256 == body.client_build_sha256, ClientRelease.status == "allowed",
    ))
    if release is None:
        raise ApiError(403, "client_not_allowed", "This version of UAI Aegis is not allowed. Install the latest one.")


# ------------------------------------------------------------------------------ devices


@router.post("/devices", response_model=DeviceOut, status_code=201)
async def register_device(body: DeviceIn, state: AppState = Depends(get_state),
                          db: AsyncSession = Depends(get_db), user: User = Depends(require_student)):
    """Called once per laptop. The app proves it holds the private key by signing
    'AEGIS-REGISTER-V1\\n<user id>\\n<public key>'."""
    if not await state.limiter.hit(f"device-register:{user.id}", 10, 3600):
        raise too_many()
    try:
        raw_key = b64url_decode(body.public_key)
    except ValueError:
        raise bad_request("bad_public_key", "Public key is not valid base64url.") from None
    if len(raw_key) != 32:
        raise bad_request("bad_public_key", "Expected a 32-byte Ed25519 public key.")
    if not verify_signature(raw_key, registration_message(user.id, body.public_key), body.proof):
        raise bad_request("bad_proof", "Device proof signature is invalid.")
    existing = await db.scalar(select(Device).where(Device.public_key == raw_key))
    if existing is not None:
        if existing.user_id != user.id or existing.revoked_at is not None:
            raise conflict("key_in_use", "This device key cannot be registered.")
        return DeviceOut(device_id=existing.id)
    active = await db.scalar(select(func.count()).select_from(Device).where(
        Device.user_id == user.id, Device.revoked_at.is_(None)))
    if active >= MAX_DEVICES:
        raise conflict("too_many_devices", f"You already have {MAX_DEVICES} laptops registered. Ask an admin.")
    device = Device(id=uuid.uuid4(), user_id=user.id, public_key=raw_key, label=body.label.strip(),
                    platform=body.platform)
    db.add(device)
    await audit.record(db, state.settings.audit_key_bytes, actor=user, action="device.registered",
                       target_type="device", target_id=device.id, details={"platform": body.platform})
    await db.commit()
    return DeviceOut(device_id=device.id)


# -------------------------------------------------------------------------------- exams


@router.get("/exams", response_model=list[StudentExam])
async def my_exams(db: AsyncSession = Depends(get_db), user: User = Depends(require_student)):
    batch_ids = select(BatchMember.batch_id).where(BatchMember.user_id == user.id)
    subject_ids = select(SubjectBatch.subject_id).where(SubjectBatch.batch_id.in_(batch_ids))
    rows = (await db.execute(
        select(Exam, Subject).join(Subject, Subject.id == Exam.subject_id)
        .where(Exam.subject_id.in_(subject_ids), Exam.status != ExamStatus.draft)
        .order_by(Exam.opened_at.desc())
    )).all()
    sessions = {s.exam_id: s for s in (await db.scalars(
        select(ExamSession).where(ExamSession.student_id == user.id))).all()}
    out = []
    for exam, subject in rows:
        session = sessions.get(exam.id)
        if not await _is_enrolled(db, user, exam):
            continue
        if exam.status == ExamStatus.closed and session is None:
            continue
        join_closes = (exam.opened_at + timedelta(minutes=exam.join_window_minutes)
                       if exam.opened_at and exam.join_window_minutes else None)
        out.append(StudentExam(
            exam_id=exam.id, title=exam.title, subject_code=subject.code, subject_name=subject.name,
            status=exam.status, duration_minutes=exam.duration_minutes, opened_at=exam.opened_at,
            join_closes_at=join_closes, session_id=session.id if session else None,
            session_status=session.status if session else None,
            results_released=exam.results_released_at is not None,
        ))
    return out


@router.post("/exams/{exam_id}/start", response_model=SessionView)
async def start_exam(exam_id: uuid.UUID, body: StartIn, state: AppState = Depends(get_state),
                     db: AsyncSession = Depends(get_db), user: User = Depends(require_student),
                     device: Device = Depends(signed_device)):
    """Start the exam (needs the access code shown in the lab), or rejoin it after a
    crash. Rejoining from a different laptop locks the session until a professor
    approves the switch."""
    exam = await db.get(Exam, exam_id)
    if exam is None or not await _is_enrolled(db, user, exam):
        raise not_found()
    session = await db.scalar(select(ExamSession).where(ExamSession.exam_id == exam.id,
                                                        ExamSession.student_id == user.id).with_for_update())
    if session is not None:
        if session.status not in FINISHED and session.device_id != device.id \
                and session.pending_device_id != device.id:
            policy = effective_policy(exam.policy)
            if not policy.allow_device_change:
                raise conflict("device_mismatch", "This exam is open on another laptop.")
            event = apply_event(session, policy, kind="device_changed", source="server",
                                details={"new_device": str(device.id), "label": device.label})
            if session.status == SessionStatus.active:  # always needs approval, whatever the policy
                lock(session, "device_changed")
                event.caused_lock = True
            session.pending_device_id = device.id
            db.add(event)
            await _record(db, state, user, "session.device_change_requested", session,
                          {"device_id": str(device.id)})
            await db.commit()
            await notify(state, exam.id, "device_change", session.id)
        elif session.status not in FINISHED and past_deadline(session, exam, utcnow()):
            await _time_up(db, state, session)
        return await _view(db, state, session, exam, device)

    # New attempt
    if exam.status != ExamStatus.live:
        raise conflict("exam_not_open", "This exam is not open.")
    now = utcnow()
    if exam.join_window_minutes and now > exam.opened_at + timedelta(minutes=exam.join_window_minutes):
        raise conflict("join_window_closed", "The time to start this exam has passed. Talk to your professor.")
    if not await state.limiter.hit(f"access-code:{user.id}:{exam.id}", 10, 600):
        raise too_many("Too many wrong codes. Wait 10 minutes.")
    expected = state.cipher.decrypt_str(exam.access_code_enc, AAD.access_code(exam.id))
    given = (body.access_code or "").strip().upper()
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise forbidden("Wrong exam code.")
    await _check_client(db, state, device, body)

    pool = (await db.scalars(select(Question).join(ExamPoolQuestion, ExamPoolQuestion.question_id == Question.id)
                             .where(ExamPoolQuestion.exam_id == exam.id, Question.archived.is_(False)))).all()
    try:
        picked = pick_questions(list(pool), Selection.model_validate(exam.selection or {}), secrets.SystemRandom())
    except SelectionError:
        raise conflict("exam_misconfigured", "This exam's question pool is incomplete. Tell your professor.") from None
    session = ExamSession(id=uuid.uuid4(), exam_id=exam.id, student_id=user.id, started_at=now,
                          batch_id=await _exam_batch(db, user, exam),
                          deadline_at=now + timedelta(minutes=exam.duration_minutes), device_id=device.id,
                          client_version=body.client_version, status=SessionStatus.active)
    db.add(session)
    try:
        # Session row first (its question rows reference it). The unique (exam, student)
        # constraint makes a double-click or a second laptop racing the first fail here.
        await db.flush()
    except IntegrityError:
        await db.rollback()
        session = await db.scalar(select(ExamSession).where(ExamSession.exam_id == exam.id,
                                                            ExamSession.student_id == user.id))
        if session is None or session.device_id != device.id:
            raise conflict("device_mismatch", "This exam is open on another laptop.") from None
        return await _view(db, state, session, exam, device)
    for ordinal, qid in enumerate(picked, start=1):
        db.add(SessionQuestion(session_id=session.id, question_id=qid, ordinal=ordinal))
    await _record(db, state, user, "session.started", session, {"device_id": str(device.id)})
    await db.commit()
    await notify(state, exam.id, "started", session.id)
    return await _view(db, state, session, exam, device)


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(session_id: uuid.UUID, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), user: User = Depends(require_student),
                      device: Device = Depends(signed_device)):
    session, exam = await _session_for(db, user, device, session_id, allow_pending=True)
    if session.status not in FINISHED and past_deadline(session, exam, utcnow()):
        await _time_up(db, state, session)
    return await _view(db, state, session, exam, device)


@router.put("/sessions/{session_id}/answers/{question_id}", response_model=SaveOut)
async def save_answer(session_id: uuid.UUID, question_id: uuid.UUID, body: SaveIn,
                      state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                      user: User = Depends(require_student), device: Device = Depends(signed_device)):
    """Autosave. Call every few seconds while the student types."""
    if not await state.limiter.hit(f"autosave:{session_id}", 120, 60):
        raise too_many("Saving too often.")
    session, exam = await _session_for(db, user, device, session_id)
    await _ensure_writable(db, state, session, exam)
    question = await _question_in_session(db, session, question_id)
    await _language_for(db, question, exam, body.language)
    _check_code(state, body.code)
    answer = await _save(db, state, session, question, body.language, body.code, body.base_revision)
    await db.commit()
    return SaveOut(revision=answer.revision, saved_at=answer.updated_at)


async def _execute(state: AppState, db: AsyncSession, user: User, device: Device, session_id: uuid.UUID,
                   question_id: uuid.UUID, body: CodeIn, kind: RunKind) -> RunOut:
    session, exam = await _session_for(db, user, device, session_id)
    await _ensure_writable(db, state, session, exam)
    question = await _question_in_session(db, session, question_id)
    language = await _language_for(db, question, exam, body.language)
    _check_code(state, body.code)
    policy = effective_policy(exam.policy)
    if not await state.limiter.hit(f"run:{session.id}:{question.id}", policy.runs_per_minute, 60):
        raise too_many(f"You can run code {policy.runs_per_minute} times a minute per question.")
    tests = await load_tests(db, state.cipher, question.id, samples_only=kind == RunKind.run)
    if not tests:
        raise conflict("no_tests", "This question has no sample tests to run.")
    await _save(db, state, session, question, language.key, body.code)  # running also saves
    await db.commit()  # release the DB connection while the code runs

    result = await grade_code(state, question, language, body.code, tests)

    run_id = uuid.uuid4()
    db.add(Run(id=run_id, session_id=session.id, question_id=question.id, kind=kind, language=language.key,
               code_enc=state.cipher.encrypt(body.code, AAD.run(run_id)),
               status="error" if result.internal_error else "done", fraction=result.fraction,
               results=[t.to_dict() for t in result.tests]))
    await db.commit()

    reveal_hidden = kind == RunKind.run or policy.reveal_hidden_results
    specs = {t.id: t for t in tests}
    feedback = []
    for t in result.tests:
        if t.is_sample:
            spec = specs[t.test_id]
            feedback.append(TestFeedback(ordinal=t.ordinal, is_sample=True, verdict=t.verdict, time_ms=t.time_ms,
                                         input=spec.input, expected_output=spec.expected, stdout=t.stdout,
                                         stderr=t.stderr, message=t.message or None))
        elif reveal_hidden:
            feedback.append(TestFeedback(ordinal=t.ordinal, is_sample=False, verdict=t.verdict, time_ms=t.time_ms,
                                         message=t.message or None))
    hidden = [t for t in result.tests if not t.is_sample]
    summary = None
    if kind == RunKind.submit and hidden:
        summary = (f"Hidden tests: {sum(t.verdict == Verdict.accepted for t in hidden)}/{len(hidden)} passed."
                   if policy.reveal_hidden_results else "Submitted. Hidden tests are graded after the exam.")
    shown = [t for t in result.tests if t.is_sample or reveal_hidden]
    return RunOut(passed=sum(t.verdict == Verdict.accepted for t in shown), total=len(shown),
                  compile_output=result.compile_output, tests=feedback, hidden_summary=summary)


@router.post("/sessions/{session_id}/answers/{question_id}/run", response_model=RunOut)
async def run_samples(session_id: uuid.UUID, question_id: uuid.UUID, body: CodeIn,
                      state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                      user: User = Depends(require_student), device: Device = Depends(signed_device)):
    """Run against the visible sample tests."""
    return await _execute(state, db, user, device, session_id, question_id, body, RunKind.run)


@router.post("/sessions/{session_id}/answers/{question_id}/submit", response_model=RunOut)
async def submit_answer(session_id: uuid.UUID, question_id: uuid.UUID, body: CodeIn,
                        state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
                        user: User = Depends(require_student), device: Device = Depends(signed_device)):
    """Check against all tests. Final marks always use the latest saved code when the
    exam ends, so students can keep improving after submitting."""
    return await _execute(state, db, user, device, session_id, question_id, body, RunKind.submit)


@router.post("/sessions/{session_id}/heartbeat", response_model=StatusOut)
async def heartbeat(session_id: uuid.UUID, body: HeartbeatIn, state: AppState = Depends(get_state),
                    db: AsyncSession = Depends(get_db), user: User = Depends(require_student),
                    device: Device = Depends(signed_device)):
    """Every 10-15 s. Tells the monitor the student is connected and returns the
    authoritative status and time left (the app should show the server's countdown)."""
    session, exam = await _session_for(db, user, device, session_id, allow_pending=True)
    policy = effective_policy(exam.policy)
    now = utcnow()
    reconnected = False
    if session.device_id == device.id:
        gap = (now - session.last_heartbeat_at).total_seconds() if session.last_heartbeat_at else 0
        reconnected = session.status not in FINISHED and (
            session.last_heartbeat_at is None or gap > policy.heartbeat_timeout_seconds)
        if session.status == SessionStatus.active and gap > policy.heartbeat_timeout_seconds:
            db.add(apply_event(session, policy, kind="heartbeat_gap", source="server",
                               details={"seconds": int(gap)}))
        session.last_heartbeat_at = now
        if body.client_version:
            session.client_version = body.client_version
    device.last_seen_at = now
    if session.status not in FINISHED and past_deadline(session, exam, now):
        await _time_up(db, state, session)
    await db.commit()
    if reconnected:  # routine heartbeats are not pushed; only "this student is back"
        await notify(state, exam.id, "connected", session.id)
    return _status(session, policy, now)


@router.post("/sessions/{session_id}/events", response_model=StatusOut)
async def report_events(session_id: uuid.UUID, body: EventsIn, state: AppState = Depends(get_state),
                        db: AsyncSession = Depends(get_db), user: User = Depends(require_student),
                        device: Device = Depends(signed_device)):
    """The app reports anything suspicious it detects or blocks. Minor events add a
    strike; reaching the limit or any severe event locks the session at once."""
    session, exam = await _session_for(db, user, device, session_id, allow_pending=True)
    policy = effective_policy(exam.policy)
    was_active = session.status == SessionStatus.active
    strikes_before = session.strikes
    for item in body.events:
        if not EVENT_KIND.match(item.kind):
            raise bad_request("bad_event_kind", "Event kinds are lower_snake_case, max 48 characters.")
        if len(json.dumps(item.details, default=str)) > 2000:
            raise bad_request("event_too_large", "Event details are limited to 2 KB.")
        details = dict(item.details)
        if session.device_id != device.id:
            details["from_pending_device"] = True
        db.add(apply_event(session, policy, kind=item.kind, source="client", details=details,
                           client_ts=item.client_ts))
    locked_now = was_active and session.status == SessionStatus.locked
    if locked_now:
        await _record(db, state, None, "session.locked", session,
                      {"reason": session.lock_reason, "strikes": session.strikes})
    await db.commit()
    if body.events:
        change = "locked" if locked_now else "strike" if session.strikes != strikes_before else "event"
        await notify(state, exam.id, change, session.id)
    return _status(session, policy, utcnow())


@router.post("/sessions/{session_id}/finish", response_model=FinishOut)
async def finish_exam(session_id: uuid.UUID, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), user: User = Depends(require_student),
                      device: Device = Depends(signed_device)):
    """Student hands in. Their latest saved code for each question becomes final, and
    they get a receipt hash per answer as proof of what was submitted."""
    session, exam = await _session_for(db, user, device, session_id)
    if session.status in FINISHED:
        return FinishOut(status=session.status, receipts=await _receipts(db, session))
    await _ensure_writable(db, state, session, exam)
    await finalize_session(db, state.cipher, session, "submitted")
    await _record(db, state, user, "session.submitted", session)
    await db.commit()
    await notify(state, exam.id, "submitted", session.id)
    state.tasks.spawn(grade_session(state, session.id))
    return FinishOut(status=session.status, receipts=await _receipts(db, session))


@router.get("/results", response_model=list[StudentResult])
async def my_results(db: AsyncSession = Depends(get_db), user: User = Depends(require_student)):
    rows = (await db.execute(
        select(ExamSession, Exam).join(Exam, Exam.id == ExamSession.exam_id)
        .where(ExamSession.student_id == user.id, Exam.results_released_at.is_not(None),
               ExamSession.status.in_(FINISHED))
    )).all()
    out = []
    for session, exam in rows:
        (result,) = await compute_results(db, exam, [session])
        out.append(StudentResult(exam_id=exam.id, exam_title=exam.title, total=result.total,
                                 max_total=result.max_total,
                                 questions=[q.model_dump(mode="json") for q in result.questions]))
    return out
