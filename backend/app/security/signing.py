"""Ed25519 request signing for the student desktop app.

The app creates an Ed25519 key pair on first launch, keeps the private key in the OS
keychain, and registers the public key with the server. Every exam request then
carries these headers:

    X-Aegis-Device:     device id returned at registration
    X-Aegis-Timestamp:  unix time in seconds
    X-Aegis-Nonce:      16+ random bytes, base64url (never reused)
    X-Aegis-Signature:  base64url Ed25519 signature of the canonical message below

Canonical message (UTF-8, lines joined with "\\n"):

    AEGIS-SIG-V1
    <HTTP method, upper case>
    <path including query string, e.g. /api/v1/student/sessions/<id>/heartbeat>
    <timestamp>
    <nonce>
    <lower-case hex SHA-256 of the raw request body (empty body -> hash of b"")>

The server rejects stale timestamps, reused nonces (replay) and any signature that does
not verify against the device's registered key. A stolen login token is therefore
useless on any other machine.

`DeviceSigner` below is the reference implementation; the desktop app must produce
byte-identical messages (see docs/CLIENT_PROTOCOL.md).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import time
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SIG_VERSION = "AEGIS-SIG-V1"
REGISTER_VERSION = "AEGIS-REGISTER-V1"

HEADER_DEVICE = "X-Aegis-Device"
HEADER_TIMESTAMP = "X-Aegis-Timestamp"
HEADER_NONCE = "X-Aegis-Nonce"
HEADER_SIGNATURE = "X-Aegis-Signature"


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    try:
        padded = text + "=" * (-len(text) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("invalid base64url") from exc


def canonical_message(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    return "\n".join(
        [SIG_VERSION, method.upper(), path, timestamp, nonce, hashlib.sha256(body).hexdigest()]
    ).encode("utf-8")


def registration_message(user_id: uuid.UUID | str, public_key_b64: str) -> bytes:
    return f"{REGISTER_VERSION}\n{user_id}\n{public_key_b64}".encode()


def verify_signature(public_key_raw: bytes, message: bytes, signature_b64: str) -> bool:
    try:
        signature = b64url_decode(signature_b64)
        Ed25519PublicKey.from_public_bytes(public_key_raw).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


class DeviceSigner:
    """Reference client. Used by the tests and as the spec for the desktop app."""

    def __init__(self, private_key: Ed25519PrivateKey | None = None) -> None:
        self.private_key = private_key or Ed25519PrivateKey.generate()
        self.device_id: str | None = None

    @property
    def public_key_raw(self) -> bytes:
        return self.private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def public_key_b64(self) -> str:
        return b64url_encode(self.public_key_raw)

    def registration_proof(self, user_id: uuid.UUID | str) -> str:
        return b64url_encode(self.private_key.sign(registration_message(user_id, self.public_key_b64)))

    def headers(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        *,
        timestamp: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        if self.device_id is None:
            raise RuntimeError("register the device first")
        ts = str(timestamp if timestamp is not None else int(time.time()))
        nonce = nonce or b64url_encode(secrets.token_bytes(16))
        signature = self.private_key.sign(canonical_message(method, path, ts, nonce, body))
        return {
            HEADER_DEVICE: self.device_id,
            HEADER_TIMESTAMP: ts,
            HEADER_NONCE: nonce,
            HEADER_SIGNATURE: b64url_encode(signature),
        }
