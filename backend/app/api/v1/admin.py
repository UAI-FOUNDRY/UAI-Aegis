"""Admin: departments, users (incl. CSV import), batches, languages, app releases, audit.

Admins manage the organisation but cannot see question pools or student answers.
Every change here is written to the tamper-evident audit log.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import bad_request, conflict, not_found
from ...models import (
    AuditEntry,
    AuthSession,
    Batch,
    BatchMember,
    ClientRelease,
    Department,
    Exam,
    Language,
    Question,
    Role,
    Subject,
    User,
    utcnow,
)
from ...security.passwords import hash_password, password_problem
from ...services import audit
from ...state import AppState
from ..deps import get_db, get_state, require_admin
from ..schemas import ORM, LanguageOut, Message, UserBrief, UserOut, normalise_email
from .catalog import BatchOut, DepartmentOut

router = APIRouter(prefix="/admin", tags=["admin"])

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5000

class AdminSubjectSummary(BaseModel):
    """Safe subject information for the admin dashboard."""

    id: uuid.UUID
    code: str
    name: str
    archived: bool
    question_count: int
    exam_count: int


@router.get(
    "/subject-summary",
    response_model=list[AdminSubjectSummary],
)
async def admin_subject_summary(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Return subject names and counts without exposing content."""

    subjects = (
        await db.scalars(
            select(Subject).order_by(Subject.code)
        )
    ).all()

    summaries = []

    for subject in subjects:
        question_count = await db.scalar(
            select(func.count())
            .select_from(Question)
            .where(
                Question.subject_id == subject.id,
                Question.archived.is_(False),
            )
        )

        exam_count = await db.scalar(
            select(func.count())
            .select_from(Exam)
            .where(Exam.subject_id == subject.id)
        )

        summaries.append(
            AdminSubjectSummary(
                id=subject.id,
                code=subject.code,
                name=subject.name,
                archived=subject.archived,
                question_count=question_count or 0,
                exam_count=exam_count or 0,
            )
        )

    return summaries


async def _record(db, state: AppState, admin: User, action: str, target_type: str, target_id, details=None):
    await audit.record(db, state.settings.audit_key_bytes, actor=admin, action=action,
                       target_type=target_type, target_id=target_id, details=details)


def _check_domain(state: AppState, email: str) -> None:
    domains = state.settings.email_domains
    if domains and email.split("@", 1)[1] not in domains:
        raise bad_request("email_domain", f"Only college email addresses are allowed ({', '.join(domains)}).")


