from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import Commitment
from app.domain.ingestion import (
    DisruptionCandidate,
    GmailMessage,
    GmailNotification,
    GoogleConnection,
    HistoryPage,
)
from app.security import FernetFieldCipher


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
NEW_TIME = NOW + timedelta(hours=4)


class FakeGmail:
    def __init__(self, message_ids: list[str]) -> None:
        self.message_ids = message_ids

    async def list_history(self, *, connection, start_history_id, page_token=None):
        return HistoryPage(history_id=900, added_message_ids=self.message_ids)

    async def get_message(self, *, connection, message_id):
        return GmailMessage(
            id=message_id,
            thread_id="t1",
            history_id=900,
            internal_date=NOW,
            label_ids=frozenset({"Label_123"}),
            subject="Flight AB12CD delayed",
            from_address="updates@example.test",
            text_body="Your flight AB12CD is delayed.",
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
        self.claims: set[str] = set()
        self.audits: list[str] = []

    async def get_connection(self, user_id):
        return self.connection

    async def get_gmail_cursor(self, *, user_id, mailbox):
        return self.cursor

    async def update_gmail_cursor_if_newer(self, *, user_id, mailbox, proposed_history_id):
        self.cursor = max(self.cursor, proposed_history_id)
        return self.cursor

    async def claim_source_event(self, *, user_id, event):
        if event.source_event_key in self.claims:
            return False
        self.claims.add(event.source_event_key)
        return True

    async def release_source_event_claim(self, *, user_id, source_event_key):
        self.claims.discard(source_event_key)

    async def append_ingestion_audit(
        self, *, user_id, outcome, correlation_id, source_event_key=None, detail=None
    ):
        self.audits.append(outcome)


class FakeCommitments:
    def __init__(self, commitments) -> None:
        self._commitments = commitments

    async def list_commitments_in_window(self, *, user_id, start, end):
        return [c for c in self._commitments if start <= c.starts_at <= end]


class FakeDisruptions:
    def __init__(self) -> None:
        self.disruptions: list = []

    async def create_disruption_if_absent(self, disruption) -> bool:
        if any(existing.id == disruption.id for existing in self.disruptions):
            return False
        self.disruptions.append(disruption)
        return True


class FakePhase3:
    def __init__(self) -> None:
        self.commands: list = []

    async def enqueue_assessment(self, command) -> None:
        self.commands.append(command)


class Extractor:
    def __init__(self, booking_reference: str | None) -> None:
        self._booking_reference = booking_reference

    async def extract(self, *, message, correlation_id):
        return DisruptionCandidate(
            change_type="flight_delay",
            provider="Example Airlines",
            booking_reference=self._booking_reference,
            old_time=NOW,
            new_time=NEW_TIME,
            location_text="Bengaluru",
            confidence=0.93,
            evidence_excerpt="Your flight AB12CD is delayed.",
        )


def _commitment(commitment_id: str, summary: str) -> Commitment:
    return Commitment(
        id=commitment_id,
        user_id="u1",
        source_event_key=f"seed:{commitment_id}",
        summary=summary,
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=3),
    )


class System:
    def __init__(self, *, commitments, booking_reference="AB12CD", message_ids=None):
        from app.services.commitment_matcher import ConservativeCommitmentMatcher
        from app.services.gmail_ingestion import GmailIngestionService

        self.repo = FakeRepository()
        self.disruptions = FakeDisruptions()
        self.phase3 = FakePhase3()
        self.service = GmailIngestionService(
            repository=self.repo,
            gmail=FakeGmail(message_ids or ["m1"]),
            extractor=Extractor(booking_reference),
            matcher=ConservativeCommitmentMatcher(
                commitments=FakeCommitments(commitments),
                disruptions=self.disruptions,
                cipher=FernetFieldCipher(FernetFieldCipher.generate_key()),
                model_version="gemini-2.5-flash",
            ),
            phase3=self.phase3,
        )

    async def ingest(self, history_id: int = 900):
        from app.services.gmail_ingestion import IngestGmailNotification

        return await self.service.ingest_gmail_notification(
            IngestGmailNotification(
                user_id="u1",
                notification=GmailNotification(
                    email_address="mailbox@example.test",
                    history_id=history_id,
                    published_at=NOW,
                ),
                correlation_id="corr-1",
            )
        )


@pytest.mark.asyncio
async def test_match_creates_one_disruption_then_one_phase3_command() -> None:
    from app.services.retention import AssessDisruption

    system = System(commitments=[_commitment("flight_AB12", "Flight AB12CD Example Airlines")])

    await system.ingest()

    assert len(system.disruptions.disruptions) == 1
    assert system.phase3.commands == [
        AssessDisruption(
            disruption_id=system.disruptions.disruptions[0].id,
            commitment_id="flight_AB12",
            correlation_id="corr-1",
            source_event_key="gmail:m1:900",
        )
    ]


@pytest.mark.asyncio
async def test_a_redelivered_notification_never_enqueues_a_second_command() -> None:
    system = System(commitments=[_commitment("flight_AB12", "Flight AB12CD Example Airlines")])

    await system.ingest()
    system.repo.cursor = 800
    await system.ingest()

    assert len(system.disruptions.disruptions) == 1
    assert len(system.phase3.commands) == 1


@pytest.mark.asyncio
async def test_review_never_enqueues_phase3() -> None:
    system = System(
        commitments=[
            _commitment("flight_a", "Example Airlines Bengaluru"),
            _commitment("flight_b", "Example Airlines Bengaluru"),
        ],
        booking_reference=None,
    )

    summary = await system.ingest()

    assert summary.review_count == 1
    assert system.disruptions.disruptions == []
    assert system.phase3.commands == []


@pytest.mark.asyncio
async def test_an_unmatched_message_enqueues_nothing() -> None:
    system = System(commitments=[])

    summary = await system.ingest()

    assert summary.disruption_count == 0
    assert system.phase3.commands == []


@pytest.mark.asyncio
async def test_the_handoff_command_carries_only_identifiers() -> None:
    system = System(commitments=[_commitment("flight_AB12", "Flight AB12CD Example Airlines")])

    await system.ingest()

    payload = system.phase3.commands[0].model_dump()
    assert set(payload) == {
        "disruption_id",
        "commitment_id",
        "correlation_id",
        "source_event_key",
    }
    assert "AB12CD" not in str(payload)
