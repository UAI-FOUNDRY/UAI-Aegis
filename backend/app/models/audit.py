"""Tamper-evident audit log.

Each entry stores HMAC-SHA256(audit_key, previous_hash | entry). Changing, deleting or
re-ordering any past entry breaks every hash after it, and without the audit key (kept
off the database server) the chain cannot be recomputed to hide the change.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, UTCDateTime

GENESIS_HASH = "0" * 64


class AuditEntry(IdMixin, Base):
    __tablename__ = "audit_entries"

    seq: Mapped[int] = mapped_column(Integer, unique=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime())
    # No foreign key on purpose: audit rows must never change, even if a user row does.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_label: Mapped[str] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))


class AuditHead(Base):
    """Single row holding the latest sequence number and hash (also detects a deleted tail)."""

    __tablename__ = "audit_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    hash: Mapped[str] = mapped_column(String(64))
