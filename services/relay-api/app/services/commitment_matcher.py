from __future__ import annotations

import re
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Protocol

from app.contracts import Commitment, Disruption, GmailEvidenceRef, Provenance
from app.domain.ingestion import DisruptionCandidate, GmailMessage, MatchResult
from app.security import FieldCipher
from app.services.retention import AssessDisruption


LOOKBACK = timedelta(hours=24)
LOOKAHEAD = timedelta(hours=72)
START_TOLERANCE = timedelta(hours=6)

# Deliberately strict. Without a booking reference every remaining signal must
# agree to reach MINIMUM_SCORE, and the runner-up must be clearly behind.
BOOKING_REFERENCE_SCORE = 100
PROVIDER_SCORE = 25
LOCATION_SCORE = 20
START_TIME_SCORE = 25
MINIMUM_SCORE = 70
MINIMUM_MARGIN = 15

# Words that carry no identity, so they must never be the reason two records match.
_STOP_WORDS = frozenset(
    {
        "a", "air", "airline", "airlines", "airways", "an", "and", "at", "co", "company",
        "corp", "flight", "for", "from", "in", "inc", "limited", "ltd", "of", "on", "the",
        "to", "train", "trip", "with",
    }
)


class CommitmentStore(Protocol):
    async def list_commitments_in_window(
        self, *, user_id: str, start: datetime, end: datetime
    ) -> list[Commitment]: ...


class DisruptionStore(Protocol):
    async def create_disruption_if_absent(
        self, disruption: Disruption, *, assessment: AssessDisruption | None = None
    ) -> bool: ...


def normalize_tokens(value: str | None) -> frozenset[str]:
    """Split text into comparable identity tokens. No model is involved."""
    if not value:
        return frozenset()
    tokens = re.split(r"[^0-9a-z]+", value.casefold())
    return frozenset(token for token in tokens if token and token not in _STOP_WORDS)


def normalize_booking_reference(value: str | None) -> str | None:
    """Booking references differ only by case, spacing, and punctuation."""
    if not value:
        return None
    normalized = re.sub(r"[^0-9A-Z]", "", value.upper())
    return normalized or None


def score_commitment(
    candidate: DisruptionCandidate, commitment: Commitment
) -> tuple[int, list[str]]:
    """Score one commitment against one candidate. Deterministic and explainable."""
    summary_tokens = normalize_tokens(commitment.summary)
    reference = normalize_booking_reference(candidate.booking_reference)
    if reference is not None and reference in {
        normalize_booking_reference(token) for token in summary_tokens
    }:
        return BOOKING_REFERENCE_SCORE, ["booking_reference"]

    score = 0
    reasons: list[str] = []
    changed_time = candidate.new_time or candidate.old_time
    if changed_time is not None and abs(commitment.starts_at - changed_time) <= START_TOLERANCE:
        score += START_TIME_SCORE
        reasons.append("planned_start_within_6_hours")
    if normalize_tokens(candidate.provider) & summary_tokens:
        score += PROVIDER_SCORE
        reasons.append("provider")
    if normalize_tokens(candidate.location_text) & summary_tokens:
        score += LOCATION_SCORE
        reasons.append("location")
    return score, reasons


