from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.contracts import ActionRecord, ActionState
from app.domain.product import (
    ActionOutcomeView,
    ApprovalActionSummary,
    ApprovalBatchView,
    DashboardView,
    OutcomeStatus,
    PlanTimelineItem,
    TimelineStatus,
)
from app.repositories.product import DashboardSource, ProductRepository


class DashboardProjectionService:
    def __init__(self, repository: ProductRepository, now: Callable[[], datetime] | None = None) -> None:
        self._repository = repository
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def build_dashboard(self, *, user_id: str) -> DashboardView:
        source = await self._repository.get_dashboard_source(user_id=user_id)
        if source is None:
            return DashboardView(
                repair_plan_id="none",
                repair_plan_version=1,
                generated_at=_utc(self._now()),
                timeline=(),
                approval=None,
                outcomes=(),
                last_event_id=None,
            )

        changed_ids = _changed_commitment_ids(source)
        verified_ids = _verified_commitment_ids(source.actions, changed_ids)
        affected_ids = set(source.assessment.affected_commitment_ids) if source.assessment else changed_ids
        timeline = tuple(
            _timeline_item(commitment, affected_ids, changed_ids, verified_ids)
            for commitment in source.commitments
            if not source.assessment or commitment.id in source.assessment.reachable_commitment_ids
        )
        approval = _approval_view(source)
        outcomes = tuple(_action_outcome(action) for action in sorted(source.actions, key=lambda item: (item.updated_at, item.id)))
        last_event_id = max((audit.id for audit in source.audits), default=None)
        return DashboardView(
            repair_plan_id=source.plan.id,
            repair_plan_version=source.plan.version,
            generated_at=_utc(source.generated_at),
            timeline=timeline,
            approval=approval,
            outcomes=outcomes,
            last_event_id=last_event_id,
        )


def _changed_commitment_ids(source: DashboardSource) -> set[str]:
    if source.plan.selected_candidate_id is None:
        return set()
    for candidate in source.plan.candidates:
        if candidate.id == source.plan.selected_candidate_id:
            return {change.commitment_id for change in candidate.changes}
    return set()


def _verified_commitment_ids(actions: tuple[ActionRecord, ...], changed_ids: set[str]) -> set[str]:
    verified: set[str] = set()
    for action in actions:
        if action.state is not ActionState.VERIFIED:
            continue
        for commitment_id in changed_ids:
            if commitment_id in action.target_ref or commitment_id in action.repair_plan_id:
                verified.add(commitment_id)
    return verified


def _timeline_item(commitment, affected_ids: set[str], changed_ids: set[str], verified_ids: set[str]) -> PlanTimelineItem:
    if commitment.protected:
        status = TimelineStatus.PROTECTED
        explanation = "This commitment is protected from automated changes."
    elif commitment.id in verified_ids:
        status = TimelineStatus.REPAIRED
        explanation = "Relay verified the approved repair for this commitment."
    elif commitment.id in affected_ids or commitment.id in changed_ids:
        status = TimelineStatus.AT_RISK
        explanation = "Relay identified a timing change that may affect this commitment."
    else:
        status = TimelineStatus.CHANGED
        explanation = "This commitment is part of the current travel plan."
    return PlanTimelineItem(
        commitment_id=commitment.id,
        title=commitment.summary[:140],
        starts_at=_utc(commitment.starts_at),
        ends_at=_utc(commitment.ends_at),
        status=status,
        explanation=explanation,
        is_pickup_prompt=(commitment.type or "").lower() in {"pickup", "transport"},
        pickup_version=commitment.version if (commitment.type or "").lower() in {"pickup", "transport"} else None,
    )


