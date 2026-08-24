from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import ActionRecord, ActionState, Approval, ApprovalDecisionRequest
from app.repositories.actions import ApprovalVersionConflict
from app.services.action_state import derive_action_idempotency_key


NOW = datetime.now(timezone.utc)


def authorized_voice_action(*, id: str, expires_at: datetime = NOW + timedelta(minutes=5)) -> ActionRecord:
    snapshot = {
        "type": "voice_call",
        "goal": "Move appointment",
        "recipient_ref": "contact:clinic",
        "identity_disclosure": "I am calling on behalf of the user.",
        "authorized_options": ["Tuesday afternoon"],
        "max_fee_inr": 1000,
        "must_not": ["Share private details"],
        "required_evidence": ["confirmed time"],
        "expires_at": expires_at,
    }
    return ActionRecord(
        id=id,
        user_id="user-a",
        repair_plan_id="plan-1",
        repair_plan_version=1,
        type="voice_call",
        target_ref="contact:clinic",
        idempotency_key=derive_action_idempotency_key(1, "voice_call", "contact:clinic", snapshot),
        authorization_snapshot=snapshot,
        state=ActionState.AUTHORIZED,
        expires_at=expires_at,
        correlation_id="created-correlation",
    )


@pytest.mark.emulator
async def test_two_simultaneous_claims_return_one_dispatched_action(actions):
    await actions.create(authorized_voice_action(id="act-1"))

    first, second = await asyncio.gather(
        actions.claim_dispatch("user-a", "act-1", NOW, "c-1"),
        actions.claim_dispatch("user-a", "act-1", NOW, "c-2"),
    )

    assert {first.action.state, second.action.state} == {ActionState.DISPATCHED}
    assert first.claimed is not second.claimed
    assert (await actions.get("user-a", "act-1")).version == 2
    dispatch = await actions.get_dispatch("user-a", "act-1")
    assert dispatch is not None
    assert dispatch.status == "claimed"
    assert {first.reconciliation_required, second.reconciliation_required} == {False, True}


@pytest.mark.emulator
async def test_expired_voice_action_becomes_needs_user_without_a_dispatch_claim(actions):
    await actions.create(authorized_voice_action(id="act-expired", expires_at=NOW - timedelta(seconds=1)))

    claim = await actions.claim_dispatch("user-a", "act-expired", NOW, "c-1")

    assert claim.claimed is False
    assert claim.action.state is ActionState.NEEDS_USER
    assert claim.action.dispatched_at is None
    assert claim.action.verification_evidence == {"reason": "authorization_expired"}
    assert claim.action.version == 2


@pytest.mark.emulator
async def test_second_approval_decision_is_a_conflict_without_reauthorizing_actions(actions):
    await actions.create(authorized_voice_action(id="act-approval").model_copy(update={"state": ActionState.AWAITING_APPROVAL}))
    await actions.create_approval(
        Approval(
            id="approval-1",
            user_id="user-a",
            action_ids=["act-approval"],
            state="awaiting_approval",
            version=1,
            correlation_id="created-correlation",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    before = await actions.get("user-a", "act-approval")
    first = await actions.decide_approval(
        "user-a",
        ApprovalDecisionRequest(approval_id="approval-1", decision="approve", expected_version=1),
        "c-1",
    )
    with pytest.raises(ApprovalVersionConflict):
        await actions.decide_approval(
            "user-a",
            ApprovalDecisionRequest(approval_id="approval-1", decision="approve", expected_version=1),
            "c-2",
        )

    stored = await actions.get("user-a", "act-approval")
    assert first.action_ids == ["act-approval"]
    assert stored is not None
    assert before is not None
    assert stored.state is ActionState.AUTHORIZED
    assert stored.version == 2
    assert stored.updated_at > before.updated_at