class ConservativeCommitmentMatcher:
    """Matches only on strong evidence and never mutates a commitment.

    Anything short of decisive evidence becomes `needs_review`, which creates no
    disruption and leaves every commitment untouched.
    """

    def __init__(
        self,
        *,
        commitments: CommitmentStore,
        disruptions: DisruptionStore,
        cipher: FieldCipher,
        model_version: str,
    ) -> None:
        self._commitments = commitments
        self._disruptions = disruptions
        self._cipher = cipher
        self._model_version = model_version

    async def match(
        self, *, user_id: str, candidate: DisruptionCandidate, received_at: datetime
    ) -> MatchResult:
        del received_at
        window = _window(candidate)
        if window is None:
            # A change with no stated time cannot be placed against a plan.
            return MatchResult(status="needs_review", score=0, reasons=["no_changed_time"])

        start, end = window
        commitments = await self._commitments.list_commitments_in_window(
            user_id=user_id, start=start, end=end
        )
        if not commitments:
            return MatchResult(status="no_match", score=0, reasons=["no_commitment_in_window"])

        scored = [(commitment, *score_commitment(candidate, commitment)) for commitment in commitments]
        referenced = [entry for entry in scored if entry[1] == BOOKING_REFERENCE_SCORE]
        if len(referenced) > 1:
            return MatchResult(
                status="needs_review",
                score=BOOKING_REFERENCE_SCORE,
                reasons=["duplicate_booking_reference"],
            )
        if len(referenced) == 1:
            commitment, score, reasons = referenced[0]
            return MatchResult(
                status="matched", commitment_id=commitment.id, score=score, reasons=reasons
            )

        scored.sort(key=lambda entry: entry[1], reverse=True)
        commitment, score, reasons = scored[0]
        runner_up = scored[1][1] if len(scored) > 1 else 0
        if score < MINIMUM_SCORE:
            if score == 0:
                return MatchResult(status="no_match", score=0, reasons=["no_shared_evidence"])
            return MatchResult(status="needs_review", score=score, reasons=[*reasons, "weak_score"])
        if score - runner_up < MINIMUM_MARGIN:
            return MatchResult(status="needs_review", score=score, reasons=["ambiguous_match"])
        return MatchResult(
            status="matched", commitment_id=commitment.id, score=score, reasons=reasons
        )

    async def create_disruption_from_match(
        self,
        *,
        user_id: str,
        message: GmailMessage,
        candidate: DisruptionCandidate,
        match: MatchResult,
        source_event_key: str,
        correlation_id: str,
    ) -> bool:
        """Persist one immutable disruption. A review result persists nothing."""
        if match.status != "matched" or match.commitment_id is None:
            return False
        booking_reference = normalize_booking_reference(candidate.booking_reference)
        identifier = disruption_id(source_event_key, match.commitment_id)
        disruption = Disruption(
            id=identifier,
            user_id=user_id,
            source_event_key=source_event_key,
            kind=candidate.change_type,
            occurred_at=message.internal_date,
            correlation_id=correlation_id,
            commitment_id=match.commitment_id,
            gmail_source=GmailEvidenceRef(message_id=message.id, history_id=message.history_id),
            provider=candidate.provider,
            encrypted_booking_reference=(
                self._cipher.encrypt(booking_reference) if booking_reference else None
            ),
            previous_time=candidate.old_time,
            new_time=candidate.new_time,
            location_text=candidate.location_text,
            evidence_excerpt=candidate.evidence_excerpt,
            model_version=self._model_version,
            match_score=match.score,
            match_reasons=list(match.reasons),
            provenance=Provenance(source="gmail", confidence=candidate.confidence),
        )
        # The Phase 3 command is written in the same transaction as the disruption.
        return await self._disruptions.create_disruption_if_absent(
            disruption,
            assessment=AssessDisruption(
                disruption_id=identifier,
                commitment_id=match.commitment_id,
                correlation_id=correlation_id,
                source_event_key=source_event_key,
            ),
        )


def disruption_id(source_event_key: str, commitment_id: str) -> str:
    """One source event plus one commitment always yields the same disruption ID."""
    return sha256(f"{source_event_key}|{commitment_id}".encode("utf-8")).hexdigest()


def _window(candidate: DisruptionCandidate) -> tuple[datetime, datetime] | None:
    times = [time for time in (candidate.old_time, candidate.new_time) if time is not None]
    if not times:
        return None
    return min(times) - LOOKBACK, max(times) + LOOKAHEAD


__all__ = [
    "ConservativeCommitmentMatcher",
    "disruption_id",
    "normalize_booking_reference",
    "normalize_tokens",
    "score_commitment",
]
