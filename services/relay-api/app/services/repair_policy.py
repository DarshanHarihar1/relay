from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.contracts import (
    ActionRecord,
    ActionState,
    Approval,
    AuthorizationSnapshot,
    CalendarHoldAuthorizationSnapshot,
    UberDeepLinkAuthorizationSnapshot,
    VoiceCallAuthorizationSnapshot,
)
from app.domain.impact import (
    ActionKind,
    CandidateChange,
    PolicyDecision,
    RepairCandidate,
    make_action_idempotency_key,
    make_action_record_id,
    sha256_id,
)

# Reconciliation ledger (.superpowers/sdd/phase-03-impact-repair-planning/progress.md),
# Finding 3: phase-03 policy produces the canonical ActionRecord/Approval/
# AuthorizationSnapshot directly. There is no ActionIntent/ActionBounds/ApprovalBatch.

_VOICE_KINDS = frozenset({ActionKind.CALL_CONTACT, ActionKind.CALL_VENUE, ActionKind.CALL_HOTEL})
# Global constraint: every voice action and the Uber handoff always requires
# explicit approval, regardless of what a broad auto-policy would allow.
_ALWAYS_ASK_KINDS = _VOICE_KINDS | {ActionKind.OPEN_UBER_HANDOFF}

_ACTION_RECORD_TYPE: dict[ActionKind, str] = {
    ActionKind.CALL_CONTACT: "voice_call",
    ActionKind.CALL_VENUE: "voice_call",
    ActionKind.CALL_HOTEL: "voice_call",
    ActionKind.CREATE_CALENDAR_HOLD: "calendar_hold",
    ActionKind.OPEN_UBER_HANDOFF: "uber_deep_link",
}


class UserActionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    auto_action_kinds: tuple[ActionKind, ...] = ()
    never_action_kinds: tuple[ActionKind, ...] = ()
    max_auto_fee_inr: int = Field(default=0, ge=0)


def _target_ref(change: CandidateChange) -> str:
    return change.target_ref or f"commitment:{change.commitment_id}"


def _build_snapshot(kind: ActionKind, change: CandidateChange, expires_at: datetime) -> AuthorizationSnapshot:
    target_ref = _target_ref(change)
    if kind in _VOICE_KINDS:
        return VoiceCallAuthorizationSnapshot(
            type="voice_call",
            goal=f"Resolve the disruption affecting {change.commitment_id}",
            recipient_ref=target_ref,
            identity_disclosure="I am Relay, an assistant calling on Darshan's behalf.",
            authorized_options=["confirm_new_time"],
            max_fee_inr=0,
            must_not=["make_payment"],
            required_evidence=["confirmation"],
            expires_at=expires_at,
        )
    if kind is ActionKind.CREATE_CALENDAR_HOLD:
        start_at = change.proposed_start or expires_at
        end_at = change.proposed_end or expires_at
        return CalendarHoldAuthorizationSnapshot(
            type="calendar_hold",
            calendar_id=target_ref,
            start_at=start_at,
            end_at=end_at,
            visibility="private",
        )
    return UberDeepLinkAuthorizationSnapshot(
        type="uber_deep_link",
        pickup="current_location",
        destination=target_ref,
        handoff_label="Open Uber",
    )


class PolicyEngine:
    def decide(self, kind: ActionKind, snapshot: AuthorizationSnapshot, policy: UserActionPolicy) -> PolicyDecision:
        if kind in policy.never_action_kinds:
            return PolicyDecision.NEVER
        if kind in _ALWAYS_ASK_KINDS:
            return PolicyDecision.ASK
        fee = getattr(snapshot, "max_fee_inr", 0)
        if kind in policy.auto_action_kinds and fee <= policy.max_auto_fee_inr:
            return PolicyDecision.AUTO
        return PolicyDecision.ASK

    def create_batch(
        self,
        *,
        user_id: str,
        repair_plan_id: str,
        repair_plan_version: int,
        candidate: RepairCandidate,
        policy: UserActionPolicy,
        expires_at: datetime,
        correlation_id: str,
    ) -> tuple[tuple[ActionRecord, ...], Approval | None]:
        planned = [
            (kind, change, _build_snapshot(kind, change, expires_at))
            for change in candidate.changes
            for kind in change.action_kinds
        ]
        decisions = {(kind, change): self.decide(kind, snapshot, policy) for kind, change, snapshot in planned}

        if any(decision is PolicyDecision.NEVER for decision in decisions.values()):
            # No ActionRecord is created at all: a NEVER-policy action was
            # never going to be attempted. The block is explained on the
            # RepairCandidate itself, not here.
            return (), None

        has_ask = any(decision is PolicyDecision.ASK for decision in decisions.values())

        planned.sort(key=lambda item: (item[0].value, _target_ref(item[1])))

        records: list[ActionRecord] = []
        for kind, change, snapshot in planned:
            decision = decisions[(kind, change)]
            state = ActionState.AWAITING_APPROVAL if has_ask or decision is PolicyDecision.ASK else ActionState.AUTHORIZED
            target_ref = _target_ref(change)
            action_id = make_action_record_id(repair_plan_id, candidate.id, kind, target_ref)
            records.append(
                ActionRecord(
                    id=action_id,
                    user_id=user_id,
                    repair_plan_id=repair_plan_id,
                    repair_plan_version=repair_plan_version,
                    type=_ACTION_RECORD_TYPE[kind],
                    target_ref=target_ref,
                    idempotency_key=make_action_idempotency_key(repair_plan_version, kind, target_ref, snapshot),
                    authorization_snapshot=snapshot,
                    state=state,
                    correlation_id=correlation_id,
                    expires_at=expires_at,
                )
            )

        if not has_ask:
            # Every action was AUTO-authorized directly; no approval record
            # is needed (audit trail lives on the ActionRecord itself).
            return tuple(records), None

        approval_id = sha256_id(
            "approval",
            {"repair_plan_id": repair_plan_id, "repair_plan_version": repair_plan_version, "candidate_id": candidate.id},
        )
        approval = Approval(
            id=approval_id,
            user_id=user_id,
            action_ids=[record.id for record in records],
            state="awaiting_approval",
            version=1,
            correlation_id=correlation_id,
            expires_at=expires_at,
        )
        return tuple(records), approval
