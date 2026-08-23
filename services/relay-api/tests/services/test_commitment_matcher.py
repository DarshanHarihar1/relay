from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import Commitment
from app.domain.ingestion import DisruptionCandidate, GmailMessage, MatchResult
from app.security import FernetFieldCipher


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
OLD_TIME = datetime(2026, 8, 24, 3, 30, tzinfo=timezone.utc)
NEW_TIME = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)


def _commitment(commitment_id: str, summary: str, starts_at: datetime) -> Commitment:
    return Commitment(
        id=commitment_id,
        user_id="u1",
        source_event_key=f"seed:{commitment_id}",
        summary=summary,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=3),
    )


def _candidate(**overrides) -> DisruptionCandidate:
    payload = {
        "change_type": "flight_delay",
        "provider": "Example Airlines",
        "booking_reference": "AB12CD",
        "old_time": OLD_TIME,
        "new_time": NEW_TIME,
        "location_text": "Bengaluru",
        "confidence": 0.92,
        "evidence_excerpt": "Your flight is delayed to 09:00.",
    }
    payload.update(overrides)
    return DisruptionCandidate(**payload)


def _message() -> GmailMessage:
    return GmailMessage(
        id="m1",
        thread_id="t1",
        history_id=900,
        internal_date=NOW,
        label_ids=frozenset({"Label_123"}),
        subject="Flight delayed",
        from_address="updates@example.test",
        text_body="Your flight is delayed to 09:00.",
    )


class FakeCommitments:
    def __init__(self, commitments: list[Commitment]) -> None:
        self.commitments = commitments
        self.windows: list[tuple[datetime, datetime]] = []
        self.mutations: list[str] = []

    async def list_commitments_in_window(self, *, user_id, start, end):
        self.windows.append((start, end))
        return [c for c in self.commitments if start <= c.starts_at <= end]

    async def update(self, commitment):
        self.mutations.append(commitment.id)


class FakeDisruptions:
    def __init__(self) -> None:
        self.disruptions: list = []

    async def create_disruption_if_absent(self, disruption) -> bool:
        if any(existing.id == disruption.id for existing in self.disruptions):
            return False
        self.disruptions.append(disruption)
        return True


def _service(commitments, disruptions=None):
    from app.services.commitment_matcher import ConservativeCommitmentMatcher

    return ConservativeCommitmentMatcher(
        commitments=commitments,
        disruptions=disruptions or FakeDisruptions(),
        cipher=FernetFieldCipher(FernetFieldCipher.generate_key()),
        model_version="gemini-2.5-flash",
    )


@pytest.mark.asyncio
async def test_booking_reference_wins() -> None:
    commitments = FakeCommitments(
        [
            _commitment("flight_AB12", "Flight AB12CD Example Airlines Bengaluru", OLD_TIME),
            _commitment("dinner_1", "Dinner in Bengaluru", NEW_TIME + timedelta(hours=5)),
        ]
    )

    result = await _service(commitments).match(user_id="u1", candidate=_candidate(), received_at=NOW)

    assert result == MatchResult(
        status="matched", commitment_id="flight_AB12", score=100, reasons=["booking_reference"]
    )


@pytest.mark.asyncio
async def test_the_query_window_spans_24_hours_before_and_72_hours_after() -> None:
    commitments = FakeCommitments([])

    await _service(commitments).match(user_id="u1", candidate=_candidate(), received_at=NOW)

    assert commitments.windows == [(OLD_TIME - timedelta(hours=24), NEW_TIME + timedelta(hours=72))]


@pytest.mark.asyncio
async def test_a_duplicate_booking_reference_requires_review() -> None:
    commitments = FakeCommitments(
        [
            _commitment("flight_a", "Flight AB12CD outbound", OLD_TIME),
            _commitment("flight_b", "Flight AB12CD return", OLD_TIME + timedelta(hours=2)),
        ]
    )

    result = await _service(commitments).match(user_id="u1", candidate=_candidate(), received_at=NOW)

    assert result.status == "needs_review"
    assert result.commitment_id is None
    assert result.reasons == ["duplicate_booking_reference"]


@pytest.mark.asyncio
async def test_ambiguous_match_requires_review() -> None:
    commitments = FakeCommitments(
        [
            _commitment("flight_a", "Example Airlines Bengaluru departure", OLD_TIME),
            _commitment("flight_b", "Example Airlines Bengaluru departure", OLD_TIME),
        ]
    )

    result = await _service(commitments).match(
        user_id="u1", candidate=_candidate(booking_reference=None), received_at=NOW
    )

    assert result.status == "needs_review"
    assert result.commitment_id is None
    assert commitments.mutations == []


