"""Question pools and test cases. Hidden test data and reference solutions are
encrypted at rest (AES-256-GCM), bound to their own row IDs."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedMixin, IdMixin, UTCDateTime, utcnow


class Difficulty(StrEnum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class Question(IdMixin, CreatedMixin, Base):
    __tablename__ = "questions"

    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    statement_md: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[Difficulty] = mapped_column(Enum(Difficulty, native_enum=False, length=16))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Which grader plug-in checks answers ("stdio" for input -> output programs).
    grader: Mapped[str] = mapped_column(String(32), default="stdio")
    grader_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    marks: Mapped[int] = mapped_column(Integer, default=10)
    # Base time limit (fastest-language equivalent). NULL = use auto_time_limit_ms,
    # which is measured from the reference solution.
    time_limit_ms: Mapped[int | None] = mapped_column(Integer)
    auto_time_limit_ms: Mapped[int | None] = mapped_column(Integer)
    memory_limit_kb: Mapped[int | None] = mapped_column(Integer)
    starter_code: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)  # language key -> code
    allowed_languages: Mapped[list[str] | None] = mapped_column(JSON)  # NULL = subject default
    reference_language: Mapped[str | None] = mapped_column(String(32))
    reference_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class TestCase(IdMixin, CreatedMixin, Base):
    __tablename__ = "test_cases"
    __test__ = False  # tell pytest this is not a test class

    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    group_name: Mapped[str] = mapped_column(String(64), default="main")
    weight: Mapped[int] = mapped_column(Integer, default=1)
    input_enc: Mapped[bytes] = mapped_column(LargeBinary)
    # NULL until generated from the reference solution (or typed by the professor).
    expected_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
