"""Who may do what with a subject or exam.

Rules:
- Students never get grants; they only see their own sessions.
- Professors see a subject only through an AccessGrant (owner/editor/grader/viewer),
  either for the whole subject or for one exam.
- Admins manage accounts, subjects and grants, but get no access to questions, tests
  or answers unless they also hold a grant (they cannot, grants need a professor).
- A user with no grant gets 404 (existence is not revealed); with some grant but not the
  needed capability, 403.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import forbidden, not_found
from ..models import AccessGrant, GrantRole, Role, User


class Cap(StrEnum):
    view = "view"  # see questions, tests, answers, results, monitor
    edit = "edit"  # change questions, tests and exams
    grade = "grade"  # manual marks
    proctor = "proctor"  # open/close exams, lock/resume/end sessions
    share = "share"  # grant or revoke access for other professors


ROLE_CAPS: dict[GrantRole, frozenset[Cap]] = {
    GrantRole.owner: frozenset({Cap.view, Cap.edit, Cap.grade, Cap.proctor, Cap.share}),
    GrantRole.editor: frozenset({Cap.view, Cap.edit, Cap.grade, Cap.proctor}),
    GrantRole.grader: frozenset({Cap.view, Cap.grade, Cap.proctor}),
    GrantRole.viewer: frozenset({Cap.view}),
}


async def caps_for(
    db: AsyncSession, user: User, subject_id: uuid.UUID, exam_id: uuid.UUID | None = None
) -> set[Cap]:
    if user.role != Role.professor:
        return set()
    scope = (
        AccessGrant.exam_id.is_(None)
        if exam_id is None
        else or_(AccessGrant.exam_id.is_(None), AccessGrant.exam_id == exam_id)
    )
    roles = await db.scalars(
        select(AccessGrant.role).where(
            AccessGrant.subject_id == subject_id, AccessGrant.user_id == user.id, scope
        )
    )
    caps: set[Cap] = set()
    for role in roles:
        caps |= ROLE_CAPS[role]
    return caps


async def require_cap(
    db: AsyncSession,
    user: User,
    subject_id: uuid.UUID,
    cap: Cap,
    exam_id: uuid.UUID | None = None,
) -> set[Cap]:
    caps = await caps_for(db, user, subject_id, exam_id)
    if not caps:
        raise not_found()
    if cap not in caps:
        raise forbidden()
    return caps
