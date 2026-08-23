from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import SourceEventEnvelope
from app.domain.ingestion import (
    GmailMessage,
    GmailNotification,
    GmailProfile,
    GoogleConnection,
    HistoryPage,
    WatchRegistration,
)


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)


class _AnyHash:
    """A redacted message hash: present, opaque, and never the message ID."""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) and len(other) == 64 and other != "m1"


ANY_HASH = _AnyHash()


class FakeGmail:
    def __init__(self) -> None:
        self.history_calls = 0
        self.message_calls = 0

    async def list_history(self, *, connection, start_history_id, page_token=None):
        self.history_calls += 1
        assert connection.gmail_label_id == "Label_123"
        assert start_history_id == 800
        return HistoryPage(history_id=900, added_message_ids=["m1", "m1"])

    async def get_message(self, *, connection, message_id):
        self.message_calls += 1
        return GmailMessage(
            id=message_id,
            thread_id="t1",
            history_id=900,
            internal_date=NOW,
            label_ids=frozenset({"Label_123"}),
            subject="Flight update",
            from_address="updates@example.test",
            text_body="Your flight is delayed.",
        )


class FakeRepository:
    def __init__(self) -> None:
        self.connection = GoogleConnection(
            user_id="u1",
            granted_scopes=frozenset({"https://www.googleapis.com/auth/gmail.readonly"}),
            gmail_email_address="mailbox@example.test",
            gmail_label_id="Label_123",
            gmail_history_id=800,
            encrypted_refresh_token="encrypted",
            connected_at=NOW,
        )
        self.cursor = 800
        self.claimed_source_event_keys: set[str] = set()
        self.events: list[SourceEventEnvelope] = []
        self.audits: list[str] = []
        self.details: list[dict[str, str] | None] = []

    async def get_connection(self, user_id):
        return self.connection if user_id == "u1" else None

    async def get_gmail_cursor(self, *, user_id, mailbox):
        return self.cursor

    async def update_gmail_cursor_if_newer(self, *, user_id, mailbox, proposed_history_id):
        self.cursor = max(self.cursor, proposed_history_id)
        return self.cursor

    async def claim_source_event(self, *, user_id, event):
        key = event.source_event_key
        if key in self.claimed_source_event_keys:
            return False
        self.claimed_source_event_keys.add(key)
        self.events.append(event)
        return True

    async def release_source_event_claim(self, *, user_id, source_event_key):
        self.claimed_source_event_keys.discard(source_event_key)

    async def append_ingestion_audit(
        self, *, user_id, outcome, correlation_id, source_event_key=None, detail=None
    ):
        self.audits.append(outcome)
        if detail is not None:
            self.details.append(detail)

    async def put_connection(self, connection):
        self.connection = connection

    async def list_connections_due_for_watch_renewal(self, before):
        expires_at = self.connection.gmail_watch_expires_at
        return [self.connection] if expires_at is not None and expires_at <= before else []


@pytest.mark.asyncio
async def test_duplicate_notification_claims_source_once() -> None:
    from app.services.gmail_ingestion import GmailIngestionService, IngestGmailNotification

    repository = FakeRepository()
    gmail = FakeGmail()
    service = GmailIngestionService(repository=repository, gmail=gmail)
    command = IngestGmailNotification(
        user_id="u1",
        notification=GmailNotification(
            email_address="mailbox@example.test", history_id=900, published_at=NOW
        ),
        correlation_id="corr-1",
    )

    first = await service.ingest_gmail_notification(command)
    second = await service.ingest_gmail_notification(command)

    assert first.persisted_count == 1
    assert second.stale is True
    assert repository.claimed_source_event_keys == {"gmail:m1:900"}
    assert gmail.message_calls == 1


