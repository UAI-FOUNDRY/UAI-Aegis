"""Field-level encryption at rest with AES-256-GCM.

What gets encrypted: students' code (drafts, final answers, run history), hidden test
inputs and expected outputs, reference solutions, and exam access codes.

Every ciphertext is bound to *where it belongs* through GCM's associated data (AAD),
e.g. "aegis:answer:<session id>:<question id>". If a ciphertext is copied or swapped
into another row (another student's answer, another test case), authentication fails
and the server raises DecryptionError instead of returning the wrong data.

Blob layout:  version(1) | key_id_len(1) | key_id | nonce(12) | ciphertext + tag(16)
The key id lets keys be rotated: new data uses the active key, old keys still decrypt.
"""

from __future__ import annotations

import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_FORMAT_VERSION = 1
_NONCE_LEN = 12


class DecryptionError(Exception):
    """Ciphertext is corrupt, tampered with, moved to another row, or the key is missing."""


class FieldCipher:
    def __init__(self, keys: dict[str, bytes], active_key_id: str) -> None:
        if active_key_id not in keys:
            raise ValueError("active key id not in key map")
        for key_id, key in keys.items():
            if len(key) != 32:
                raise ValueError(f"key {key_id!r} must be 32 bytes (AES-256)")
            if not key_id or len(key_id.encode()) > 255:
                raise ValueError("key ids must be 1-255 bytes")
        self._keys = {k: AESGCM(v) for k, v in keys.items()}
        self._active = active_key_id

    def encrypt(self, data: str | bytes, aad: str) -> bytes:
        plaintext = data.encode("utf-8") if isinstance(data, str) else data
        nonce = os.urandom(_NONCE_LEN)  # random 96-bit nonce per message
        key_id = self._active.encode()
        ciphertext = self._keys[self._active].encrypt(nonce, plaintext, aad.encode("utf-8"))
        return bytes([_FORMAT_VERSION, len(key_id)]) + key_id + nonce + ciphertext

    def decrypt(self, blob: bytes, aad: str) -> bytes:
        try:
            if blob[0] != _FORMAT_VERSION:
                raise DecryptionError("unknown ciphertext format")
            kid_len = blob[1]
            key_id = blob[2 : 2 + kid_len].decode()
            nonce = blob[2 + kid_len : 2 + kid_len + _NONCE_LEN]
            ciphertext = blob[2 + kid_len + _NONCE_LEN :]
            aes = self._keys.get(key_id)
            if aes is None:
                raise DecryptionError(f"unknown key id {key_id!r}")
            return aes.decrypt(nonce, ciphertext, aad.encode("utf-8"))
        except DecryptionError:
            raise
        except (InvalidTag, IndexError, UnicodeDecodeError, ValueError) as exc:
            raise DecryptionError("ciphertext failed authentication") from exc

    def decrypt_str(self, blob: bytes, aad: str) -> str:
        return self.decrypt(blob, aad).decode("utf-8")

    def key_id_of(self, blob: bytes) -> str:
        return blob[2 : 2 + blob[1]].decode()


class AAD:
    """Associated-data labels. Always build them through here so they stay consistent."""

    @staticmethod
    def answer(session_id: uuid.UUID, question_id: uuid.UUID) -> str:
        return f"aegis:answer:{session_id}:{question_id}"

    @staticmethod
    def run(run_id: uuid.UUID) -> str:
        return f"aegis:run:{run_id}"

    @staticmethod
    def test_input(test_id: uuid.UUID) -> str:
        return f"aegis:test-input:{test_id}"

    @staticmethod
    def test_expected(test_id: uuid.UUID) -> str:
        return f"aegis:test-expected:{test_id}"

    @staticmethod
    def reference(question_id: uuid.UUID) -> str:
        return f"aegis:reference:{question_id}"

    @staticmethod
    def access_code(exam_id: uuid.UUID) -> str:
        return f"aegis:access-code:{exam_id}"
