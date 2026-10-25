"""Exam rules: question selection, time limits, finalising sessions and grading."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import secrets
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..grading import get_grader
from ..grading.registry import GradeResult, RunTarget, TestSpec
from ..models import (
    Answer,
    Difficulty,
    Exam,
    ExamSession,
    Language,
    Question,
    Run,
    RunKind,
    SessionStatus,
    SubjectLanguage,
    TestCase,
    utcnow,
)
from ..security.crypto import AAD, FieldCipher
from . import audit
from .proctoring import effective_policy

if TYPE_CHECKING:
    from ..config import Settings
    from ..state import AppState

log = logging.getLogger("aegis.exams")

DEFAULT_TIME_LIMIT_MS = 2000


# ----------------------------------------------------------------------- configuration


class SelectionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    difficulty: Difficulty | None = None
    tags: list[str] = Field(default_factory=list, max_length=10)
    count: int = Field(1, ge=1, le=50)


class Selection(BaseModel):
    """How each student's questions are picked from the exam pool.

    mode "random": for each rule, pick `count` random questions matching it (e.g. one
    easy, one medium, one hard). Every student gets the same difficulty mix but a
    different paper. mode "fixed": everyone gets the whole pool, in shuffled order.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["random", "fixed"] = "random"
    rules: list[SelectionRule] = Field(default_factory=list, max_length=20)


class Marking(BaseModel):
    """auto_percent of each question's marks come from tests; the rest are manual marks."""

    model_config = ConfigDict(extra="forbid")

    auto_percent: int = Field(100, ge=0, le=100)


class SelectionError(Exception):
    pass


def _matches(question: Question, rule: SelectionRule) -> bool:
    if rule.difficulty is not None and question.difficulty != rule.difficulty:
        return False
    return all(tag in (question.tags or []) for tag in rule.tags)


def pick_questions(pool: list[Question], selection: Selection, rng: random.Random) -> list[uuid.UUID]:
    if selection.mode == "fixed" or not selection.rules:
        ids = [q.id for q in pool]
        rng.shuffle(ids)
        return ids
    # Fill the most constrained rules first so overlapping rules are less likely to fail.
    rules = sorted(selection.rules, key=lambda r: sum(_matches(q, r) for q in pool))
    chosen: list[uuid.UUID] = []
    for rule in rules:
        candidates = [q.id for q in pool if q.id not in chosen and _matches(q, rule)]
        if len(candidates) < rule.count:
            what = rule.difficulty.value if rule.difficulty else "any difficulty"
            tags = f" tagged {', '.join(rule.tags)}" if rule.tags else ""
            raise SelectionError(
                f"The pool has {len(candidates)} unused {what} question(s){tags}; the rule needs {rule.count}."
            )
        chosen.extend(rng.sample(candidates, rule.count))
    rng.shuffle(chosen)
    return chosen


def check_selection(pool: list[Question], selection: Selection) -> str | None:
    rng = secrets.SystemRandom()
    try:
        for _ in range(30):
            pick_questions(pool, selection, rng)
    except SelectionError as exc:
        return str(exc)
    return None


# ------------------------------------------------------------------------------ limits


def time_limit_for(question: Question, language: Language, settings: Settings) -> int:
    base = question.time_limit_ms or question.auto_time_limit_ms or DEFAULT_TIME_LIMIT_MS
    return max(settings.min_time_limit_ms, min(int(base * language.time_multiplier), settings.max_time_limit_ms))


def memory_for(question: Question, settings: Settings) -> int:
    return question.memory_limit_kb or settings.default_memory_limit_kb


async def languages_for_question(db: AsyncSession, question: Question) -> list[Language]:
    if question.allowed_languages:
        langs = await db.scalars(
            select(Language).where(Language.key.in_(question.allowed_languages), Language.is_enabled)
        )
    else:
        langs = await db.scalars(
            select(Language)
            .join(SubjectLanguage, SubjectLanguage.language_id == Language.id)
            .where(SubjectLanguage.subject_id == question.subject_id, Language.is_enabled)
        )
    return sorted(langs.all(), key=lambda lang: lang.name)


async def load_tests(
    db: AsyncSession, cipher: FieldCipher, question_id: uuid.UUID, *, samples_only: bool = False
) -> list[TestSpec]:
    query = select(TestCase).where(TestCase.question_id == question_id).order_by(TestCase.ordinal)
    if samples_only:
        query = query.where(TestCase.is_sample)
    tests = (await db.scalars(query)).all()
    return [
        TestSpec(
            id=str(t.id),
            ordinal=t.ordinal,
            is_sample=t.is_sample,
            group=t.group_name,
            weight=t.weight,
            input=cipher.decrypt_str(t.input_enc, AAD.test_input(t.id)),
            expected=cipher.decrypt_str(t.expected_enc, AAD.test_expected(t.id)) if t.expected_enc else None,
        )
        for t in tests
    ]


async def grade_code(
    state: AppState, question: Question, language: Language, code: str, tests: list[TestSpec]
) -> GradeResult:
    grader = get_grader(question.grader)
    target = RunTarget(
        language_key=language.key,
        judge0_id=language.judge0_id,
        file_name=language.file_name,
        source=code,
        time_limit_ms=time_limit_for(question, language, state.settings),
        memory_limit_kb=memory_for(question, state.settings),
    )
    return await grader.grade(state.executor, target, tests, question.grader_config or {})


# ---------------------------------------------------------------------------- sessions


def receipt_hash(session_id: uuid.UUID, question_id: uuid.UUID, language: str, revision: int, code: str) -> str:
    """Submission receipt shown to the student. Bound to the session and question, so it
    proves what was submitted without revealing whether two students wrote the same code."""
    h = hashlib.sha256(f"AEGIS-RECEIPT-V1|{session_id}|{question_id}|{language}|{revision}|".encode())
    h.update(code.encode("utf-8"))
    return h.hexdigest()


