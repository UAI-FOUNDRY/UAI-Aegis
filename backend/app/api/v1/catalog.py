"""Read-only lists the apps need for dropdowns and pickers."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Batch, Department, Language, Role, User
from ..deps import current_user, get_db, require_staff
from ..schemas import ORM, LanguageOut, UserBrief

router = APIRouter(prefix="/catalog", tags=["catalog"])


class DepartmentOut(ORM):
    id: uuid.UUID
    code: str
    name: str


class BatchOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    department_id: uuid.UUID | None


@router.get("/languages", response_model=list[LanguageOut], dependencies=[Depends(current_user)])
async def languages(db: AsyncSession = Depends(get_db)):
    return (await db.scalars(select(Language).where(Language.is_enabled).order_by(Language.name))).all()


@router.get("/departments", response_model=list[DepartmentOut], dependencies=[Depends(require_staff)])
async def departments(db: AsyncSession = Depends(get_db)):
    return (await db.scalars(select(Department).order_by(Department.code))).all()


@router.get("/batches", response_model=list[BatchOut], dependencies=[Depends(require_staff)])
async def batches(db: AsyncSession = Depends(get_db)):
    return (await db.scalars(select(Batch).order_by(Batch.code))).all()


@router.get("/professors", response_model=list[UserBrief], dependencies=[Depends(require_staff)])
async def professors(q: str = Query("", max_length=100), db: AsyncSession = Depends(get_db)):
    query = select(User).where(User.role == Role.professor, User.is_active).order_by(User.full_name).limit(50)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(User.email.like(like), User.full_name.ilike(like)))
    return (await db.scalars(query)).all()
