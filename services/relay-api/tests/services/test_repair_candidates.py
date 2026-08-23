from datetime import datetime, timedelta, timezone

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
    RepairScore,
    RescheduleOption,
)
from app.services.repair_candidates import (
    CandidateFactory,
    candidate_is_feasible,
    score_candidate,
    select_candidate,
    validate_candidate,
)

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


def candidate_with(*, cost: int, social: int, delay: int) -> RepairCandidate:
    dinner_node = next(node for node in ASSESSMENT.nodes if node.commitment_id == "dinner")
    delayed_node = dinner_node.model_copy(update={"effective_start": dinner_node.effective_start + timedelta(minutes=delay)})
    return RepairCandidate(
        id="candidate-dinner-confirm",
        kind=CandidateKind.CONFIRM_LATE_ARRIVAL,
        changes=(
            CandidateChange(
                commitment_id="dinner",
                kind=CandidateKind.CONFIRM_LATE_ARRIVAL,
                financial_cost_inr=cost,
                social_coordination_units=social,
            ),
        ),
        invalid_reasons=(),
        explanation="test confirm",
        projected_nodes=(delayed_node,),
    )


def candidate_with_id(candidate_id: str, *, weighted_total: int = 0, invariant_violations: int = 0) -> RepairCandidate:
    score = RepairScore(
        invariant_violations=invariant_violations,
        missed_critical_commitments=0,
        changed_commitments=0,
        financial_cost_inr=0,
        social_coordination_units=0,
        preference_violation_units=0,
        avoidable_delay_minutes=0,
        weighted_total=weighted_total,
    )
    return RepairCandidate(
        id=candidate_id,
        kind=CandidateKind.KEEP_AS_IS,
        changes=(CandidateChange(commitment_id="dinner", kind=CandidateKind.KEEP_AS_IS),),
        score=score,
        invalid_reasons=(),
        explanation="test",
        projected_nodes=(),
    )


def test_score_uses_published_weights_after_hard_constraint_filter() -> None:
    score = score_candidate(candidate_with(cost=100, social=1, delay=5), ASSESSMENT, COMMITMENTS)
    assert score.weighted_total == 100 + 40 + 50
    assert score.invariant_violations == 0


def test_selection_uses_lexical_score_then_candidate_id() -> None:
    later_id = candidate_with_id("candidate_b", weighted_total=50)
    earlier_id = candidate_with_id("candidate_a", weighted_total=50)
    assert select_candidate((later_id, earlier_id)).id == "candidate_a"


def test_candidate_with_an_invariant_violation_cannot_win() -> None:
    unsafe = candidate_with_id("candidate_a", invariant_violations=1, weighted_total=0)
    safe = candidate_with_id("candidate_b", invariant_violations=0, weighted_total=999)
    assert select_candidate((unsafe, safe)).id == "candidate_b"


def test_select_candidate_returns_none_when_nothing_is_eligible() -> None:
    unsafe = candidate_with_id("candidate_a", invariant_violations=1)
    assert select_candidate((unsafe,)) is None
