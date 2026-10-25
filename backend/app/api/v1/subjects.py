"""Subjects (lab courses), their languages and batches, and who can access them."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import bad_request, conflict, not_found
from ...models import (
    AccessGrant,
    Batch,
    Department,
    Exam,
    GrantRole,
    Language,
    Role,
    Subject,
    SubjectBatch,
    SubjectLanguage,
    User,
)
from ...services import audit
from ...services.authz import Cap, require_cap
from ...state import AppState
from ..deps import get_db, get_state, require_staff
from ..schemas import ORM, UserBrief, normalise_email

router = APIRouter(prefix="/subjects", tags=["subjects"])

_ROLE_RANK = {GrantRole.owner: 4, GrantRole.editor: 3, GrantRole.grader: 2, GrantRole.viewer: 1}


class SubjectIn(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=200)
    department_id: uuid.UUID | None = None
    is_lab: bool = True
    language_keys: list[str] = Field(default_factory=list, max_length=50)
    batch_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    owner_id: uuid.UUID | None = Field(None, description="Admins only: the professor who will own it.")


class SubjectPatch(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str | None = Field(None, min_length=1, max_length=200)
    department_id: uuid.UUID | None = None
    is_lab: bool | None = None
    archived: bool | None = None


class SubjectOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    department_id: uuid.UUID | None
    is_lab: bool
    archived: bool
    created_at: datetime
    my_role: GrantRole | None = None
    language_keys: list[str] = []
    batch_ids: list[uuid.UUID] = []


class KeysIn(BaseModel):
    keys: list[str] = Field(max_length=50)


class BatchIdsIn(BaseModel):
    batch_ids: list[uuid.UUID] = Field(max_length=200)


class GrantIn(BaseModel):
    email: str
    role: GrantRole
    exam_id: uuid.UUID | None = Field(None, description="Limit the grant to one exam (e.g. a co-evaluator).")

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalise_email(v)


class GrantOut(ORM):
    id: uuid.UUID
    role: GrantRole
    exam_id: uuid.UUID | None
    user: UserBrief
    created_at: datetime


async def _record(db, state: AppState, actor: User, action: str, target_id, details=None):
    await audit.record(db, state.settings.audit_key_bytes, actor=actor, action=action,
                       target_type="subject", target_id=target_id, details=details)


async def _my_role(db: AsyncSession, user: User, subject_id: uuid.UUID) -> GrantRole | None:
    roles = (await db.scalars(
        select(AccessGrant.role).where(AccessGrant.subject_id == subject_id, AccessGrant.user_id == user.id)
    )).all()
    return max(roles, key=_ROLE_RANK.__getitem__) if roles else None


async def _load(db: AsyncSession, user: User, subject_id: uuid.UUID, cap: Cap | None,
                admin_ok: bool) -> Subject:
    """Load a subject the user may act on. cap=None means 'any grant at all'."""
    subject = await db.get(Subject, subject_id)
    if subject is None:
        raise not_found()
    if user.role == Role.admin and admin_ok:
        return subject
    if cap is None:
        if await _my_role(db, user, subject.id) is None:
            raise not_found()
        return subject
    await require_cap(db, user, subject.id, cap)
    return subject


async def _out(db: AsyncSession, user: User, subject: Subject) -> SubjectOut:
    keys = (await db.scalars(
        select(Language.key).join(SubjectLanguage, SubjectLanguage.language_id == Language.id)
        .where(SubjectLanguage.subject_id == subject.id).order_by(Language.key)
    )).all()
    batch_ids = (await db.scalars(select(SubjectBatch.batch_id).where(SubjectBatch.subject_id == subject.id))).all()
    out = SubjectOut.model_validate(subject)
    out.my_role = await _my_role(db, user, subject.id)
    out.language_keys = list(keys)
    out.batch_ids = list(batch_ids)
    return out


async def _set_languages(db: AsyncSession, subject_id: uuid.UUID, keys: list[str]) -> None:
    langs = (await db.scalars(select(Language).where(Language.key.in_(keys)))).all() if keys else []
    unknown = set(keys) - {lang.key for lang in langs}
    if unknown:
        raise bad_request("unknown_language", f"Unknown language(s): {', '.join(sorted(unknown))}")
    await db.execute(delete(SubjectLanguage).where(SubjectLanguage.subject_id == subject_id))
    for lang in langs:
        db.add(SubjectLanguage(subject_id=subject_id, language_id=lang.id))


async def _set_batches(db: AsyncSession, subject_id: uuid.UUID, batch_ids: list[uuid.UUID]) -> None:
    found = set((await db.scalars(select(Batch.id).where(Batch.id.in_(batch_ids)))).all()) if batch_ids else set()
    if set(batch_ids) - found:
        raise bad_request("unknown_batch", "One or more batches do not exist.")
    await db.execute(delete(SubjectBatch).where(SubjectBatch.subject_id == subject_id))
    for batch_id in found:
        db.add(SubjectBatch(subject_id=subject_id, batch_id=batch_id))


@router.get("", response_model=list[SubjectOut])
async def list_subjects(db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    if user.role == Role.admin:
        subjects = (await db.scalars(select(Subject).order_by(Subject.code))).all()
    else:
        subjects = (await db.scalars(
            select(Subject).where(Subject.id.in_(
                select(AccessGrant.subject_id).where(AccessGrant.user_id == user.id)
            )).order_by(Subject.code)
        )).all()
    return [await _out(db, user, s) for s in subjects]


@router.post("", response_model=SubjectOut, status_code=201)
async def create_subject(body: SubjectIn, state: AppState = Depends(get_state),
                         db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    if user.role == Role.admin:
        owner = await db.get(User, body.owner_id) if body.owner_id else None
        if owner is None or owner.role != Role.professor or not owner.is_active:
            raise bad_request("owner_required", "Choose an active professor as the owner.")
    else:
        owner = user
    if await db.scalar(select(Subject.id).where(Subject.code == body.code)):
        raise conflict("duplicate", f"Subject {body.code} already exists.")
    if body.department_id and await db.get(Department, body.department_id) is None:
        raise bad_request("unknown_department", "Department not found.")
    subject = Subject(id=uuid.uuid4(), code=body.code, name=body.name.strip(), department_id=body.department_id,
                      is_lab=body.is_lab, created_by=user.id)
    db.add(subject)
    await db.flush()
    await _set_languages(db, subject.id, body.language_keys)
    await _set_batches(db, subject.id, body.batch_ids)
    db.add(AccessGrant(subject_id=subject.id, user_id=owner.id, role=GrantRole.owner, granted_by=user.id))
    await _record(db, state, user, "subject.created", subject.id, {"code": subject.code, "owner": owner.email})
    await db.commit()
    return await _out(db, user, subject)


@router.get("/{subject_id}", response_model=SubjectOut)
async def get_subject(subject_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    subject = await _load(db, user, subject_id, None, admin_ok=True)
    return await _out(db, user, subject)


@router.patch("/{subject_id}", response_model=SubjectOut)
async def update_subject(subject_id: uuid.UUID, body: SubjectPatch, state: AppState = Depends(get_state),
                         db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    subject = await _load(db, user, subject_id, Cap.edit, admin_ok=True)
    changes = body.model_dump(exclude_unset=True)
    if "code" in changes and await db.scalar(
        select(Subject.id).where(Subject.code == changes["code"], Subject.id != subject.id)
    ):
        raise conflict("duplicate", "Another subject has that code.")
    for k, v in changes.items():
        setattr(subject, k, v)
    await _record(db, state, user, "subject.updated", subject.id, changes)
    await db.commit()
    return await _out(db, user, subject)


@router.put("/{subject_id}/languages", response_model=SubjectOut)
async def set_languages(subject_id: uuid.UUID, body: KeysIn, state: AppState = Depends(get_state),
                        db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    subject = await _load(db, user, subject_id, Cap.edit, admin_ok=True)
    await _set_languages(db, subject.id, body.keys)
    await _record(db, state, user, "subject.languages_set", subject.id, {"keys": sorted(body.keys)})
    await db.commit()
    return await _out(db, user, subject)


@router.put("/{subject_id}/batches", response_model=SubjectOut)
async def set_batches(subject_id: uuid.UUID, body: BatchIdsIn, state: AppState = Depends(get_state),
                      db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    subject = await _load(db, user, subject_id, Cap.edit, admin_ok=True)
    await _set_batches(db, subject.id, body.batch_ids)
    await _record(db, state, user, "subject.batches_set", subject.id, {"batch_ids": [str(b) for b in body.batch_ids]})
    await db.commit()
    return await _out(db, user, subject)


@router.get("/{subject_id}/access", response_model=list[GrantOut])
async def list_access(subject_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    subject = await _load(db, user, subject_id, Cap.share, admin_ok=True)
    rows = (await db.execute(
        select(AccessGrant, User).join(User, User.id == AccessGrant.user_id)
        .where(AccessGrant.subject_id == subject.id).order_by(AccessGrant.created_at)
    )).all()
    return [GrantOut(id=g.id, role=g.role, exam_id=g.exam_id, user=UserBrief.model_validate(u),
                     created_at=g.created_at) for g, u in rows]


@router.post("/{subject_id}/access", response_model=GrantOut, status_code=201)
async def grant_access(subject_id: uuid.UUID, body: GrantIn, state: AppState = Depends(get_state),
                       db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    """Share a subject (or one exam of it) with another professor."""
    subject = await _load(db, user, subject_id, Cap.share, admin_ok=True)
    grantee = await db.scalar(select(User).where(User.email == body.email))
    if grantee is None or grantee.role != Role.professor or not grantee.is_active:
        raise bad_request("not_a_professor", "Access can only be shared with an active professor account.")
    if body.exam_id is not None:
        exam = await db.get(Exam, body.exam_id)
        if exam is None or exam.subject_id != subject.id:
            raise bad_request("unknown_exam", "That exam is not part of this subject.")
        if body.role == GrantRole.owner:
            raise bad_request("bad_role", "Ownership can only be given for the whole subject.")
    scope = AccessGrant.exam_id.is_(None) if body.exam_id is None else AccessGrant.exam_id == body.exam_id
    grant = await db.scalar(select(AccessGrant).where(
        AccessGrant.subject_id == subject.id, AccessGrant.user_id == grantee.id, scope
    ))
    if grant is None:
        grant = AccessGrant(id=uuid.uuid4(), subject_id=subject.id, exam_id=body.exam_id, user_id=grantee.id,
                            role=body.role, granted_by=user.id)
        db.add(grant)
    else:
        if grant.role == GrantRole.owner and body.role != GrantRole.owner:
            await _ensure_other_owner(db, subject.id, grant.id)
        grant.role = body.role
    await _record(db, state, user, "subject.access_granted", subject.id,
                  {"to": grantee.email, "role": body.role.value,
                   "exam_id": str(body.exam_id) if body.exam_id else None})
    await db.commit()
    await db.refresh(grant)
    return GrantOut(id=grant.id, role=grant.role, exam_id=grant.exam_id,
                    user=UserBrief.model_validate(grantee), created_at=grant.created_at)


async def _ensure_other_owner(db: AsyncSession, subject_id: uuid.UUID, grant_id: uuid.UUID) -> None:
    owners = await db.scalar(select(func.count()).select_from(AccessGrant).where(
        AccessGrant.subject_id == subject_id, AccessGrant.role == GrantRole.owner,
        AccessGrant.exam_id.is_(None), AccessGrant.id != grant_id,
    ))
    if not owners:
        raise bad_request("last_owner", "A subject must keep at least one owner.")


@router.delete("/{subject_id}/access/{grant_id}", status_code=204, response_model=None)
async def revoke_access(subject_id: uuid.UUID, grant_id: uuid.UUID, state: AppState = Depends(get_state),
                        db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)) -> None:
    subject = await _load(db, user, subject_id, Cap.share, admin_ok=True)
    grant = await db.get(AccessGrant, grant_id)
    if grant is None or grant.subject_id != subject.id:
        raise not_found()
    if grant.role == GrantRole.owner and grant.exam_id is None:
        await _ensure_other_owner(db, subject.id, grant.id)
    grantee = await db.get(User, grant.user_id)
    await db.delete(grant)
    await _record(db, state, user, "subject.access_revoked", subject.id,
                  {"from": grantee.email if grantee else str(grant.user_id), "role": grant.role.value})
    await db.commit()