@pytest.mark.asyncio
async def test_a_near_date_alone_is_never_a_match() -> None:
    commitments = FakeCommitments([_commitment("dentist", "Dentist appointment", OLD_TIME)])

    result = await _service(commitments).match(
        user_id="u1",
        candidate=_candidate(booking_reference=None, provider=None, location_text=None),
        received_at=NOW,
    )

    assert result.status != "matched"
    assert commitments.mutations == []


@pytest.mark.asyncio
async def test_full_evidence_without_a_reference_matches_at_seventy() -> None:
    commitments = FakeCommitments(
        [
            _commitment("flight_a", "Example Airlines to Bengaluru", OLD_TIME),
            _commitment("dinner_1", "Dinner reservation", NEW_TIME + timedelta(hours=40)),
        ]
    )

    result = await _service(commitments).match(
        user_id="u1", candidate=_candidate(booking_reference=None), received_at=NOW
    )

    assert result.status == "matched"
    assert result.commitment_id == "flight_a"
    assert result.score == 70
    assert result.reasons == ["planned_start_within_6_hours", "provider", "location"]


@pytest.mark.asyncio
async def test_a_candidate_without_any_changed_time_requires_review() -> None:
    commitments = FakeCommitments([_commitment("flight_a", "Flight AB12CD", OLD_TIME)])

    result = await _service(commitments).match(
        user_id="u1",
        candidate=_candidate(change_type="other", old_time=None, new_time=None),
        received_at=NOW,
    )

    assert result.status == "needs_review"
    assert result.reasons == ["no_changed_time"]
    assert commitments.windows == []


@pytest.mark.asyncio
async def test_event_key_cannot_create_a_second_disruption() -> None:
    disruptions = FakeDisruptions()
    service = _service(FakeCommitments([]), disruptions)
    inputs = dict(
        user_id="u1",
        message=_message(),
        candidate=_candidate(),
        match=MatchResult(
            status="matched", commitment_id="flight_AB12", score=100, reasons=["booking_reference"]
        ),
        source_event_key="gmail:m1:900",
        correlation_id="corr-1",
    )

    assert await service.create_disruption_from_match(**inputs) is True
    assert await service.create_disruption_from_match(**inputs) is False
    assert len(disruptions.disruptions) == 1


@pytest.mark.asyncio
async def test_a_created_disruption_carries_redacted_provenance_and_scoring() -> None:
    disruptions = FakeDisruptions()
    cipher = FernetFieldCipher(FernetFieldCipher.generate_key())
    from app.services.commitment_matcher import ConservativeCommitmentMatcher

    service = ConservativeCommitmentMatcher(
        commitments=FakeCommitments([]),
        disruptions=disruptions,
        cipher=cipher,
        model_version="gemini-2.5-flash",
    )

    await service.create_disruption_from_match(
        user_id="u1",
        message=_message(),
        candidate=_candidate(),
        match=MatchResult(
            status="matched", commitment_id="flight_AB12", score=100, reasons=["booking_reference"]
        ),
        source_event_key="gmail:m1:900",
        correlation_id="corr-1",
    )

    disruption = disruptions.disruptions[0]
    assert disruption.commitment_id == "flight_AB12"
    assert disruption.kind == "flight_delay"
    assert disruption.source_event_key == "gmail:m1:900"
    assert disruption.gmail_source.message_id == "m1"
    assert disruption.gmail_source.history_id == 900
    assert disruption.model_version == "gemini-2.5-flash"
    assert disruption.match_score == 100
    assert disruption.match_reasons == ["booking_reference"]
    assert disruption.provenance.source == "gmail"
    assert disruption.provenance.confidence == 0.92
    assert disruption.new_time == NEW_TIME
    assert "AB12CD" not in disruption.encrypted_booking_reference
    assert cipher.decrypt(disruption.encrypted_booking_reference) == "AB12CD"


@pytest.mark.asyncio
async def test_a_review_result_cannot_create_a_disruption() -> None:
    disruptions = FakeDisruptions()
    service = _service(FakeCommitments([]), disruptions)

    created = await service.create_disruption_from_match(
        user_id="u1",
        message=_message(),
        candidate=_candidate(),
        match=MatchResult(status="needs_review", score=40, reasons=["weak_score"]),
        source_event_key="gmail:m1:900",
        correlation_id="corr-1",
    )

    assert created is False
    assert disruptions.disruptions == []


def test_disruption_round_trips_through_serialization() -> None:
    from app.contracts import Disruption

    disruption = Disruption(
        id="d1",
        user_id="u1",
        source_event_key="gmail:m1:900",
        kind="flight_delay",
        occurred_at=NOW,
    )

    assert Disruption.model_validate(disruption.model_dump(mode="json")) == disruption
    assert disruption.commitment_id is None
    assert disruption.match_reasons == []
