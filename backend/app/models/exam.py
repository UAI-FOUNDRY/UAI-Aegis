"""Exams, per-student sessions, answers, runs and proctoring events."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedMixin, IdMixin, UTCDateTime, utcnow


class ExamStatus(StrEnum):
    draft = "draft"
    live = "live"
    closed = "closed"


class SessionStatus(StrEnum):
    active = "active"  # student is working
    locked = "locked"  # frozen until a professor resumes or ends it
    submitted = "submitted"  # finished normally, by time-up, or auto-submitted while locked
    terminated = "terminated"  # ended by a professor


class Severity(StrEnum):
    info = "info"  # recorded only
    minor = "minor"  # counts as a strike
    severe = "severe"  # locks immediately (if the exam policy says so)


class RunKind(StrEnum):
    run = "run"  # sample tests, student feedback
    submit = "submit"  # all tests, student-triggered
    final = "final"  # all tests on the final answer, used for marks


class Exam(IdMixin, CreatedMixin, Base):
    __tablename__ = "exams"

    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    instructions_md: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer)
    join_window_minutes: Mapped[int] = mapped_column(Integer, default=15)
    status: Mapped[ExamStatus] = mapped_column(
        Enum(ExamStatus, native_enum=False, length=16), default=ExamStatus.draft
    )
    selection: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # question picking rules
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # proctoring policy
    marking: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # auto/manual split
    batch_ids: Mapped[list[str] | None] = mapped_column(JSON)  # NULL = all batches of the subject
    access_code_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    results_released_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ExamPoolQuestion(Base):
    __tablename__ = "exam_pool"

    exam_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )


class ExamSession(IdMixin, CreatedMixin, Base):
    """One student's attempt at one exam. The unique constraint makes it impossible
    for two students to share a session or for one student to hold two."""

    __tablename__ = "exam_sessions"
    __table_args__ = (UniqueConstraint("exam_id", "student_id"),)

    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"))
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=False, length=16), default=SessionStatus.active
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    deadline_at: Mapped[datetime] = mapped_column(UTCDateTime())  # server-side timer
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"))
    pending_device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id"))
    strikes: Mapped[int] = mapped_column(Integer, default=0)
    lock_reason: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)  # for professor review
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    client_version: Mapped[str | None] = mapped_column(String(32))
    grading_status: Mapped[str] = mapped_column(String(16), default="pending")


class SessionQuestion(Base):
    """Which questions a session was given (randomly picked at start), in order."""

    __tablename__ = "session_questions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)


class Answer(IdMixin, CreatedMixin, Base):
    """The latest code for one question in one session (autosaved, then finalised).

    code_enc is AES-256-GCM encrypted with the session and question IDs as associated
    data: if two students' ciphertexts were ever swapped in the database, decryption
    fails instead of showing one student's code as another's.
    """

    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("session_id", "question_id"),)

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_sessions.id", ondelete="CASCADE"))
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    language: Mapped[str] = mapped_column(String(32))
    code_enc: Mapped[bytes] = mapped_column(LargeBinary)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    receipt: Mapped[str | None] = mapped_column(String(64))  # SHA-256 given to the student
    auto_fraction: Mapped[float | None] = mapped_column(Float)  # 0..1 from tests
    auto_details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    manual_marks: Mapped[float | None] = mapped_column(Float)
    override_marks: Mapped[float | None] = mapped_column(Float)  # replaces the computed total
    manual_note: Mapped[str | None] = mapped_column(Text)
    graded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    graded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Run(IdMixin, CreatedMixin, Base):
    """Every Run / Submit / final grading, kept as run history for the professor."""

    __tablename__ = "runs"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_sessions.id", ondelete="CASCADE"))
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    kind: Mapped[RunKind] = mapped_column(Enum(RunKind, native_enum=False, length=16))
    language: Mapped[str] = mapped_column(String(32))
    code_enc: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(16), default="done")  # done | error
    fraction: Mapped[float | None] = mapped_column(Float)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class ProctorEvent(IdMixin, CreatedMixin, Base):
    __tablename__ = "proctor_events"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_sessions.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(48))
    severity: Mapped[Severity] = mapped_column(Enum(Severity, native_enum=False, length=16))
    source: Mapped[str] = mapped_column(String(16))  # client | server | staff
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    client_ts: Mapped[datetime | None] = mapped_column(UTCDateTime())
    caused_lock: Mapped[bool] = mapped_column(Boolean, default=False)