@pytest.mark.asyncio
async def test_retry_uses_exactly_three_delays_then_dead_letters() -> None:
    from app.services.gmail_ingestion import GmailRetryableError
    from app.worker import GmailWorker, InMemoryDeadLetterQueue

    class RetryService:
        async def ingest_gmail_notification(self, command):
            raise GmailRetryableError("Gmail temporarily unavailable")

    delays: list[int] = []
    dead_letters = InMemoryDeadLetterQueue()
    worker = GmailWorker(
        ingestion=RetryService(),
        dead_letters=dead_letters,
        sleep=lambda seconds: delays.append(seconds),
    )
    command = object()

    await worker.process(command)

    assert delays == [30, 120, 600]
    assert dead_letters.items == [command]


@pytest.mark.asyncio
async def test_expired_history_triggers_exactly_one_bounded_resync() -> None:
    from app.adapters.gmail import GmailHistoryExpiredError
    from app.services.gmail_ingestion import GmailIngestionService, IngestGmailNotification

    class ExpiredGmail(FakeGmail):
        def __init__(self) -> None:
            super().__init__()
            self.resync_calls: list[datetime] = []

        async def list_history(self, *, connection, start_history_id, page_token=None):
            self.history_calls += 1
            raise GmailHistoryExpiredError("Gmail history cursor expired")

        async def resync_last_48_hours(self, *, connection, since):
            self.resync_calls.append(since)
            return HistoryPage(history_id=950, added_message_ids=["m2"])

    repository = FakeRepository()
    gmail = ExpiredGmail()
    service = GmailIngestionService(repository=repository, gmail=gmail, now=lambda: NOW)

    summary = await service.ingest_gmail_notification(
        IngestGmailNotification(
            user_id="u1",
            notification=GmailNotification(
                email_address="mailbox@example.test", history_id=900, published_at=NOW
            ),
            correlation_id="corr-1",
        )
    )

    assert summary.resynced is True
    assert summary.persisted_count == 1
    assert gmail.resync_calls == [NOW - timedelta(hours=48)]
    assert repository.audits == ["GMAIL_HISTORY_EXPIRED_RESYNC"]
    assert repository.cursor == 950


@pytest.mark.asyncio
async def test_watch_registration_persists_mailbox_cursor_and_expiry() -> None:
    from app.services.gmail_ingestion import GmailWatchService

    expires_at = NOW + timedelta(days=7)

    class WatchGmail:
        async def get_profile(self, *, connection):
            return GmailProfile(email_address="mailbox@example.test", history_id=900)

        async def ensure_watch(self, *, connection):
            return WatchRegistration(
                history_id=900,
                expires_at=expires_at,
                request={"labelIds": ["Label_123"], "labelFilterBehavior": "INCLUDE"},
            )

    repository = FakeRepository()
    repository.connection = repository.connection.model_copy(
        update={"gmail_email_address": None, "gmail_history_id": None}
    )
    service = GmailWatchService(repository=repository, gmail=WatchGmail(), now=lambda: NOW)

    registration = await service.register_gmail_watch("u1")

    assert registration.history_id == 900
    assert repository.connection.gmail_email_address == "mailbox@example.test"
    assert repository.connection.gmail_history_id == 900
    assert repository.connection.gmail_watch_expires_at == expires_at


@pytest.mark.asyncio
async def test_watch_renewal_audits_a_provider_failure_and_continues() -> None:
    from app.adapters.gmail import GmailRetryableError
    from app.services.gmail_ingestion import GmailWatchService

    class FailingGmail:
        async def get_profile(self, *, connection):
            raise GmailRetryableError("Gmail temporarily unavailable")

        async def ensure_watch(self, *, connection):
            raise AssertionError("ensure_watch must not run after a failed profile read")

    repository = FakeRepository()
    repository.connection = repository.connection.model_copy(
        update={"gmail_watch_expires_at": NOW + timedelta(hours=6)}
    )
    service = GmailWatchService(repository=repository, gmail=FailingGmail(), now=lambda: NOW)

    renewed = await service.renew_expiring_watches()

    assert renewed == []
    assert repository.audits == ["GMAIL_WATCH_RENEWAL_FAILED"]


