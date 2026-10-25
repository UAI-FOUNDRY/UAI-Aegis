"""People and organisation: departments, users, batches, languages, subjects, access grants.

All of these are plain rows, so adding a department, lab, subject, professor, batch or
programming language never needs a code change or redeploy.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedMixin, IdMixin


class Role(StrEnum):
    admin = "admin"
    professor = "professor"
    student = "student"


class GrantRole(StrEnum):
    owner = "owner"  # everything, including sharing
    editor = "editor"  # edit questions/exams, grade, proctor
    grader = "grader"  # view answers, grade, proctor
    viewer = "viewer"  # view answers and results only


class Department(IdMixin, CreatedMixin, Base):
    __tablename__ = "departments"

    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))


class User(IdMixin, CreatedMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    college_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False, length=16))
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )
    # Argon2id hash. NULL for accounts that only sign in through college SSO.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    # Users are deactivated, never deleted, so audit history stays intact.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Batch(IdMixin, CreatedMixin, Base):
    """A group of students, e.g. 'B.Tech CSE (AI & ML) 2025, Division A'."""

    __tablename__ = "batches"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )


class BatchMember(Base):
    __tablename__ = "batch_members"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )


class Language(IdMixin, CreatedMixin, Base):
    """A programming language the judge can run. Adding one = adding a row."""

    __tablename__ = "languages"

    key: Mapped[str] = mapped_column(String(32), unique=True)  # e.g. "python3", "cpp17"
    name: Mapped[str] = mapped_column(String(100))  # shown to students
    judge0_id: Mapped[int] = mapped_column(Integer)  # from GET /languages on the Judge0 server
    file_name: Mapped[str] = mapped_column(String(64))  # e.g. "main.py", "Main.java"
    monaco_id: Mapped[str] = mapped_column(String(32))  # editor syntax mode, e.g. "python"
    time_multiplier: Mapped[float] = mapped_column(Float, default=1.0)  # slower languages get more time
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Subject(IdMixin, CreatedMixin, Base):
    __tablename__ = "subjects"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )
    is_lab: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class SubjectBatch(Base):
    __tablename__ = "subject_batches"

    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True
    )


class SubjectLanguage(Base):
    __tablename__ = "subject_languages"

    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True
    )
    language_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("languages.id", ondelete="CASCADE"), primary_key=True
    )


class AccessGrant(IdMixin, CreatedMixin, Base):
    """Who may see or change a subject's content.

    exam_id NULL  -> the grant covers the whole subject.
    exam_id set   -> the grant covers only that exam (e.g. a co-evaluator for one practical).
    Only professors can hold grants; admins manage grants but never see content.
    """

    __tablename__ = "access_grants"

    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    exam_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("exams.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[GrantRole] = mapped_column(Enum(GrantRole, native_enum=False, length=16))
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
