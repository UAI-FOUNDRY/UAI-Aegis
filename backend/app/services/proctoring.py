"""Violation policy: warn with strikes, lock on serious events, professor decides.

Why lock instead of auto-submitting:
- Auto-submit is irreversible. A false alarm (antivirus popup, OS update notice) would
  end an honest student's exam.
- A lock stops further work immediately (the server refuses saves, runs and submits
  for a locked session, even from a modified app), keeps the code frozen and safe,
  and leaves the judgement to the professor, who is usually in the room.
- The timer keeps running while locked, so locking cannot be used to buy time.
  The professor can add time when resuming after a false alarm.
- If nobody acts before the deadline, the frozen code is auto-submitted and flagged.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models import ExamSession, ProctorEvent, SessionStatus, Severity, utcnow

# What each client-reported event means by default. Exams can override any of these.
DEFAULT_SEVERITY: dict[str, Severity] = {
    # Usually harmless or ambiguous -> a strike
    "focus_lost": Severity.minor,
    "fullscreen_exited": Severity.minor,
    "app_restarted": Severity.minor,
    "clipboard_external_paste": Severity.minor,
    # Blocked by the app itself, recorded for context only
    "paste_blocked": Severity.info,
    "shortcut_blocked": Severity.info,
    "network_reconnected": Severity.info,
    "heartbeat_gap": Severity.info,  # Wi-Fi drops are common; shown, not punished
    # Strong signs of an attempt to get around the lockdown -> lock immediately
    "display_added": Severity.severe,
    "vm_detected": Severity.severe,
    "remote_desktop_detected": Severity.severe,
    "screen_capture_detected": Severity.severe,
    "blocked_app_detected": Severity.severe,
    "debugger_detected": Severity.severe,
    "integrity_failed": Severity.severe,
    "device_changed": Severity.severe,
}

EVENT_KIND = re.compile(r"^[a-z0-9_]{1,48}$")


class ExamPolicy(BaseModel):
    """Per-exam proctoring settings, editable by the professor."""

    model_config = ConfigDict(extra="forbid")

    max_strikes: int = Field(3, ge=1, le=50, description="Minor events allowed before the session locks.")
    lock_on_severe: bool = Field(True, description="Lock immediately on a severe event.")
    severity: dict[str, Severity] = Field(default_factory=dict, description="Override event severities.")
    reveal_hidden_results: bool = Field(False, description="Show pass/fail of hidden tests on Submit.")
    runs_per_minute: int = Field(6, ge=1, le=60, description="Run/Submit limit per student per question.")
    grace_seconds: int = Field(30, ge=0, le=300, description="Accept in-flight saves after the deadline.")
    heartbeat_timeout_seconds: int = Field(45, ge=10, le=600)
    allow_device_change: bool = Field(True, description="Allow switching laptop with professor approval.")


def effective_policy(raw: dict[str, Any] | None) -> ExamPolicy:
    return ExamPolicy.model_validate(raw or {})


def classify(kind: str, policy: ExamPolicy) -> Severity:
    if kind in policy.severity:
        return policy.severity[kind]
    # Unknown kinds (e.g. from a newer app version) count as a strike rather than being ignored.
    return DEFAULT_SEVERITY.get(kind, Severity.minor)


def lock(session: ExamSession, reason: str, *, now: datetime | None = None) -> None:
    session.status = SessionStatus.locked
    session.lock_reason = reason[:64]
    session.locked_at = now or utcnow()
    session.flagged = True


def apply_event(
    session: ExamSession,
    policy: ExamPolicy,
    *,
    kind: str,
    source: str,
    details: dict[str, Any] | None = None,
    client_ts: datetime | None = None,
) -> ProctorEvent:
    """Record an event and apply the policy. The caller adds the event to the DB session."""
    severity = classify(kind, policy)
    was_active = session.status == SessionStatus.active
    if was_active:
        if severity == Severity.severe and policy.lock_on_severe:
            lock(session, kind)
        elif severity in (Severity.minor, Severity.severe):
            session.strikes += 1
            if session.strikes >= policy.max_strikes:
                lock(session, "too_many_violations")
    return ProctorEvent(
        session_id=session.id,
        kind=kind,
        severity=severity,
        source=source,
        details=details or {},
        client_ts=client_ts,
        caused_lock=was_active and session.status == SessionStatus.locked,
    )