@pytest.mark.asyncio
async def test_terminal_failure_dead_letters_without_any_retry_delay() -> None:
    from app.adapters.gmail import GmailTerminalError
    from app.worker import GmailWorker, InMemoryDeadLetterQueue

    class TerminalService:
        async def ingest_gmail_notification(self, command):
            raise GmailTerminalError("Gmail authorization failed: 403")

    delays: list[int] = []
    dead_letters = InMemoryDeadLetterQueue()
    worker = GmailWorker(
        ingestion=TerminalService(),
        dead_letters=dead_letters,
        sleep=lambda seconds: delays.append(seconds),
    )
    command = object()

    await worker.process(command)

    assert delays == []
    assert dead_letters.items == [command]


@pytest.mark.asyncio
async def test_bad_model_json_creates_review_not_a_disruption() -> None:
    from app.adapters.gemini import ExtractionReviewRequired
    from app.services.gmail_ingestion import GmailIngestionService, IngestGmailNotification

    class ReviewingExtractor:
        async def extract(self, *, message, correlation_id):
            raise ExtractionReviewRequired("SCHEMA_INVALID")

    repository = FakeRepository()
    service = GmailIngestionService(
        repository=repository, gmail=FakeGmail(), extractor=ReviewingExtractor()
    )

    summary = await service.ingest_gmail_notification(
        IngestGmailNotification(
            user_id="u1",
            notification=GmailNotification(
                email_address="mailbox@example.test", history_id=900, published_at=NOW
            ),
            correlation_id="corr-1",
        )
    )

    assert summary.review_count == 1
    assert summary.candidate_count == 0
    assert repository.audits == ["EXTRACTION_REVIEW_REQUIRED"]
    assert repository.details == [{"reason": "SCHEMA_INVALID", "message_id_hash": ANY_HASH}]


@pytest.mark.asyncio
async def test_extraction_records_a_candidate_without_raw_message_content() -> None:
    from app.domain.ingestion import DisruptionCandidate
    from app.services.gmail_ingestion import GmailIngestionService, IngestGmailNotification

    class Extractor:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def extract(self, *, message, correlation_id):
            self.seen.append(message.id)
            return DisruptionCandidate(
                change_type="flight_delay",
                provider="Example Air",
                booking_reference="AB12CD",
                old_time=NOW,
                new_time=NOW + timedelta(hours=2),
                location_text="BLR",
                confidence=0.9,
                evidence_excerpt="Your flight is delayed.",
            )

    repository = FakeRepository()
    extractor = Extractor()
    service = GmailIngestionService(
        repository=repository, gmail=FakeGmail(), extractor=extractor
    )

    summary = await service.ingest_gmail_notification(
        IngestGmailNotification(
            user_id="u1",
            notification=GmailNotification(
                email_address="mailbox@example.test", history_id=900, published_at=NOW
            ),
            correlation_id="corr-1",
        )
    )

    assert summary.candidate_count == 1
    assert summary.review_count == 0
    assert extractor.seen == ["m1"]
    persisted = repository.events[0].payload
    assert set(persisted) == {"message_id", "history_id"}


@pytest.mark.asyncio
async def test_a_retryable_model_failure_releases_the_claim_for_retry() -> None:
    from app.adapters.errors import RetryableProviderError
    from app.services.gmail_ingestion import GmailIngestionService, IngestGmailNotification

    class OverloadedExtractor:
        async def extract(self, *, message, correlation_id):
            raise RetryableProviderError("Vertex temporarily unavailable")

    repository = FakeRepository()
    service = GmailIngestionService(
        repository=repository, gmail=FakeGmail(), extractor=OverloadedExtractor()
    )
    command = IngestGmailNotification(
        user_id="u1",
        notification=GmailNotification(
            email_address="mailbox@example.test", history_id=900, published_at=NOW
        ),
        correlation_id="corr-1",
    )

    with pytest.raises(RetryableProviderError):
        await service.ingest_gmail_notification(command)

    assert repository.claimed_source_event_keys == set()
