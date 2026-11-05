"""Password hashing with Argon2id (winner of the Password Hashing Competition,
first choice in the OWASP password storage guidance).

Only used for the development login and the fallback "college ID + password" login.
With college SSO, students never give Aegis a password at all.
"""

from __future__ import annotations

import asyncio
import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id with OWASP's recommended parameters (19 MiB, 2 iterations, 1 lane, 16-byte
# random salt). A whole lab signs in at the start of an exam, so the server must check
# ~100 passwords within seconds on modest hardware; argon2-cffi's default profile (64 MiB,
# 3 iterations, 4 threads per hash) costs about 5x more CPU. Stored hashes with other
# parameters keep working and are upgraded at the next sign-in (needs_rehash).
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
# Hash compared against when the account does not exist, so a wrong email and a
# wrong password take the same time (no account enumeration by timing).
_DUMMY_HASH = _hasher.hash("aegis-timing-equaliser")

MIN_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        if not stored_hash:
            _hasher.verify(_DUMMY_HASH, password)
            return False
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# Each hash takes tens of milliseconds of CPU and 19 MiB. Run them in worker threads
# (argon2 releases the GIL) so the server keeps answering other requests, at most one
# per CPU core but one, so a lab signing in together cannot starve everything else.
DEFAULT_CONCURRENCY = max(1, min(8, (os.cpu_count() or 2) - 1))


async def verify_password_async(slots: asyncio.Semaphore, stored_hash: str | None, password: str) -> bool:
    async with slots:
        return await asyncio.to_thread(verify_password, stored_hash, password)


async def hash_password_async(slots: asyncio.Semaphore, password: str) -> str:
    async with slots:
        return await asyncio.to_thread(hash_password, password)


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def password_problem(password: str, *, email: str | None = None) -> str | None:
    """Return why a new password is unacceptable, or None if it is fine."""
    if len(password) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters."
    if len(password) > 256:
        return "Password is too long."
    if len(set(password)) < 4:
        return "Password is too repetitive."
    if email and email.split("@")[0].lower() in password.lower():
        return "Password must not contain your email name."
    return None
