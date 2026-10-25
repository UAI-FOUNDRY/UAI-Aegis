"""Professor home page: every subject, its exams, and per batch who has taken them.

One call answers "which batch sat which exam, how many have submitted, what still needs
checking, and in which languages did they answer", across every subject and language the
professor teaches.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    AccessGrant,
    Answer,
    Batch,
    BatchMember,
    Exam,
    ExamSession,
    ExamStatus,
    GrantRole,
    Language,
    SessionStatus,
    Subject,
    SubjectBatch,
    SubjectLanguage,
    User,
)
from ..deps import get_db, require_professor

router = APIRouter(tags=["dashboard"])

_RANK = {GrantRole.owner: 4, GrantRole.editor: 3, GrantRole.grader: 2, GrantRole.viewer: 1}


class BatchProgress(BaseModel):
    batch_id: uuid.UUID
    code: str
    name: str
    enrolled: int
    started: int
    submitted: int  # finished in any way (submitted, time up, ended by a professor)
    locked: int
    flagged: int
    grading_pending: int


class ExamSummary(BaseModel):
    id: uuid.UUID
    title: str
    status: ExamStatus
    languages: list[str] | None  # the exam's language lock, if any
    languages_used: dict[str, int]  # language -> number of students who answered in it
    opened_at: datetime | None
    closed_at: datetime | None
    results_released: bool
    batches: list[BatchProgress]


class SubjectSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    my_role: GrantRole
    languages: list[str]
    exams: list[ExamSummary]


@router.get("/dashboard", response_model=list[SubjectSummary])
async def dashboard(db: AsyncSession = Depends(get_db), user: User = Depends(require_professor)):
    grants = (await db.scalars(select(AccessGrant).where(AccessGrant.user_id == user.id))).all()
    by_subject: dict[uuid.UUID, list[AccessGrant]] = {}
    for grant in grants:
        by_subject.setdefault(grant.subject_id, []).append(grant)

    out = []
    for subject_id, subject_grants in by_subject.items():
        subject = await db.get(Subject, subject_id)
        if subject is None or subject.archived:
            continue
        languages = sorted((await db.scalars(
            select(Language.key).join(SubjectLanguage, SubjectLanguage.language_id == Language.id)
            .where(SubjectLanguage.subject_id == subject.id)
        )).all())
        query = select(Exam).where(Exam.subject_id == subject.id).order_by(Exam.created_at.desc())
        if not any(g.exam_id is None for g in subject_grants):  # only some exams were shared
            query = query.where(Exam.id.in_([g.exam_id for g in subject_grants]))
        subject_batches = (await db.scalars(
            select(Batch).join(SubjectBatch, SubjectBatch.batch_id == Batch.id)
            .where(SubjectBatch.subject_id == subject.id).order_by(Batch.code)
        )).all()
        exams = [await _exam_summary(db, exam, subject_batches) for exam in (await db.scalars(query)).all()]
        out.append(SubjectSummary(
            id=subject.id, code=subject.code, name=subject.name,
            my_role=max((g.role for g in subject_grants), key=_RANK.__getitem__),
            languages=languages, exams=exams,
        ))
    return sorted(out, key=lambda s: s.code)


async def _exam_summary(db: AsyncSession, exam: Exam, subject_batches: list[Batch]) -> ExamSummary:
    wanted = {str(b) for b in exam.batch_ids} if exam.batch_ids else None
    batches = [b for b in subject_batches if wanted is None or str(b.id) in wanted]
    sessions = (await db.scalars(select(ExamSession).where(ExamSession.exam_id == exam.id))).all()
    finished = (SessionStatus.submitted, SessionStatus.terminated)
    progress = []
    for batch in batches:
        enrolled = await db.scalar(select(func.count()).select_from(BatchMember).where(BatchMember.batch_id == batch.id))
        mine = [s for s in sessions if s.batch_id == batch.id]
        progress.append(BatchProgress(
            batch_id=batch.id, code=batch.code, name=batch.name, enrolled=enrolled or 0, started=len(mine),
            submitted=sum(s.status in finished for s in mine),
            locked=sum(s.status == SessionStatus.locked for s in mine),
            flagged=sum(s.flagged for s in mine),
            grading_pending=sum(s.status in finished and s.grading_status != "done" for s in mine),
        ))
    used = Counter()
    if sessions:
        rows = (await db.execute(
            select(Answer.language, func.count(func.distinct(Answer.session_id)))
            .where(Answer.session_id.in_([s.id for s in sessions])).group_by(Answer.language)
        )).all()
        used.update(dict(rows))
    return ExamSummary(
        id=exam.id, title=exam.title, status=exam.status, languages=exam.languages, languages_used=dict(used),
        opened_at=exam.opened_at, closed_at=exam.closed_at, results_released=exam.results_released_at is not None,
        batches=progress,
    )
