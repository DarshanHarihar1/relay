from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)


class FakeRetentionStore:
    """Records what a purge asked for, so the policy itself can be asserted."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, datetime | None]] = []
        self.counts = {
            "raw_evidence": 1,
            "picker_sessions": 2,
            "context_cache": 3,
            "calendar_free_busy": 4,
            "revoked_token_ciphertext": 5,
            "audit_log": 6,
        }
        self.audit_events: list = []

    async def purge_older_than(self, *, category: str, cutoff: datetime | None) -> int:
        self.requests.append((category, cutoff))
        return self.counts[category]

    async def append_audit_event(self, event) -> None:
        self.audit_events.append(event)


@pytest.mark.asyncio
async def test_retention_purges_raw_evidence_and_keeps_a_redacted_audit() -> None:
    from app.services.retention import RetentionService

    store = FakeRetentionStore()

    summary = await RetentionService(store=store).purge_expired_ingestion_data(now=NOW)

    assert summary.raw_evidence_deleted == 1
    assert store.audit_events[0].outcome == "RAW_EVIDENCE_PURGED"
    assert store.audit_events[0].payload == {"category": "raw_evidence", "deleted": "1"}


@pytest.mark.asyncio
async def test_every_retention_window_matches_the_documented_policy() -> None:
    from app.services.retention import RetentionService

    store = FakeRetentionStore()

    await RetentionService(store=store).purge_expired_ingestion_data(now=NOW)

    assert dict(store.requests) == {
        "raw_evidence": NOW - timedelta(days=30),
        "picker_sessions": NOW - timedelta(minutes=15),
        "context_cache": NOW - timedelta(minutes=15),
        "calendar_free_busy": NOW - timedelta(hours=24),
        "revoked_token_ciphertext": NOW,
        "audit_log": NOW - timedelta(days=90),
    }


@pytest.mark.asyncio
async def test_a_repeated_purge_is_idempotent_and_returns_counts_only() -> None:
    from app.services.retention import RetentionService

    store = FakeRetentionStore()
    service = RetentionService(store=store)

    first = await service.purge_expired_ingestion_data(now=NOW)
    store.counts = dict.fromkeys(store.counts, 0)
    second = await service.purge_expired_ingestion_data(now=NOW)

    assert second.raw_evidence_deleted == 0
    assert second.total() == 0
    assert first.total() == 21
    # Counts only. No message ID, address, phone, booking reference, or excerpt.
    assert all(isinstance(value, int) for value in second.model_dump().values())


def test_structured_log_redaction_covers_every_sensitive_field() -> None:
    from app.services.retention import redact_log_fields

    redacted = redact_log_fields(
        {
            "authorization": "Bearer ya29.secret",
            "refresh_token": "1//refresh",
            "encrypted_refresh_token": "gAAAA",
            "phone_number": "+919876543210",
            "booking_reference": "AB12CD",
            "text_body": "Your flight is delayed",
            "evidence_excerpt": "Your flight is delayed",
            "subject": "Flight delayed",
            "from_address": "updates@example.test",
            "email_address": "user@example.test",
            "correlation_id": "corr-1",
            "outcome": "MATCH_REVIEW_REQUIRED",
            "score": 100,
        }
    )

    assert redacted["correlation_id"] == "corr-1"
    assert redacted["outcome"] == "MATCH_REVIEW_REQUIRED"
    assert redacted["score"] == 100
    for field in (
        "authorization",
        "refresh_token",
        "encrypted_refresh_token",
        "phone_number",
        "booking_reference",
        "text_body",
        "evidence_excerpt",
        "subject",
        "from_address",
        "email_address",
    ):
        assert redacted[field] == "[redacted]", field


def test_redaction_is_recursive_and_leaves_no_secret_in_a_nested_payload() -> None:
    from app.services.retention import redact_log_fields

    redacted = redact_log_fields(
        {"request": {"headers": {"authorization": "Bearer ya29.secret"}}, "items": [{"phone_number": "+91"}]}
    )

    assert "ya29.secret" not in str(redacted)
    assert redacted["items"][0]["phone_number"] == "[redacted]"


def test_the_log_filter_redacts_sensitive_extras_before_they_are_emitted() -> None:
    import logging

    from app.services.retention import RedactingLogFilter

    record = logging.LogRecord("relay.test", logging.INFO, __file__, 1, "event", None, None)
    record.correlation_id = "corr-1"
    record.email_address = "user@example.test"
    record.detail = {"booking_reference": "AB12CD"}

    assert RedactingLogFilter().filter(record) is True
    assert record.correlation_id == "corr-1"
    assert record.email_address == "[redacted]"
    assert record.detail == {"booking_reference": "[redacted]"}


def test_the_log_filter_leaves_the_standard_record_attributes_alone() -> None:
    import logging

    from app.services.retention import RedactingLogFilter

    record = logging.LogRecord("relay.test", logging.INFO, __file__, 1, "msg %s", ("x",), None)

    RedactingLogFilter().filter(record)

    assert record.getMessage() == "msg x"
    assert record.name == "relay.test"
