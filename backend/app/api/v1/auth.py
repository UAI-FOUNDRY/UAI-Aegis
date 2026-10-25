"""Sign in / sign out.

Development login uses email + password (Argon2id). College SSO (Google or Microsoft)
will be added as another route that ends in the same AuthSession, so nothing else in
the system changes when it arrives.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import bad_request, too_many, unauthorized
from ...models import AuthSession, ClientKind, User, utcnow
from ...security.passwords import hash_password, needs_rehash, password_problem, verify_password
from ...security.tokens import new_token
from ...services import audit
from ...state import AppState
from ..deps import client_ip, current_user, get_db, get_state
from ..schemas import Message, UserOut, normalise_email

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)
    client: ClientKind = ClientKind.portal

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalise_email(v)


class LoginOut(BaseModel):
    token: str
    expires_at: datetime
    user: UserOut


class ChangePasswordIn(BaseModel):
    current_password: str | None = Field(None, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


@router.post("/login", response_model=LoginOut)
async def login(
    body: LoginIn,
    request: Request,
    state: AppState = Depends(get_state),
    db: AsyncSession = Depends(get_db),
) -> LoginOut:
    settings = state.settings
    ip = client_ip(request)
    allowed_ip = await state.limiter.hit(f"login:ip:{ip}", settings.login_attempts_per_ip, 900)
    allowed_email = await state.limiter.hit(f"login:email:{body.email}", settings.login_attempts_per_email, 900)
    if not (allowed_ip and allowed_email):
        raise too_many("Too many sign-in attempts. Wait 15 minutes and try again.")

    user = await db.scalar(select(User).where(User.email == body.email))
    # verify_password runs even for unknown emails, so timing does not reveal which exist.
    valid = verify_password(user.password_hash if user else None, body.password)
    if not valid or user is None or not user.is_active:
        raise unauthorized("Wrong email or password.", code="invalid_credentials")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    raw, digest = new_token()
    hours = settings.session_ttl_desktop_hours if body.client == ClientKind.desktop else settings.session_ttl_portal_hours
    session = AuthSession(
        user_id=user.id,
        token_hash=digest,
        client=body.client,
        expires_at=utcnow() + timedelta(hours=hours),
        ip=ip[:64],
        user_agent=(request.headers.get("user-agent") or "")[:256],
    )
    db.add(session)
    await db.commit()
    return LoginOut(token=raw, expires_at=session.expires_at, user=UserOut.model_validate(user))


@router.post("/logout", response_model=Message)
async def logout(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> Message:
    session: AuthSession = request.state.auth_session
    await db.execute(update(AuthSession).where(AuthSession.id == session.id).values(revoked_at=utcnow()))
    await db.commit()
    return Message(message="Signed out.")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> User:
    return user


@router.post("/change-password", response_model=Message)
async def change_password(
    body: ChangePasswordIn,
    request: Request,
    state: AppState = Depends(get_state),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> Message:
    user = await db.get(User, user.id)
    if user.password_hash and not verify_password(user.password_hash, body.current_password or ""):
        raise bad_request("wrong_password", "Your current password is wrong.")
    problem = password_problem(body.new_password, email=user.email)
    if problem:
        raise bad_request("weak_password", problem)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # Sign out every other session: if someone else knew the old password, they are out.
    current: AuthSession = request.state.auth_session
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.id != current.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await audit.record(db, state.settings.audit_key_bytes, actor=user, action="user.password_changed",
                       target_type="user", target_id=user.id)
    await db.commit()
    return Message(message="Password changed.")
