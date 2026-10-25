"""Opaque bearer tokens for login sessions.

Tokens are 256 random bits from the OS CSPRNG. The server stores only SHA-256(token),
so tokens can be revoked instantly and a database dump does not contain usable tokens.
The "aegis_" prefix lets secret scanners spot a leaked token.
"""

from __future__ import annotations

import hashlib
import secrets

TOKEN_PREFIX = "aegis_"


def new_token() -> tuple[str, bytes]:
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode("utf-8")).digest()


ACCESS_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L look-alikes


def new_access_code(length: int = 8) -> str:
    return "".join(secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(length))
