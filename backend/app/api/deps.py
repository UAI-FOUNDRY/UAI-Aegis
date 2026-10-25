"""Request dependencies: database session, signed-in user, role checks and the
device-signature check that every student exam request must pass."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError, forbidden, unauthorized
from ..models import AuthSession, Device, Role, User, utcnow
from ..security.signing import (
    HEADER_DEVICE,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    canonical_message,
    verify_signature,
)
from ..security.tokens import TOKEN_PREFIX, hash_token
from ..state import AppState

_bearer = HTTPBearer(auto_error=False, description="Token from POST /api/v1/auth/login")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_PASSWORD_CHANGE_PATHS = {"/api/v1/auth/me", "/api/v1/auth/change-password", "/api/v1/auth/logout"}


def get_state(request: Request) -> AppState:
    return request.app.state.aegis


async def get_db(state: AppState = Depends(get_state)) -> AsyncIterator[AsyncSession]:
    async with state.sessionmaker() as session:
        yield session


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    if creds is None or not creds.credentials.startswith(TOKEN_PREFIX) or len(creds.credentials) > 128:
        raise unauthorized()
    session = await db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(creds.credentials)))
    now = utcnow()
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise unauthorized("Your session has ended. Sign in again.", code="session_expired")
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise unauthorized()
    if session.last_used_at is None or now - session.last_used_at > timedelta(minutes=1):
        session.last_used_at = now
        await db.commit()
    request.state.auth_session = session
    if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_PATHS:
        raise ApiError(403, "password_change_required", "Set a new password before continuing.")
    return user


def require_role(*roles: Role):
    async def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise forbidden()
        return user

    return dependency


require_admin = require_role(Role.admin)
require_staff = require_role(Role.professor, Role.admin)
require_professor = require_role(Role.professor)
require_student = require_role(Role.student)


async def signed_device(
    request: Request,
    state: AppState = Depends(get_state),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_student),
) -> Device:
    """Verify the Ed25519 signature on a student exam request (see security/signing.py)."""
    headers = request.headers
    device_id = headers.get(HEADER_DEVICE)
    timestamp = headers.get(HEADER_TIMESTAMP)
    nonce = headers.get(HEADER_NONCE)
    signature = headers.get(HEADER_SIGNATURE)
    if not (device_id and timestamp and nonce and signature):
        raise unauthorized("Exam requests must come from the UAI Aegis app.", code="signature_required")
    try:
        ts = int(timestamp)
        device_uuid = uuid.UUID(device_id)
    except ValueError:
        raise unauthorized("Malformed signature headers.", code="bad_signature") from None
    skew = state.settings.signature_max_skew_seconds
    if abs(int(time.time()) - ts) > skew:
        raise unauthorized("Your laptop's clock is wrong. Set the correct date and time.", code="stale_signature")
    if not _NONCE_RE.match(nonce):
        raise unauthorized("Malformed signature headers.", code="bad_signature")

    device = await db.get(Device, device_uuid)
    if device is None or device.user_id != user.id or device.revoked_at is not None:
        raise unauthorized("This laptop is not registered to your account.", code="unknown_device")

    body = await request.body()
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if not verify_signature(device.public_key, canonical_message(request.method, path, timestamp, nonce, body), signature):
        raise unauthorized("Signature check failed.", code="bad_signature")
    if not await state.nonces.use(f"{device.id}:{nonce}", ttl_seconds=2 * skew + 5):
        raise unauthorized("This request was already used.", code="replayed_request")
    return device
