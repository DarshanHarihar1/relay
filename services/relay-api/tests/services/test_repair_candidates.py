from datetime import datetime, timezone

from app.contracts import Commitment
from app.domain.impact import (
    CandidateChange,
    CandidateKind,
    ConstraintTrace,
    FeasibilityStatus,
    ImpactAssessment,
    ImpactNode,
    PlanningOptions,
    RepairCandidate,
    RescheduleOption,
)
from app.services.repair_candidates import CandidateFactory, candidate_is_feasible, validate_candidate

USER_ID = "u1"
NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def commitment(commitment_id, *, start, end, earliest=None, latest=None, flexibility=None) -> Commitment:
    return Commitment(
        id=commitment_id,
        user_id=USER_ID,
        source_event_key=f"seed:{commitment_id}",
        summary=commitment_id,
        starts_at=parse(start),
        ends_at=parse(end),
        earliest_start=parse(earliest) if earliest else None,
        latest_start=parse(latest) if latest else None,
        flexibility=flexibility,
    )


DINNER = commitment(
    "dinner",
    start="2026-08-22T22:00:00Z",
    end="2026-08-22T23:00:00Z",
    earliest="2026-08-22T21:30:00Z",
    latest="2026-08-22T22:30:00Z",
)
INTERVIEW = commitment(
    "interview", start="2026-08-23T09:00:00Z", end="2026-08-23T09:30:00Z", flexibility="NEVER_MOVE"
)
UNRELATED = commitment("unrelated", start="2026-08-24T09:00:00Z", end="2026-08-24T09:30:00Z")
COMMITMENTS = {"dinner": DINNER, "interview": INTERVIEW, "unrelated": UNRELATED}


def _node(commitment_id: str, *, traces: tuple[ConstraintTrace, ...] = ()) -> ImpactNode:
    return ImpactNode(
        commitment_id=commitment_id,
        effective_start=COMMITMENTS[commitment_id].starts_at,
        effective_end=COMMITMENTS[commitment_id].ends_at,
        earliest_feasible_start=None,
        latest_start=COMMITMENTS[commitment_id].latest_start or COMMITMENTS[commitment_id].ends_at,
        slack_minutes=None,
        status=FeasibilityStatus.FEASIBLE,
        constraint_traces=traces,
    )


ASSESSMENT = ImpactAssessment(
    id="assessment_1",
    disruption_id="disruption_1",
    source_commitment_id="flight",
    reachable_commitment_ids=("flight", "dinner", "interview"),
    affected_commitment_ids=("dinner", "interview"),
    nodes=(_node("dinner"), _node("interview")),
    diagnostics=(),
    input_fingerprint="fp1",
)

_UNKNOWN_ROUTE_TRACE = ConstraintTrace(
    edge_id="e1",
    source_id="flight",
    target_id="dinner",
    release_at=NOW,
    route_minutes=None,
    constrained_arrival_at=None,
    status=FeasibilityStatus.AT_RISK,
    reason="route snapshot unavailable",
)

ASSESSMENT_WITH_UNKNOWN_ROUTE = ASSESSMENT.model_copy(
    update={"nodes": (_node("dinner", traces=(_UNKNOWN_ROUTE_TRACE,)), _node("interview"))}
)

OPTIONS_WITH_INTERVIEW_SLOT = PlanningOptions(
    reschedule_options=(
        RescheduleOption(
            commitment_id="interview",
            start=parse("2026-08-23T10:00:00Z"),
            end=parse("2026-08-23T10:30:00Z"),
            target_ref="interview_desk",
            max_fee_inr=0,
        ),
        RescheduleOption(
            commitment_id="unrelated",
            start=parse("2026-08-24T10:00:00Z"),
            end=parse("2026-08-24T10:30:00Z"),
            target_ref="somewhere",
            max_fee_inr=0,
        ),
    ),
    approval_expires_at=NOW,
)


def candidate_reschedule(commitment_id: str, start: str, end: str) -> RepairCandidate:
    return RepairCandidate(
        id=f"candidate-{commitment_id}-reschedule",
        kind=CandidateKind.RESCHEDULE,
        changes=(
            CandidateChange(
                commitment_id=commitment_id,
                kind=CandidateKind.RESCHEDULE,
                proposed_start=parse(start),
                proposed_end=parse(end),
            ),
        ),
        invalid_reasons=(),
        explanation="test reschedule",
    )


def candidate_keep_as_is(commitment_id: str) -> RepairCandidate:
    return RepairCandidate(
        id=f"candidate-{commitment_id}-keep",
        kind=CandidateKind.KEEP_AS_IS,
        changes=(CandidateChange(commitment_id=commitment_id, kind=CandidateKind.KEEP_AS_IS),),
        invalid_reasons=(),
        explanation="test keep",
    )


def test_factory_never_generates_a_change_for_unreachable_or_protected_commitment() -> None:
    candidates = CandidateFactory().generate(ASSESSMENT, COMMITMENTS, OPTIONS_WITH_INTERVIEW_SLOT)
    assert all(change.commitment_id != "interview" for candidate in candidates for change in candidate.changes)
    assert all(change.commitment_id != "unrelated" for candidate in candidates for change in candidate.changes)


def test_reschedule_after_latest_start_is_invalid() -> None:
    candidate = candidate_reschedule("dinner", "2026-08-22T23:15:00Z", "2026-08-23T00:15:00Z")
    assert "dinner exceeds latest_start" in validate_candidate(candidate, ASSESSMENT, COMMITMENTS)


def test_unknown_route_prevents_candidate_that_requires_that_route() -> None:
    candidate = candidate_keep_as_is("dinner")
    assert candidate_is_feasible(candidate, ASSESSMENT_WITH_UNKNOWN_ROUTE, COMMITMENTS) is False


def test_valid_reschedule_within_window_has_no_reasons() -> None:
    candidate = candidate_reschedule("dinner", "2026-08-22T21:45:00Z", "2026-08-22T22:45:00Z")
    assert validate_candidate(candidate, ASSESSMENT, COMMITMENTS) == ()
    assert candidate_is_feasible(candidate, ASSESSMENT, COMMITMENTS) is True
