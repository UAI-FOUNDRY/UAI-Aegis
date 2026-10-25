"""Response/request models shared by several routers."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import Role

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


def normalise_email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL_RE.match(value):
        raise ValueError("not a valid email address")
    return value


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    college_id: str | None
    role: Role
    department_id: uuid.UUID | None
    is_active: bool
    must_change_password: bool
    created_at: datetime


class UserBrief(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    college_id: str | None


class Message(BaseModel):
    message: str


class LanguageOut(ORM):
    id: uuid.UUID
    key: str
    name: str
    monaco_id: str
    file_name: str
    time_multiplier: float
    is_enabled: bool


class Page(BaseModel):
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)


class EmailField(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalise_email(v)
