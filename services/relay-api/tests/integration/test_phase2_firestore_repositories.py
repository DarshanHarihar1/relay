from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.contracts import AuditLogEntry, Commitment, Disruption, GmailEvidenceRef, Provenance
from app.repositories.disruptions import FirestoreDisruptionRepository
from app.repositories.firestore import firestore_data, user_document
from app.repositories.retention import FirestoreRetentionStore


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)


def _disruption(user_id: str, disruption_id: str, created_at: datetime = NOW) -> Disruption:
    return Disruption(
        id=disruption_id,
        user_id=user_id,
        source_event_key="gmail:m1:900",
        kind="flight_delay",
        occurred_at=NOW,
        created_at=created_at,
        updated_at=created_at,
        commitment_id="flight_AB12",
        gmail_source=GmailEvidenceRef(message_id="m1", history_id=900),
        evidence_excerpt="Your flight is delayed.",
        model_version="gemini-2.5-flash",
        match_score=100,
        match_reasons=["booking_reference"],
        provenance=Provenance(source="gmail", confidence=0.93),
    )


@pytest.mark.emulator
async def test_a_disruption_is_created_once_and_never_twice(firestore_client) -> None:
    repository = FirestoreDisruptionRepository(firestore_client)
    user_id = f"u-{uuid4().hex}"
    disruption = _disruption(user_id, uuid4().hex)

    assert await repository.create_disruption_if_absent(disruption) is True
    assert await repository.create_disruption_if_absent(disruption) is False


@pytest.mark.emulator
async def test_a_stored_disruption_round_trips_with_its_phase2_fields(firestore_client) -> None:
    repository = FirestoreDisruptionRepository(firestore_client)
    user_id = f"u-{uuid4().hex}"
    disruption = _disruption(user_id, uuid4().hex)
    await repository.create_disruption_if_absent(disruption)

    snapshot = await firestore_client.document(
        user_document(user_id, "disruptions", disruption.id)
    ).get()
    stored = Disruption.model_validate(snapshot.to_dict())

    assert stored.commitment_id == "flight_AB12"
    assert stored.gmail_source.message_id == "m1"
    assert stored.match_reasons == ["booking_reference"]
    assert stored.provenance.confidence == 0.93


@pytest.mark.emulator
async def test_the_commitment_window_query_excludes_commitments_outside_it(firestore_client) -> None:
    repository = FirestoreDisruptionRepository(firestore_client)
    user_id = f"u-{uuid4().hex}"
    for name, starts_at in (
        ("inside", NOW),
        ("before", NOW - timedelta(days=5)),
        ("after", NOW + timedelta(days=5)),
    ):
        commitment = Commitment(
            id=name,
            user_id=user_id,
            source_event_key=f"seed:{name}",
            summary=f"Flight {name}",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
        )
        await firestore_client.document(
            user_document(user_id, "commitments", name)
        ).create(firestore_data(commitment))

    found = await repository.list_commitments_in_window(
        user_id=user_id, start=NOW - timedelta(hours=24), end=NOW + timedelta(hours=72)
    )

    assert [c.id for c in found] == ["inside"]


@pytest.mark.emulator
async def test_retention_strips_raw_evidence_and_is_idempotent(firestore_client) -> None:
    store = FirestoreRetentionStore(firestore_client)
    user_id = f"u-{uuid4().hex}"
    expired = _disruption(user_id, uuid4().hex, created_at=NOW - timedelta(days=45))
    await FirestoreDisruptionRepository(firestore_client).create_disruption_if_absent(expired)

    cutoff = NOW - timedelta(days=30)
    first = await store.purge_older_than(category="raw_evidence", cutoff=cutoff)
    second = await store.purge_older_than(category="raw_evidence", cutoff=cutoff)

    snapshot = await firestore_client.document(
        user_document(user_id, "disruptions", expired.id)
    ).get()
    stored = snapshot.to_dict()

    assert first >= 1
    assert second == 0, "a second purge must find nothing left to strip"
    assert stored["evidence_excerpt"] is None
    assert stored["gmail_source"] is None
    # The disruption itself survives; Phase 3 still needs it.
    assert stored["commitment_id"] == "flight_AB12"
    assert stored["match_score"] == 100


@pytest.mark.emulator
async def test_retention_reports_zero_for_in_process_categories(firestore_client) -> None:
    store = FirestoreRetentionStore(firestore_client)

    for category in ("picker_sessions", "context_cache", "calendar_free_busy",
                     "revoked_token_ciphertext"):
        assert await store.purge_older_than(category=category, cutoff=NOW) == 0


