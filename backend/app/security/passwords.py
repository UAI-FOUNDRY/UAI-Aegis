"""Password hashing with Argon2id (winner of the Password Hashing Competition,
first choice in the OWASP password storage guidance).

Only used for the development login and the fallback "college ID + password" login.
With college SSO, students never give Aegis a password at all.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults follow RFC 9106's recommended profile: Argon2id, 64 MiB memory,
# 3 iterations, 4 lanes, 16-byte random salt per hash.
_hasher = PasswordHasher()
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
