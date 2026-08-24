from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import ActionRecord, ActionState, Approval, ApprovalDecisionRequest, VoiceCallAuthorizationSnapshot
from app.domain.impact import ActionKind, make_action_idempotency_key
from app.repositories.actions import ApprovalVersionConflict


NOW = datetime.now(timezone.utc)


def voice_action(action_id: str, *, state: ActionState = ActionState.AWAITING_APPROVAL) -> ActionRecord:
    snapshot = VoiceCallAuthorizationSnapshot(
        type="voice_call",
        goal="Confirm the reservation",
        recipient_ref=f"place:{action_id}",
        identity_disclosure="I am Relay, an assistant calling on Darshan's behalf.",
        authorized_options=["confirm"],
        max_fee_inr=0,
        must_not=["make_payment"],
        required_evidence=["confirmation"],
        expires_at=NOW + timedelta(hours=1),
    )
    return ActionRecord(
        id=action_id,
        user_id="user-approval",
        repair_plan_id="plan-approval",
        repair_plan_version=1,
        type="voice_call",
        target_ref=f"place:{action_id}",
        idempotency_key=make_action_idempotency_key(1, ActionKind.CALL_VENUE, f"place:{action_id}", snapshot),
        authorization_snapshot=snapshot,
        state=state,
        expires_at=snapshot.expires_at,
        correlation_id="created-correlation",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.emulator
async def test_approval_authorizes_every_action_and_creates_one_pending_dispatch_each(actions):
    await actions.create(voice_action("call-1"))
    await actions.create(voice_action("call-2"))
    await actions.create_approval(
        Approval(
            id="approval-batch",
            user_id="user-approval",
            action_ids=["call-1", "call-2"],
            state="awaiting_approval",
            version=1,
            correlation_id="created-correlation",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    result = await actions.decide_approval(
        "user-approval",
        ApprovalDecisionRequest(approval_id="approval-batch", decision="approve", expected_version=1),
        "corr-approve",
    )

    assert result.state == "approved"
    assert [
        (await actions.get("user-approval", action_id)).state is ActionState.AUTHORIZED
        for action_id in result.action_ids
    ]
    dispatches = [await actions.get_dispatch("user-approval", action_id) for action_id in result.action_ids]
    assert [dispatch.status for dispatch in dispatches if dispatch is not None] == ["pending", "pending"]

    audit_snapshots = [
        snapshot
        async for snapshot in actions._client.collection("users/user-approval/audit_log").stream()
    ]
    assert len(audit_snapshots) == 2


@pytest.mark.emulator
async def test_repeat_or_concurrent_approval_cannot_enqueue_twice(actions):
    await actions.create(voice_action("call-once"))
    await actions.create_approval(
        Approval(
            id="approval-once",
            user_id="user-approval",
            action_ids=["call-once"],
            state="awaiting_approval",
            version=1,
            correlation_id="created-correlation",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    request = ApprovalDecisionRequest(approval_id="approval-once", decision="approve", expected_version=1)

    results = await asyncio.gather(
        actions.decide_approval("user-approval", request, "corr-1"),
        actions.decide_approval("user-approval", request, "corr-2"),
        return_exceptions=True,
    )

    assert sum(isinstance(result, ApprovalVersionConflict) for result in results) == 1
    dispatch = await actions.get_dispatch("user-approval", "call-once")
    assert dispatch is not None
    assert dispatch.status == "pending"


@pytest.mark.emulator
async def test_declining_a_batch_never_creates_dispatch_records(actions):
    await actions.create(voice_action("call-decline"))
    await actions.create_approval(
        Approval(
            id="approval-decline",
            user_id="user-approval",
            action_ids=["call-decline"],
            state="awaiting_approval",
            version=1,
            correlation_id="created-correlation",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    result = await actions.decide_approval(
        "user-approval",
        ApprovalDecisionRequest(approval_id="approval-decline", decision="decline", expected_version=1),
        "corr-decline",
    )

    assert result.state == "declined"
    assert (await actions.get("user-approval", "call-decline")).state is ActionState.NEEDS_USER
    assert await actions.get_dispatch("user-approval", "call-decline") is None