async def _revoke_sessions(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


# --------------------------------------------------------------------------- departments


class DepartmentIn(BaseModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=200)


class DepartmentPatch(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str | None = Field(None, min_length=1, max_length=200)


@router.post("/departments", response_model=DepartmentOut, status_code=201)
async def create_department(body: DepartmentIn, state: AppState = Depends(get_state),
                            db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    code = body.code.upper()
    if await db.scalar(select(Department.id).where(Department.code == code)):
        raise conflict("duplicate", f"Department {code} already exists.")
    dept = Department(id=uuid.uuid4(), code=code, name=body.name.strip())
    db.add(dept)
    await _record(db, state, admin, "department.created", "department", dept.id, {"code": code})
    await db.commit()
    return dept


@router.patch("/departments/{department_id}", response_model=DepartmentOut)
async def update_department(department_id: uuid.UUID, body: DepartmentPatch, state: AppState = Depends(get_state),
                            db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    dept = await db.get(Department, department_id)
    if dept is None:
        raise not_found()
    changes = body.model_dump(exclude_unset=True)
    if "code" in changes:
        changes["code"] = changes["code"].upper()
        if await db.scalar(select(Department.id).where(Department.code == changes["code"], Department.id != dept.id)):
            raise conflict("duplicate", "Another department has that code.")
    for k, v in changes.items():
        setattr(dept, k, v)
    await _record(db, state, admin, "department.updated", "department", dept.id, changes)
    await db.commit()
    return dept


# --------------------------------------------------------------------------------- users


class UserCreate(BaseModel):
    email: str
    full_name: str = Field(min_length=1, max_length=200)
    role: Role
    college_id: str | None = Field(None, max_length=64)
    department_id: uuid.UUID | None = None
    temporary_password: str | None = Field(None, max_length=256)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalise_email(v)


class UserPatch(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    role: Role | None = None
    college_id: str | None = Field(None, max_length=64)
    department_id: uuid.UUID | None = None
    is_active: bool | None = None


class TemporaryPasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class UserList(BaseModel):
    total: int
    items: list[UserOut]


@router.get("/users", response_model=UserList, dependencies=[Depends(require_admin)])
async def list_users(
    role: Role | None = None,
    q: str = Query("", max_length=100),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(User)
    if role:
        query = query.where(User.role == role)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.where((User.email.like(like)) | (User.full_name.ilike(like)) | (User.college_id.ilike(like)))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    items = (await db.scalars(query.order_by(User.full_name).limit(limit).offset(offset))).all()
    return UserList(total=total or 0, items=[UserOut.model_validate(u) for u in items])


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    _check_domain(state, body.email)
    if await db.scalar(select(User.id).where(User.email == body.email)):
        raise conflict("duplicate", "A user with this email already exists.")
    if body.college_id and await db.scalar(select(User.id).where(User.college_id == body.college_id)):
        raise conflict("duplicate", "A user with this college ID already exists.")
    if body.department_id and await db.get(Department, body.department_id) is None:
        raise bad_request("unknown_department", "Department not found.")
    user = User(id=uuid.uuid4(), email=body.email, full_name=body.full_name.strip(), role=body.role,
                college_id=body.college_id, department_id=body.department_id)
    if body.temporary_password:
        problem = password_problem(body.temporary_password, email=body.email)
        if problem:
            raise bad_request("weak_password", problem)
        user.password_hash = hash_password(body.temporary_password)
        user.must_change_password = True
    db.add(user)
    await _record(db, state, admin, "user.created", "user", user.id,
                  {"email": user.email, "role": user.role.value})
    await db.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: uuid.UUID, body: UserPatch, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    user = await db.get(User, user_id)
    if user is None:
        raise not_found()
    changes = body.model_dump(exclude_unset=True)
    if user.id == admin.id and (changes.get("is_active") is False or changes.get("role", Role.admin) != Role.admin):
        raise bad_request("self_lockout", "You cannot deactivate or demote your own admin account.")
    if changes.get("college_id") and await db.scalar(
        select(User.id).where(User.college_id == changes["college_id"], User.id != user.id)
    ):
        raise conflict("duplicate", "Another user has this college ID.")
    for k, v in changes.items():
        setattr(user, k, v)
    if changes.get("is_active") is False or "role" in changes:
        await _revoke_sessions(db, user.id)  # role or status changes take effect immediately
    await _record(db, state, admin, "user.updated", "user", user.id,
                  {k: (v.value if hasattr(v, "value") else v) for k, v in changes.items()})
    await db.commit()
    return user


@router.post("/users/{user_id}/temporary-password", response_model=Message)
async def set_temporary_password(user_id: uuid.UUID, body: TemporaryPasswordIn, state: AppState = Depends(get_state),
                                 db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    user = await db.get(User, user_id)
    if user is None:
        raise not_found()
    problem = password_problem(body.password, email=user.email)
    if problem:
        raise bad_request("weak_password", problem)
    user.password_hash = hash_password(body.password)
    user.must_change_password = True
    await _revoke_sessions(db, user.id)
    await _record(db, state, admin, "user.temporary_password_set", "user", user.id)
    await db.commit()
    return Message(message="Temporary password set. The user must change it at next sign-in.")


class ImportProblem(BaseModel):
    line: int
    message: str


class ImportReport(BaseModel):
    dry_run: bool
    rows: int
    created: int
    updated: int
    batch_memberships_added: int
    errors: list[ImportProblem]


@router.post("/users/import", response_model=ImportReport)
async def import_users(
    file: UploadFile = File(..., description="CSV: email, full_name, role, college_id, department_code, batch_code"),
    dry_run: bool = Query(True, description="Check the file without saving anything."),
    state: AppState = Depends(get_state),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Bulk add or update students and professors. All-or-nothing: if any row has an
    error, nothing is saved and every problem is listed with its line number."""
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise bad_request("file_too_large", "CSV must be under 2 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise bad_request("bad_encoding", "Save the CSV as UTF-8.") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise bad_request("empty_file", "The CSV has no header row.")
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
    missing = {"email", "full_name"} - set(reader.fieldnames)
    if missing:
        raise bad_request("missing_columns", f"Missing column(s): {', '.join(sorted(missing))}.")

    departments = {d.code: d for d in (await db.scalars(select(Department))).all()}
    batches = {b.code: b for b in (await db.scalars(select(Batch))).all()}
    errors: list[ImportProblem] = []
    plans: list[tuple[int, dict]] = []
    seen_emails: set[str] = set()
    seen_ids: set[str] = set()
    for line, row in enumerate(reader, start=2):
        if line - 1 > MAX_IMPORT_ROWS:
            errors.append(ImportProblem(line=line, message=f"More than {MAX_IMPORT_ROWS} rows; split the file."))
            break
        row = {k: (v or "").strip() for k, v in row.items() if k}
        try:
            email = normalise_email(row.get("email", ""))
            _check_domain(state, email)
        except Exception as exc:  # noqa: BLE001 - reported per line
            msg = exc.detail["message"] if hasattr(exc, "detail") else "invalid email"
            errors.append(ImportProblem(line=line, message=f"{row.get('email') or '(blank)'}: {msg}"))
            continue
        if email in seen_emails:
            errors.append(ImportProblem(line=line, message=f"{email} appears twice in the file"))
            continue
        seen_emails.add(email)
        role_text = (row.get("role") or "student").lower()
        if role_text not in ("student", "professor"):
            errors.append(ImportProblem(line=line, message="role must be 'student' or 'professor'"))
            continue
        full_name = row.get("full_name", "")
        if not full_name or len(full_name) > 200:
            errors.append(ImportProblem(line=line, message="full_name is required (max 200 characters)"))
            continue
        college_id = row.get("college_id") or None
        if college_id:
            if college_id in seen_ids:
                errors.append(ImportProblem(line=line, message=f"college_id {college_id} appears twice"))
                continue
            seen_ids.add(college_id)
        dept_code = (row.get("department_code") or "").upper()
        if dept_code and dept_code not in departments:
            errors.append(ImportProblem(line=line, message=f"unknown department_code {dept_code}"))
            continue
        batch_code = row.get("batch_code") or ""
        if batch_code and batch_code not in batches:
            errors.append(ImportProblem(line=line, message=f"unknown batch_code {batch_code}"))
            continue
        plans.append((line, {"email": email, "full_name": full_name, "role": Role(role_text),
                             "college_id": college_id, "department": departments.get(dept_code),
                             "batch": batches.get(batch_code)}))

    existing = {u.email: u for u in (await db.scalars(
        select(User).where(User.email.in_([p["email"] for _, p in plans]))
    )).all()} if plans else {}
    for line, plan in plans:
        if plan["college_id"]:
            owner = await db.scalar(select(User.email).where(User.college_id == plan["college_id"]))
            if owner and owner != plan["email"]:
                errors.append(ImportProblem(line=line, message=f"college_id {plan['college_id']} belongs to {owner}"))
        user = existing.get(plan["email"])
        if user and user.role != plan["role"]:
            errors.append(ImportProblem(line=line, message=f"{plan['email']} is already a {user.role.value}; "
                                                          "change roles from the user page"))

    created = updated = memberships = 0
    if not errors:
        for _, plan in plans:
            user = existing.get(plan["email"])
            if user is None:
                user = User(id=uuid.uuid4(), email=plan["email"], full_name=plan["full_name"], role=plan["role"],
                            college_id=plan["college_id"],
                            department_id=plan["department"].id if plan["department"] else None)
                db.add(user)
                await db.flush()  # parent row first (no ORM relationships to order inserts)
                created += 1
            else:
                user.full_name = plan["full_name"]
                if plan["college_id"]:
                    user.college_id = plan["college_id"]
                if plan["department"]:
                    user.department_id = plan["department"].id
                updated += 1
            if plan["batch"] and plan["role"] == Role.student:
                if not await db.get(BatchMember, (plan["batch"].id, user.id)):
                    db.add(BatchMember(batch_id=plan["batch"].id, user_id=user.id))
                    memberships += 1
        if dry_run:
            await db.rollback()
        else:
            await _record(db, state, admin, "users.imported", "user", "bulk",
                          {"created": created, "updated": updated, "memberships": memberships})
            await db.commit()
    return ImportReport(dry_run=dry_run, rows=len(plans) + len(errors), created=created, updated=updated,
                        batch_memberships_added=memberships, errors=errors)


# ------------------------------------------------------------------------------- batches


class BatchIn(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=200)
    department_id: uuid.UUID | None = None


class BatchPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    department_id: uuid.UUID | None = None


class MembersIn(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=1000)


@router.post("/batches", response_model=BatchOut, status_code=201)
async def create_batch(body: BatchIn, state: AppState = Depends(get_state),
                       db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    if await db.scalar(select(Batch.id).where(Batch.code == body.code)):
        raise conflict("duplicate", f"Batch {body.code} already exists.")
    batch = Batch(id=uuid.uuid4(), code=body.code, name=body.name.strip(), department_id=body.department_id)
    db.add(batch)
    await _record(db, state, admin, "batch.created", "batch", batch.id, {"code": batch.code})
    await db.commit()
    return batch


@router.patch("/batches/{batch_id}", response_model=BatchOut)
async def update_batch(batch_id: uuid.UUID, body: BatchPatch, state: AppState = Depends(get_state),
                       db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    batch = await db.get(Batch, batch_id)
    if batch is None:
        raise not_found()
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(batch, k, v)
    await _record(db, state, admin, "batch.updated", "batch", batch.id, changes)
    await db.commit()
    return batch


@router.get("/batches/{batch_id}/members", response_model=list[UserBrief], dependencies=[Depends(require_admin)])
async def batch_members(batch_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return (await db.scalars(
        select(User).join(BatchMember, BatchMember.user_id == User.id)
        .where(BatchMember.batch_id == batch_id).order_by(User.full_name)
    )).all()


@router.post("/batches/{batch_id}/members", response_model=Message)
async def add_batch_members(batch_id: uuid.UUID, body: MembersIn, state: AppState = Depends(get_state),
                            db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    if await db.get(Batch, batch_id) is None:
        raise not_found()
    emails = {normalise_email(e) for e in body.emails}
    students = (await db.scalars(select(User).where(User.email.in_(emails), User.role == Role.student))).all()
    unknown = emails - {s.email for s in students}
    if unknown:
        raise bad_request("unknown_students", "Not found or not students: " + ", ".join(sorted(unknown)[:20]))
    added = 0
    for s in students:
        if not await db.get(BatchMember, (batch_id, s.id)):
            db.add(BatchMember(batch_id=batch_id, user_id=s.id))
            added += 1
    await _record(db, state, admin, "batch.members_added", "batch", batch_id, {"added": added})
    await db.commit()
    return Message(message=f"Added {added} student(s).")


@router.delete("/batches/{batch_id}/members/{user_id}", response_model=Message)
async def remove_batch_member(batch_id: uuid.UUID, user_id: uuid.UUID, state: AppState = Depends(get_state),
                              db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    result = await db.execute(delete(BatchMember).where(BatchMember.batch_id == batch_id, BatchMember.user_id == user_id))
    if result.rowcount == 0:
        raise not_found()
    await _record(db, state, admin, "batch.member_removed", "batch", batch_id, {"user_id": str(user_id)})
    await db.commit()
    return Message(message="Removed.")


# ----------------------------------------------------------------------------- languages


class LanguageIn(BaseModel):
    key: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_+.-]+$")
    name: str = Field(min_length=1, max_length=100)
    judge0_id: int = Field(ge=1)
    file_name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    monaco_id: str = Field(min_length=1, max_length=32)
    time_multiplier: float = Field(1.0, ge=0.5, le=10)
    is_enabled: bool = True


class LanguagePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    judge0_id: int | None = Field(None, ge=1)
    file_name: str | None = Field(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    monaco_id: str | None = Field(None, min_length=1, max_length=32)
    time_multiplier: float | None = Field(None, ge=0.5, le=10)
    is_enabled: bool | None = None


@router.get("/languages", response_model=list[LanguageOut], dependencies=[Depends(require_admin)])
async def list_languages(db: AsyncSession = Depends(get_db)):
    return (await db.scalars(select(Language).order_by(Language.key))).all()


@router.post("/languages", response_model=LanguageOut, status_code=201)
async def create_language(body: LanguageIn, state: AppState = Depends(get_state),
                          db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    if await db.scalar(select(Language.id).where(Language.key == body.key)):
        raise conflict("duplicate", f"Language {body.key} already exists.")
    lang = Language(id=uuid.uuid4(), **body.model_dump())
    db.add(lang)
    await _record(db, state, admin, "language.created", "language", lang.id, body.model_dump())
    await db.commit()
    return lang


@router.patch("/languages/{language_id}", response_model=LanguageOut)
async def update_language(language_id: uuid.UUID, body: LanguagePatch, state: AppState = Depends(get_state),
                          db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    lang = await db.get(Language, language_id)
    if lang is None:
        raise not_found()
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(lang, k, v)
    await _record(db, state, admin, "language.updated", "language", lang.id, changes)
    await db.commit()
    return lang


# -------------------------------------------------------------------- student app builds


class ReleaseIn(BaseModel):
    version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z.+-]+$")
    platform: str = Field(pattern=r"^(windows|macos|linux)$")
    build_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    notes: str | None = Field(None, max_length=500)


class ReleaseOut(ORM):
    id: uuid.UUID
    version: str
    platform: str
    build_sha256: str
    status: str
    notes: str | None
    created_at: datetime


class ReleaseStatusIn(BaseModel):
    status: str = Field(pattern=r"^(allowed|revoked)$")


@router.get("/client-releases", response_model=list[ReleaseOut], dependencies=[Depends(require_admin)])
async def list_releases(db: AsyncSession = Depends(get_db)):
    return (await db.scalars(select(ClientRelease).order_by(ClientRelease.created_at.desc()))).all()


@router.post("/client-releases", response_model=ReleaseOut, status_code=201)
async def add_release(body: ReleaseIn, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    release = ClientRelease(id=uuid.uuid4(), **body.model_dump())
    db.add(release)
    await _record(db, state, admin, "client_release.added", "client_release", release.id, body.model_dump())
    await db.commit()
    return release


@router.patch("/client-releases/{release_id}", response_model=ReleaseOut)
async def set_release_status(release_id: uuid.UUID, body: ReleaseStatusIn, state: AppState = Depends(get_state),
                             db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    release = await db.get(ClientRelease, release_id)
    if release is None:
        raise not_found()
    release.status = body.status
    await _record(db, state, admin, "client_release.status", "client_release", release.id, {"status": body.status})
    await db.commit()
    return release


# --------------------------------------------------------------------------------- audit


class AuditOut(ORM):
    seq: int
    at: datetime
    actor_label: str
    action: str
    target_type: str
    target_id: str
    details: dict


@router.get("/audit", response_model=list[AuditOut], dependencies=[Depends(require_admin)])
async def list_audit(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                     db: AsyncSession = Depends(get_db)):
    return (await db.scalars(select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(limit).offset(offset))).all()


@router.get("/audit/verify", dependencies=[Depends(require_admin)])
async def verify_audit(state: AppState = Depends(get_state), db: AsyncSession = Depends(get_db)) -> dict:
    """Recompute the whole hash chain. ok=false means a past entry was changed or deleted."""
    return await audit.verify_chain(db, state.settings.audit_key_bytes)