@pytest.mark.emulator
async def test_retention_writes_a_counts_only_audit_entry(firestore_client) -> None:
    store = FirestoreRetentionStore(firestore_client)
    user_id = f"u-{uuid4().hex}"
    entry = AuditLogEntry(
        id=uuid4().hex,
        user_id=user_id,
        outcome="RAW_EVIDENCE_PURGED",
        correlation_id="retention:2026-08-23",
        payload={"category": "raw_evidence", "deleted": "1"},
    )

    await store.append_audit_event(entry)

    snapshot = await firestore_client.document(
        user_document(user_id, "audit_log", entry.id)
    ).get()
    stored = snapshot.to_dict()

    assert stored["payload"] == {"category": "raw_evidence", "deleted": "1"}
    assert "evidence_excerpt" not in stored


@pytest.mark.emulator
async def test_the_disruption_and_its_assessment_command_are_written_atomically(
    firestore_client,
) -> None:
    from app.services.retention import AssessDisruption

    repository = FirestoreDisruptionRepository(firestore_client)
    user_id = f"u-{uuid4().hex}"
    disruption = _disruption(user_id, uuid4().hex)
    assessment = AssessDisruption(
        disruption_id=disruption.id,
        commitment_id="flight_AB12",
        correlation_id="corr-1",
        source_event_key="gmail:m1:900",
    )

    created = await repository.create_disruption_if_absent(disruption, assessment=assessment)

    outbox = await firestore_client.document(
        user_document(user_id, "outbox", disruption.id)
    ).get()

    assert created is True
    assert outbox.exists, "the assessment command must land in the same transaction"
    assert outbox.to_dict()["command"]["disruption_id"] == disruption.id
    assert outbox.to_dict()["published_at"] is None


@pytest.mark.emulator
async def test_a_redelivered_source_event_writes_no_second_outbox_entry(firestore_client) -> None:
    from app.services.retention import AssessDisruption

    repository = FirestoreDisruptionRepository(firestore_client)
    user_id = f"u-{uuid4().hex}"
    disruption = _disruption(user_id, uuid4().hex)
    assessment = AssessDisruption(
        disruption_id=disruption.id,
        commitment_id="flight_AB12",
        correlation_id="corr-1",
        source_event_key="gmail:m1:900",
    )

    first = await repository.create_disruption_if_absent(disruption, assessment=assessment)
    second = await repository.create_disruption_if_absent(disruption, assessment=assessment)

    entries = [
        snapshot
        async for snapshot in firestore_client.collection(f"users/{user_id}/outbox").stream()
    ]

    assert (first, second) == (True, False)
    assert len(entries) == 1


@pytest.mark.emulator
async def test_draining_the_outbox_publishes_each_command_exactly_once(firestore_client) -> None:
    from app.repositories.disruptions import FirestoreOutbox
    from app.services.retention import AssessDisruption

    repository = FirestoreDisruptionRepository(firestore_client)
    user_id = f"u-{uuid4().hex}"
    disruption = _disruption(user_id, uuid4().hex)
    assessment = AssessDisruption(
        disruption_id=disruption.id,
        commitment_id="flight_AB12",
        correlation_id="corr-1",
        source_event_key="gmail:m1:900",
    )
    await repository.create_disruption_if_absent(disruption, assessment=assessment)

    published: list = []

    class Publisher:
        async def publish(self, command) -> None:
            published.append(command)

    outbox = FirestoreOutbox(firestore_client, publisher=Publisher())
    first = await outbox.drain()
    second = await outbox.drain()

    assert first == 1
    assert second == 0, "a drained command must never be published twice"
    assert published[0].disruption_id == disruption.id


@pytest.mark.emulator
async def test_a_failed_publish_leaves_the_command_for_the_next_drain(firestore_client) -> None:
    from app.repositories.disruptions import FirestoreOutbox
    from app.services.retention import AssessDisruption

    repository = FirestoreDisruptionRepository(firestore_client)
    user_id = f"u-{uuid4().hex}"
    disruption = _disruption(user_id, uuid4().hex)
    await repository.create_disruption_if_absent(
        disruption,
        assessment=AssessDisruption(
            disruption_id=disruption.id,
            commitment_id="flight_AB12",
            correlation_id="corr-1",
            source_event_key="gmail:m1:900",
        ),
    )

    class FailingPublisher:
        def __init__(self) -> None:
            self.attempts = 0

        async def publish(self, command) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("Pub/Sub unavailable")

    publisher = FailingPublisher()
    outbox = FirestoreOutbox(firestore_client, publisher=publisher)

    assert await outbox.drain() == 0
    assert await outbox.drain() == 1
    assert publisher.attempts == 2
