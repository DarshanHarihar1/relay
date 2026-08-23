from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.contracts import Commitment
from app.domain.impact import (
    ActionKind,
    CandidateChange,
    CandidateKind,
    FeasibilityStatus,
    ImpactAssessment,
    PlanningOptions,
    RepairCandidate,
    RepairScore,
    sha256_id,
)
from app.services.feasibility import is_protected

WEIGHT_MISSED_CRITICAL = 200
WEIGHT_CHANGED_COMMITMENT = 60
WEIGHT_FINANCIAL_COST_INR = 1
WEIGHT_SOCIAL_COORDINATION = 40
WEIGHT_PREFERENCE_VIOLATION = 30
WEIGHT_AVOIDABLE_DELAY_MINUTE = 10

# Only these candidate kinds actually move, cancel, or replace a commitment.
# KEEP_AS_IS and CONFIRM_LATE_ARRIVAL leave the schedule itself untouched.
_SCHEDULE_CHANGING_KINDS = frozenset({CandidateKind.RESCHEDULE, CandidateKind.CANCEL, CandidateKind.REPLACE_TRANSPORT})


def validate_candidate(
    candidate: RepairCandidate,
    assessment: ImpactAssessment,
    commitments: Mapping[str, Commitment],
) -> tuple[str, ...]:
    node_by_id = {node.commitment_id: node for node in assessment.nodes}
    reasons: list[str] = []
    for change in candidate.changes:
        commitment_id = change.commitment_id
        if commitment_id not in assessment.affected_commitment_ids:
            reasons.append(f"{commitment_id} is outside the affected subgraph")
            continue
        commitment = commitments.get(commitment_id)
        if commitment is None:
            continue
        if is_protected(commitment):
            reasons.append(f"{commitment_id} is protected")
            continue
        if change.kind is CandidateKind.CANCEL:
            continue
        start = change.proposed_start if change.proposed_start is not None else commitment.starts_at
        end = change.proposed_end if change.proposed_end is not None else commitment.ends_at
        if commitment.earliest_start is not None and start < commitment.earliest_start:
            reasons.append(f"{commitment_id} starts before earliest_start")
        if commitment.latest_start is not None and start > commitment.latest_start:
            reasons.append(f"{commitment_id} exceeds latest_start")
        if end <= start:
            reasons.append(f"{commitment_id} has nonpositive duration")
        node = node_by_id.get(commitment_id)
        if node is not None and any(trace.constrained_arrival_at is None for trace in node.constraint_traces):
            reasons.append(f"{commitment_id} requires an unknown route")
    return tuple(reasons)


def candidate_is_feasible(
    candidate: RepairCandidate,
    assessment: ImpactAssessment,
    commitments: Mapping[str, Commitment],
) -> bool:
    if validate_candidate(candidate, assessment, commitments):
        return False
    return not any(node.status is FeasibilityStatus.VIOLATED for node in candidate.projected_nodes)