def _approval_view(source: DashboardSource) -> ApprovalBatchView | None:
    approval = source.approval
    if approval is None:
        return None
    actions = tuple(
        _approval_action(action)
        for action in source.actions
        if action.id in set(approval.action_ids)
    )
    expiry_candidates = [item.expires_at for item in actions if item.expires_at is not None]
    expires_at = _utc(approval.expires_at or max(expiry_candidates, default=source.generated_at))
    state = approval.state
    if expires_at <= _utc(datetime.now(timezone.utc)) and state == "awaiting_approval":
        state = "expired"
    reason = {
        "awaiting_approval": "Review and approve these limited actions.",
        "approved": "This action batch was approved.",
        "declined": "This action batch was declined.",
        "expired": "Approval expired. Review a fresh plan.",
    }[state]
    return ApprovalBatchView(
        approval_id=approval.id,
        version=approval.version,
        state=state,
        expires_at=expires_at,
        reason=reason,
        actions=actions,
    )


def _approval_action(action: ActionRecord) -> ApprovalActionSummary:
    snapshot = action.authorization_snapshot
    if snapshot.type == "voice_call":
        return ApprovalActionSummary(
            action_id=action.id,
            kind=action.type,
            goal=snapshot.goal,
            authorized_options=tuple(snapshot.authorized_options),
            max_fee_inr=snapshot.max_fee_inr,
            expires_at=_utc(snapshot.expires_at),
            disclosure=snapshot.identity_disclosure,
            must_not=tuple(snapshot.must_not),
        )
    if snapshot.type == "calendar_hold":
        return ApprovalActionSummary(
            action_id=action.id,
            kind=action.type,
            goal="Private Calendar hold",
            authorized_options=(),
            max_fee_inr=0,
            expires_at=None,
            disclosure=None,
            must_not=("change event visibility",),
        )
    return ApprovalActionSummary(
        action_id=action.id,
        kind=action.type,
        goal="Open Uber with this trip",
        authorized_options=(),
        max_fee_inr=0,
        expires_at=None,
        disclosure=None,
        must_not=("book or pay for a ride",),
    )


def _action_outcome(action: ActionRecord) -> ActionOutcomeView:
    state = action.state
    if state is ActionState.VERIFIED:
        status = OutcomeStatus.VERIFIED
        summary = "Verified in your calendar" if action.type == "calendar_hold" else "Verified"
    elif state is ActionState.NEEDS_USER:
        status = OutcomeStatus.NEEDS_USER
        summary = "Needs your attention"
    elif state is ActionState.RETRYABLE_FAILURE:
        status = OutcomeStatus.RETRYING
        summary = "Retrying safely"
    elif state is ActionState.FAILED:
        status = OutcomeStatus.FAILED
        summary = "Could not complete"
    elif state is ActionState.HANDOFF_OPENED:
        status = OutcomeStatus.HANDOFF
        summary = "Uber opened. Confirm fare and booking in Uber"
    else:
        status = OutcomeStatus.IN_PROGRESS
        summary = (
            "Calendar update sent, awaiting verification"
            if action.type == "calendar_hold" and state is ActionState.SUCCEEDED
            else "Calling within the approved limits"
            if action.type == "voice_call"
            else "Action is in progress"
        )
    evidence_label = _evidence_label(action.verification_evidence)
    handoff_url = None
    if status is OutcomeStatus.HANDOFF and action.verification_evidence:
        candidate = action.verification_evidence.get("handoff_url")
        if isinstance(candidate, str) and candidate.startswith("https://m.uber.com/ul/"):
            handoff_url = candidate
    return ActionOutcomeView(
        action_id=action.id,
        kind=action.type,
        status=status,
        summary=summary,
        occurred_at=_utc(action.updated_at),
        evidence_label=evidence_label,
        handoff_url=handoff_url,
    )


def _evidence_label(evidence: dict[str, object] | None) -> str | None:
    reason = evidence.get("reason") if evidence else None
    labels = {
        "confirmed_within_bounds": "Confirmation matched the approved limits",
        "calendar_readback_verified": "Calendar event matched the approved hold",
        "authorization_expired": "Approval expired before execution",
        "outcome_no_answer": "The recipient did not answer",
        "outcome_contradiction": "The provider result contradicted the approved bounds",
    }
    return labels.get(reason) if isinstance(reason, str) else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = ["DashboardProjectionService"]
