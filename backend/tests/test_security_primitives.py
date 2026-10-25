import base64
import os
import uuid

import pytest

from app.grading.compare import outputs_match
from app.security.crypto import AAD, DecryptionError, FieldCipher
from app.security.passwords import hash_password, password_problem, verify_password
from app.security.signing import DeviceSigner, canonical_message, verify_signature
from app.security.tokens import hash_token, new_access_code, new_token


def cipher(**keys):
    keys = keys or {"k1": os.urandom(32)}
    return FieldCipher(keys, next(iter(keys)))


def test_encrypt_roundtrip_and_ciphertext_is_random():
    c = cipher()
    a, b = c.encrypt("print(1)", "aad"), c.encrypt("print(1)", "aad")
    assert a != b  # fresh nonce every time
    assert b"print" not in a
    assert c.decrypt_str(a, "aad") == "print(1)"


def test_ciphertext_moved_to_another_row_fails():
    c = cipher()
    s1, s2, q = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    blob = c.encrypt("student one's code", AAD.answer(s1, q))
    with pytest.raises(DecryptionError):
        c.decrypt(blob, AAD.answer(s2, q))


def test_tampered_ciphertext_fails():
    c = cipher()
    blob = bytearray(c.encrypt("x = 1", "aad"))
    blob[-1] ^= 1
    with pytest.raises(DecryptionError):
        c.decrypt(bytes(blob), "aad")


def test_key_rotation_keeps_old_data_readable():
    old, new = os.urandom(32), os.urandom(32)
    blob = FieldCipher({"old": old}, "old").encrypt("secret", "aad")
    rotated = FieldCipher({"old": old, "new": new}, "new")
    assert rotated.decrypt_str(blob, "aad") == "secret"
    assert rotated.key_id_of(rotated.encrypt("x", "aad")) == "new"
    with pytest.raises(DecryptionError):
        FieldCipher({"new": new}, "new").decrypt(blob, "aad")


def test_passwords():
    h = hash_password("Correct-Horse-42!")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "Correct-Horse-42!")
    assert not verify_password(h, "wrong")
    assert not verify_password(None, "anything")
    assert password_problem("short") is not None
    assert password_problem("aaaaaaaaaaaa") is not None
    assert password_problem("Correct-Horse-42!") is None


def test_tokens_and_codes():
    raw, digest = new_token()
    assert raw.startswith("aegis_") and len(raw) > 40
    assert hash_token(raw) == digest and len(digest) == 32
    code = new_access_code()
    assert len(code) == 8 and not set(code) & set("01OIL")


def test_signatures():
    signer = DeviceSigner()
    signer.device_id = str(uuid.uuid4())
    h = signer.headers("POST", "/api/v1/x", b'{"a":1}', timestamp=1700000000, nonce="n" * 22)
    msg = canonical_message("POST", "/api/v1/x", "1700000000", "n" * 22, b'{"a":1}')
    assert verify_signature(signer.public_key_raw, msg, h["X-Aegis-Signature"])
    assert not verify_signature(signer.public_key_raw, msg.replace(b"x", b"y"), h["X-Aegis-Signature"])
    other = DeviceSigner()
    assert not verify_signature(other.public_key_raw, msg, h["X-Aegis-Signature"])


@pytest.mark.parametrize("actual,expected,mode,ok", [
    ("5\n", "5", "lines", True),
    ("5  \n\n\n", "5\n", "lines", True),
    ("5", "6", "lines", False),
    ("5\n", "5", "exact", False),
    ("1   2\n3", "1 2 3", "whitespace", True),
    ("HELLO", "hello", "case_insensitive", True),
    ("3.1415927", "3.14159265", "float", True),
    ("3.2", "3.14159265", "float", False),
    ("b\na\n", "a\nb", "unordered_lines", True),
    ("nan", "nan", "float", True),
])
def test_compare_modes(actual, expected, mode, ok):
    assert outputs_match(actual, expected, mode, tolerance=1e-6) is ok


def test_settings_reject_dev_keys_in_production():
    from app.config import Settings

    with pytest.raises(RuntimeError) as err:
        Settings(env="production").check_production()
    assert "AEGIS_DATA_KEYS" in str(err.value)
    good = base64.b64encode(os.urandom(32)).decode()
    Settings(env="production", data_keys=f"p1:{good}", active_data_key="p1", audit_key=good, executor="judge0",
             database_url="postgresql+asyncpg://x/y", redis_url="redis://r", require_known_client=True
             ).check_production()