class CandidateFactory:
    """Generates a bounded, finite set of repair candidates. No unconstrained search."""

    def generate(
        self,
        assessment: ImpactAssessment,
        commitments: Mapping[str, Commitment],
        options: PlanningOptions,
    ) -> tuple[RepairCandidate, ...]:
        candidates: list[RepairCandidate] = []
        for commitment_id in assessment.affected_commitment_ids:
            commitment = commitments.get(commitment_id)
            if commitment is None or is_protected(commitment):
                continue
            candidates.append(self._keep_as_is(commitment_id, commitment, assessment, commitments))
            for option in sorted(
                (o for o in options.reschedule_options if o.commitment_id == commitment_id),
                key=lambda item: (item.commitment_id, item.start, item.target_ref),
            ):
                candidates.append(self._reschedule(commitment, option, assessment, commitments))
            if commitment_id in options.cancellable_commitment_ids:
                candidates.append(self._cancel(commitment_id, commitment, assessment, commitments))
            for option in sorted(
                (o for o in options.transport_options if o.commitment_id == commitment_id),
                key=lambda item: (item.commitment_id, item.start, item.target_ref),
            ):
                candidates.append(self._replace_transport(commitment, option, assessment, commitments))
            target_ref = options.late_arrival_target_refs.get(commitment_id)
            if target_ref is not None:
                candidates.append(
                    self._confirm_late_arrival(commitment_id, commitment, target_ref, assessment, commitments)
                )
        return tuple(candidates)

    def _finalize(
        self,
        *,
        kind: CandidateKind,
        change: CandidateChange,
        explanation: str,
        assessment: ImpactAssessment,
        commitments: Mapping[str, Commitment],
    ) -> RepairCandidate:
        candidate_id = sha256_id(
            "candidate",
            {"assessment_id": assessment.id, "kind": kind, "change": change},
        )
        draft = RepairCandidate(
            id=candidate_id,
            kind=kind,
            changes=(change,),
            score=None,
            invalid_reasons=(),
            explanation=explanation,
            projected_nodes=(),
        )
        reasons = validate_candidate(draft, assessment, commitments)
        return draft.model_copy(update={"invalid_reasons": reasons})

    def _keep_as_is(self, commitment_id, commitment, assessment, commitments) -> RepairCandidate:
        change = CandidateChange(commitment_id=commitment_id, kind=CandidateKind.KEEP_AS_IS)
        return self._finalize(
            kind=CandidateKind.KEEP_AS_IS,
            change=change,
            explanation=f"Keep {commitment.summary} as scheduled.",
            assessment=assessment,
            commitments=commitments,
        )

    def _reschedule(self, commitment, option, assessment, commitments) -> RepairCandidate:
        change = CandidateChange(
            commitment_id=option.commitment_id,
            kind=CandidateKind.RESCHEDULE,
            proposed_start=option.start,
            proposed_end=option.end,
            financial_cost_inr=option.max_fee_inr,
            action_kinds=(ActionKind.CALL_VENUE,),
        )
        return self._finalize(
            kind=CandidateKind.RESCHEDULE,
            change=change,
            explanation=f"Reschedule {commitment.summary} to {option.start.isoformat()}.",
            assessment=assessment,
            commitments=commitments,
        )

    def _cancel(self, commitment_id, commitment, assessment, commitments) -> RepairCandidate:
        change = CandidateChange(
            commitment_id=commitment_id, kind=CandidateKind.CANCEL, action_kinds=(ActionKind.CALL_VENUE,)
        )
        return self._finalize(
            kind=CandidateKind.CANCEL,
            change=change,
            explanation=f"Cancel {commitment.summary}.",
            assessment=assessment,
            commitments=commitments,
        )

    def _replace_transport(self, commitment, option, assessment, commitments) -> RepairCandidate:
        change = CandidateChange(
            commitment_id=option.commitment_id,
            kind=CandidateKind.REPLACE_TRANSPORT,
            proposed_start=option.start,
            proposed_end=option.end,
            financial_cost_inr=option.cost_inr,
            action_kinds=(ActionKind.OPEN_UBER_HANDOFF,),
        )
        return self._finalize(
            kind=CandidateKind.REPLACE_TRANSPORT,
            change=change,
            explanation=f"Arrange replacement transport for {commitment.summary}.",
            assessment=assessment,
            commitments=commitments,
        )

    def _confirm_late_arrival(self, commitment_id, commitment, target_ref, assessment, commitments) -> RepairCandidate:
        change = CandidateChange(
            commitment_id=commitment_id,
            kind=CandidateKind.CONFIRM_LATE_ARRIVAL,
            action_kinds=(ActionKind.CALL_VENUE,),
        )
        return self._finalize(
            kind=CandidateKind.CONFIRM_LATE_ARRIVAL,
            change=change,
            explanation=f"Confirm late arrival for {commitment.summary} at {target_ref}.",
            assessment=assessment,
            commitments=commitments,
        )


def _avoidable_delay_minutes(candidate: RepairCandidate, assessment: ImpactAssessment) -> int:
    original_by_id = {node.commitment_id: node for node in assessment.nodes}
    total = 0
    for projected in candidate.projected_nodes:
        original = original_by_id.get(projected.commitment_id)
        if original is None:
            continue
        delta_minutes = (projected.effective_start - original.effective_start).total_seconds() / 60
        if delta_minutes > 0:
            total += int(delta_minutes)
    return total


def score_candidate(
    candidate: RepairCandidate,
    assessment: ImpactAssessment,
    commitments: Mapping[str, Commitment],
) -> RepairScore:
    invariant_violations = len(validate_candidate(candidate, assessment, commitments))
    missed_critical_commitments = sum(
        1
        for node in candidate.projected_nodes
        if node.status is FeasibilityStatus.VIOLATED
        and (commitment := commitments.get(node.commitment_id)) is not None
        and commitment.criticality == "CRITICAL"
    )
    changed_commitments = len(
        {change.commitment_id for change in candidate.changes if change.kind in _SCHEDULE_CHANGING_KINDS}
    )
    financial_cost_inr = sum(change.financial_cost_inr for change in candidate.changes)
    social_coordination_units = sum(change.social_coordination_units for change in candidate.changes)
    preference_violation_units = sum(change.preference_violation_units for change in candidate.changes)
    avoidable_delay_minutes = _avoidable_delay_minutes(candidate, assessment)
    weighted_total = (
        WEIGHT_MISSED_CRITICAL * missed_critical_commitments
        + WEIGHT_CHANGED_COMMITMENT * changed_commitments
        + WEIGHT_FINANCIAL_COST_INR * financial_cost_inr
        + WEIGHT_SOCIAL_COORDINATION * social_coordination_units
        + WEIGHT_PREFERENCE_VIOLATION * preference_violation_units
        + WEIGHT_AVOIDABLE_DELAY_MINUTE * avoidable_delay_minutes
    )
    return RepairScore(
        invariant_violations=invariant_violations,
        missed_critical_commitments=missed_critical_commitments,
        changed_commitments=changed_commitments,
        financial_cost_inr=financial_cost_inr,
        social_coordination_units=social_coordination_units,
        preference_violation_units=preference_violation_units,
        avoidable_delay_minutes=avoidable_delay_minutes,
        weighted_total=weighted_total,
    )


def select_candidate(candidates: Sequence[RepairCandidate]) -> RepairCandidate | None:
    eligible = [
        candidate
        for candidate in candidates
        if candidate.score is not None and not candidate.invalid_reasons and candidate.score.invariant_violations == 0
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda candidate: candidate.score.sort_key(candidate.id))
