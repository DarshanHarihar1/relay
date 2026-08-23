from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from pydantic import Field

from app.contracts import AuditLogEntry, ContractModel, NonEmptyString


class AssessDisruption(ContractModel):
    """The only Phase 3 handoff. It carries identifiers and nothing else."""

    disruption_id: NonEmptyString
    commitment_id: NonEmptyString
    correlation_id: NonEmptyString
    source_event_key: NonEmptyString


class Phase3WorkPort(Protocol):
    async def enqueue_assessment(self, command: AssessDisruption) -> None: ...


# Every ingestion-owned category and how long Relay may keep it.
RETENTION_POLICY: dict[str, timedelta] = {
    "raw_evidence": timedelta(days=30),
    "picker_sessions": timedelta(minutes=15),
    "context_cache": timedelta(minutes=15),
    "calendar_free_busy": timedelta(hours=24),
    "revoked_token_ciphertext": timedelta(0),
    "audit_log": timedelta(days=90),
}

# Field names whose values must never reach a log line or an audit payload.
REDACTED_FIELDS = frozenset(
    {
        "authorization",
        "access_token",
        "id_token",
        "refresh_token",
        "encrypted_refresh_token",
        "encrypted_phone_number",
        "encrypted_booking_reference",
        "phone_number",
        "booking_reference",
        "text_body",
        "body",
        "evidence_excerpt",
        "subject",
        "from_address",
        "email_address",
        "emailAddress",
        "display_name",
        "location_text",
        "summary",
    }
)

REDACTED = "[redacted]"


class RetentionSummary(ContractModel):
    raw_evidence_deleted: int = Field(default=0, ge=0)
    picker_sessions_deleted: int = Field(default=0, ge=0)
    context_cache_deleted: int = Field(default=0, ge=0)
    calendar_free_busy_deleted: int = Field(default=0, ge=0)
    revoked_token_ciphertext_deleted: int = Field(default=0, ge=0)
    audit_log_deleted: int = Field(default=0, ge=0)

    def total(self) -> int:
        return sum(self.model_dump().values())


class RetentionStore(Protocol):
    async def purge_older_than(self, *, category: str, cutoff: datetime | None) -> int: ...

    async def append_audit_event(self, event: AuditLogEntry) -> None: ...


class RetentionService:
    """Deletes expired ingestion data and records only counts.

    Every purge is idempotent: it deletes whatever is currently past its cutoff,
    so running it twice deletes nothing the second time.
    """

    def __init__(self, *, store: RetentionStore, user_id: str = "system") -> None:
        self._store = store
        self._user_id = user_id

    async def purge_expired_ingestion_data(self, *, now: datetime) -> RetentionSummary:
        counts: dict[str, int] = {}
        for category, window in RETENTION_POLICY.items():
            deleted = await self._store.purge_older_than(category=category, cutoff=now - window)
            counts[f"{category}_deleted"] = deleted
            if deleted:
                await self._store.append_audit_event(
                    AuditLogEntry(
                        id=uuid4().hex,
                        user_id=self._user_id,
                        outcome=f"{category.upper()}_PURGED",
                        correlation_id=f"retention:{now.date().isoformat()}",
                        payload={"category": category, "deleted": str(deleted)},
                    )
                )
        return RetentionSummary(**counts)


class RedactingLogFilter(logging.Filter):
    """Last line of defence: redact sensitive extras on their way to a log sink."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name in list(record.__dict__):
            if name in _STANDARD_RECORD_ATTRIBUTES:
                continue
            if name in REDACTED_FIELDS:
                record.__dict__[name] = REDACTED
            else:
                record.__dict__[name] = redact_log_fields(record.__dict__[name])
        return True


_STANDARD_RECORD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
) | {"message", "asctime", "taskName"}


def redact_log_fields(fields: Any) -> Any:
    """Replace every sensitive value before it can reach a log or an audit."""
    if isinstance(fields, dict):
        return {
            name: REDACTED if name in REDACTED_FIELDS else redact_log_fields(value)
            for name, value in fields.items()
        }
    if isinstance(fields, (list, tuple)):
        return [redact_log_fields(item) for item in fields]
    return fields


__all__ = [
    "REDACTED_FIELDS",
    "RedactingLogFilter",
    "RETENTION_POLICY",
    "AssessDisruption",
    "Phase3WorkPort",
    "RetentionService",
    "RetentionSummary",
    "redact_log_fields",
]
