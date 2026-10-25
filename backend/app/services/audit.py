"""Append to and verify the tamper-evident audit chain (see models/audit.py)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import GENESIS_HASH, AuditEntry, AuditHead, User, utcnow


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _json_safe(details: dict[str, Any] | None) -> dict[str, Any]:
    return json.loads(_canonical(details or {}))


def _timestamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _entry_hash(key: bytes, prev_hash: str, entry: AuditEntry) -> str:
    payload = {
        "seq": entry.seq,
        "at": _timestamp(entry.at),
        "actor_id": str(entry.actor_id) if entry.actor_id else None,
        "actor_label": entry.actor_label,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "details": entry.details,
    }
    return hmac.new(key, f"{prev_hash}|{_canonical(payload)}".encode(), hashlib.sha256).hexdigest()


async def ensure_head(db: AsyncSession) -> None:
    if await db.get(AuditHead, 1) is None:
        db.add(AuditHead(id=1, seq=0, hash=GENESIS_HASH))
        await db.flush()


async def record(
    db: AsyncSession,
    key: bytes,
    *,
    actor: User | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | str,
    details: dict[str, Any] | None = None,
) -> AuditEntry:
    """Append an entry in the caller's transaction, so the change and its audit record
    commit (or roll back) together. Never put secrets or student code in details."""
    head = await db.get(AuditHead, 1, with_for_update=True)  # serialises writers on PostgreSQL
    if head is None:
        await ensure_head(db)
        head = await db.get(AuditHead, 1, with_for_update=True)
    entry = AuditEntry(
        id=uuid.uuid4(),
        seq=head.seq + 1,
        at=utcnow(),
        actor_id=actor.id if actor else None,
        actor_label=actor.email if actor else "system",
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=_json_safe(details),
        prev_hash=head.hash,
    )
    entry.entry_hash = _entry_hash(key, head.hash, entry)
    db.add(entry)
    head.seq = entry.seq
    head.hash = entry.entry_hash
    return entry


async def verify_chain(db: AsyncSession, key: bytes) -> dict[str, Any]:
    prev = GENESIS_HASH
    expected_seq = 1
    checked = 0
    result = await db.stream_scalars(select(AuditEntry).order_by(AuditEntry.seq))
    async for entry in result:
        if entry.seq != expected_seq:
            return {"ok": False, "checked": checked, "problem_seq": expected_seq,
                    "problem": "entry missing or out of order"}
        if entry.prev_hash != prev or not hmac.compare_digest(
            _entry_hash(key, prev, entry), entry.entry_hash
        ):
            return {"ok": False, "checked": checked, "problem_seq": entry.seq,
                    "problem": "entry was modified"}
        prev = entry.entry_hash
        expected_seq += 1
        checked += 1
    head = await db.get(AuditHead, 1)
    head_seq, head_hash = (head.seq, head.hash) if head else (0, GENESIS_HASH)
    if head_seq != checked or head_hash != prev:
        return {"ok": False, "checked": checked, "problem_seq": checked + 1,
                "problem": "latest entries were deleted"}
    return {"ok": True, "checked": checked, "head_hash": prev}