def past_deadline(session: ExamSession, exam: Exam, now: datetime) -> bool:
    grace = effective_policy(exam.policy).grace_seconds
    return now > session.deadline_at + timedelta(seconds=grace)


async def finalize_session(
    db: AsyncSession, cipher: FieldCipher, session: ExamSession, reason: str, *, now: datetime | None = None
) -> bool:
    """Freeze a session: its latest saved code becomes the final answer. Returns False if
    it was already finished. Decrypting every answer here also verifies its integrity."""
    if session.status in (SessionStatus.submitted, SessionStatus.terminated):
        return False
    now = now or utcnow()
    if reason in ("time_up", "exam_closed") and session.status == SessionStatus.locked:
        reason = "locked_at_end"  # auto-submitted while waiting for a professor
        session.flagged = True
    session.status = SessionStatus.terminated if reason == "terminated" else SessionStatus.submitted
    session.finished_at = now
    session.finish_reason = reason
    session.grading_status = "pending"
    answers = await db.scalars(select(Answer).where(Answer.session_id == session.id))
    for answer in answers:
        code = cipher.decrypt_str(answer.code_enc, AAD.answer(session.id, answer.question_id))
        answer.is_final = True
        answer.receipt = receipt_hash(session.id, answer.question_id, answer.language, answer.revision, code)
    return True


async def grade_session(state: AppState, session_id: uuid.UUID) -> None:
    """Run every final answer against all its tests and store the auto marks.

    The database connection is released while code runs, so grading a whole lab after
    the exam closes does not exhaust the connection pool."""
    async with state.grading_slots:
        async with state.sessionmaker() as db:
            session = await db.get(ExamSession, session_id, with_for_update=True)
            if session is None or session.grading_status in ("running", "done"):
                return
            session.grading_status = "running"
            await db.commit()

        any_error = False
        try:
            # 1. Load everything needed (short transaction).
            jobs = []
            async with state.sessionmaker() as db:
                answers = (await db.scalars(
                    select(Answer).where(Answer.session_id == session_id, Answer.is_final)
                )).all()
                for answer in answers:
                    question = await db.get(Question, answer.question_id)
                    language = await db.scalar(select(Language).where(Language.key == answer.language))
                    if question is None or language is None:
                        any_error = True
                        continue
                    code = state.cipher.decrypt_str(answer.code_enc, AAD.answer(session_id, answer.question_id))
                    jobs.append((answer.id, question, language, code, await load_tests(db, state.cipher, question.id)))

            # 2. Run the code (no database connection held).
            results = [(answer_id, question, language, code, await grade_code(state, question, language, code, tests))
                       for answer_id, question, language, code, tests in jobs]

            # 3. Store marks and run history (short transaction).
            async with state.sessionmaker() as db:
                for answer_id, question, language, code, result in results:
                    run_id = uuid.uuid4()
                    db.add(Run(
                        id=run_id,
                        session_id=session_id,
                        question_id=question.id,
                        kind=RunKind.final,
                        language=language.key,
                        code_enc=state.cipher.encrypt(code, AAD.run(run_id)),
                        status="error" if result.internal_error else "done",
                        fraction=result.fraction,
                        results=[t.to_dict() for t in result.tests],
                    ))
                    answer = await db.get(Answer, answer_id)
                    answer.auto_fraction = result.fraction
                    answer.auto_details = {
                        "verdicts": dict(Counter(t.verdict for t in result.tests)),
                        "compile_output": result.compile_output,
                    }
                    any_error = any_error or result.internal_error
                session = await db.get(ExamSession, session_id)
                session.grading_status = "error" if any_error else "done"
                await db.commit()
        except Exception:  # keep background work alive; the professor can re-grade
            log.exception("grading failed for session %s", session_id)
            async with state.sessionmaker() as db:
                session = await db.get(ExamSession, session_id)
                if session is not None:
                    session.grading_status = "error"
                    await db.commit()


async def sweep_expired(state: AppState) -> int:
    """Auto-submit sessions whose time is up (including locked ones). Returns how many.

    Each session is finalised in its own short transaction, always locking the session
    row before the audit head (the same order as every other code path), so it cannot
    deadlock with students' requests."""
    now = utcnow()
    finished: list[uuid.UUID] = []
    async with state.sessionmaker() as db:
        rows = (await db.execute(
            select(ExamSession.id, Exam)
            .join(Exam, Exam.id == ExamSession.exam_id)
            .where(
                ExamSession.status.in_([SessionStatus.active, SessionStatus.locked]),
                ExamSession.deadline_at < now,
            )
        )).all()
    for session_id, exam in rows:
        async with state.sessionmaker() as db:
            session = await db.get(ExamSession, session_id, with_for_update=True)
            if session is None or not past_deadline(session, exam, now):
                continue
            if await finalize_session(db, state.cipher, session, "time_up", now=now):
                await audit.record(
                    db, state.settings.audit_key_bytes, actor=None, action="session.auto_submitted",
                    target_type="exam_session", target_id=session.id,
                    details={"exam_id": str(exam.id), "reason": session.finish_reason},
                )
                await db.commit()
                finished.append(session.id)
    for session_id in finished:
        state.tasks.spawn(grade_session(state, session_id))
    return len(finished)


async def sweeper_loop(state: AppState) -> None:
    while True:
        try:
            await sweep_expired(state)
        except Exception:  # pragma: no cover - logged and retried
            log.exception("sweeper iteration failed")
        await asyncio.sleep(state.settings.sweeper_interval_seconds)
