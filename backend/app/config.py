"""Runtime settings, read from environment variables prefixed with AEGIS_.

Every secret has a development default so the app runs out of the box, and
``Settings.check_production()`` refuses to start in production while any
development default is still in use.
"""

from __future__ import annotations

import base64
import binascii
from functools import cached_property
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Development-only secrets. Never used when AEGIS_ENV=production (startup fails).
_DEV_DATA_KEY = base64.b64encode(b"dev-only-key-do-not-use-in-prod!").decode()
_DEV_AUDIT_KEY = base64.b64encode(b"dev-only-audit-key-not-for-prod!").decode()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "production"] = "dev"
    app_name: str = "UAI Aegis"

    # Storage
    database_url: str = "sqlite+aiosqlite:///./aegis-dev.db"
    redis_url: str | None = None  # in-memory fallbacks are used when unset (dev/test only)

    # Encryption at rest: "keyid:base64key,keyid2:base64key". New data uses active_data_key;
    # old keys stay listed so existing rows still decrypt after a rotation.
    data_keys: str = f"dev:{_DEV_DATA_KEY}"
    active_data_key: str = "dev"
    # HMAC key for the tamper-evident audit chain (base64, 32 bytes). Keep it off the DB server.
    audit_key: str = _DEV_AUDIT_KEY

    # Accounts and sessions
    session_ttl_portal_hours: int = 12
    session_ttl_desktop_hours: int = 10
    allowed_email_domains: str = ""  # comma-separated, e.g. "uai.edu.in"; empty = any domain
    cors_origins: str = ""  # comma-separated origins of the professor portal
    login_attempts_per_email: int = 10  # failed attempts per 15 minutes
    login_attempts_per_ip: int = 30  # failed attempts per 15 minutes

    # Student app (desktop) security
    signature_max_skew_seconds: int = 120
    require_known_client: bool = False  # production: only registered app builds may start exams

    # Code execution
    executor: Literal["judge0", "local_dev"] = "local_dev"
    judge0_url: str = "http://judge0:2358"
    judge0_token: str | None = None
    judge0_max_concurrency: int = 8
    judge0_poll_interval_seconds: float = 0.5
    judge0_timeout_seconds: float = 90.0

    # Limits
    time_limit_factor: float = 3.0  # time limit = slowest reference run x factor
    min_time_limit_ms: int = 1000
    max_time_limit_ms: int = 10000
    default_memory_limit_kb: int = 262144
    max_code_bytes: int = 64 * 1024
    max_test_bytes: int = 2 * 1024 * 1024
    max_body_bytes: int = 8 * 1024 * 1024

    # Live monitor (WebSocket push to the professor portal)
    live_ticket_seconds: int = 30  # a ticket must be used within this time, once
    live_refresh_seconds: float = 10.0  # full refresh + permission re-check this often
    live_max_connections_per_user: int = 10

    # Background jobs
    sweeper_enabled: bool = True
    sweeper_interval_seconds: float = 15.0

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @cached_property
    def data_key_map(self) -> dict[str, bytes]:
        keys: dict[str, bytes] = {}
        for item in self.data_keys.split(","):
            item = item.strip()
            if not item:
                continue
            key_id, _, b64 = item.partition(":")
            try:
                raw = base64.b64decode(b64, validate=True)
            except binascii.Error as exc:
                raise ValueError(f"AEGIS_DATA_KEYS entry '{key_id}' is not valid base64") from exc
            if len(raw) != 32:
                raise ValueError(f"AEGIS_DATA_KEYS entry '{key_id}' must decode to 32 bytes")
            keys[key_id.strip()] = raw
        if self.active_data_key not in keys:
            raise ValueError("AEGIS_ACTIVE_DATA_KEY must name one of the keys in AEGIS_DATA_KEYS")
        return keys

    @cached_property
    def audit_key_bytes(self) -> bytes:
        raw = base64.b64decode(self.audit_key, validate=True)
        if len(raw) < 32:
            raise ValueError("AEGIS_AUDIT_KEY must decode to at least 32 bytes")
        return raw

    @property
    def email_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def check_production(self) -> None:
        """Fail fast if a production deployment still uses development defaults."""
        if not self.is_production:
            return
        problems = []
        if _DEV_DATA_KEY in self.data_keys:
            problems.append("AEGIS_DATA_KEYS still contains the development key")
        if self.audit_key == _DEV_AUDIT_KEY:
            problems.append("AEGIS_AUDIT_KEY is the development key")
        if self.executor == "local_dev":
            problems.append("AEGIS_EXECUTOR=local_dev runs code without a sandbox; use judge0")
        if self.database_url.startswith("sqlite"):
            problems.append("SQLite is for development only; use PostgreSQL")
        if not self.redis_url:
            problems.append("AEGIS_REDIS_URL is required (rate limits and replay protection)")
        if not self.require_known_client:
            problems.append("AEGIS_REQUIRE_KNOWN_CLIENT must be true")
        if problems:
            raise RuntimeError("Refusing to start in production:\n- " + "\n- ".join(problems))
        # Touch the key properties so malformed keys fail at startup, not mid-exam.
        _ = self.data_key_map, self.audit_key_bytes
