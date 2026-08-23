from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.contracts import ActionRecord, Approval, VoiceCallAuthorizationSnapshot
from app.domain.impact import ImpactAssessment, RepairCandidate, RepairPlan, make_assessment_id, make_repair_plan_id
from app.repositories.relay_repository import FirestoreRelayRepository

NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


def _fixture(user_id: str):
    fingerprint = "fp1"
    assessment_id = make_assessment_id("disruption_1", fingerprint)
    assessment = ImpactAssessment(
        id=assessment_id,
        disruption_id="disruption_1",
        source_commitment_id="flight",
        reachable_commitment_ids=("flight", "dinner"),
        affected_commitment_ids=("dinner",),
        nodes=(),
        diagnostics=(),
        input_fingerprint=fingerprint,
    )
    candidate = RepairCandidate(
        id="candidate_1",
        kind="KEEP_AS_IS",
        changes=(),
        invalid_reasons=(),
        explanation="Keep dinner as scheduled.",
    )
    plan = RepairPlan(
        id=make_repair_plan_id(assessment_id, 1),
        version=1,
        assessment_id=assessment.id,
        selected_candidate_id=candidate.id,
        candidates=(candidate,),
        approval_id="approval_1",
        input_fingerprint=fingerprint,
    )
    snapshot = VoiceCallAuthorizationSnapshot(
        type="voice_call",
        goal="Confirm the reservation",
        recipient_ref="place_toscano_blr",
        identity_disclosure="I am Relay, an assistant calling on Darshan's behalf.",
        authorized_options=["confirm"],
        max_fee_inr=0,
        must_not=["make_payment"],
        required_evidence=["confirmation"],
        expires_at=NOW,
    )
    action = ActionRecord(
        id="action_1",
        user_id=user_id,
        repair_plan_id=plan.id,
        repair_plan_version=1,
        type="voice_call",
        target_ref="place_toscano_blr",
        idempotency_key="akey_1",
        authorization_snapshot=snapshot,
        state="awaiting_approval",
        correlation_id="corr-1",
    )
    approval = Approval(
        id="approval_1",
        user_id=user_id,
        action_ids=[action.id],
        state="awaiting_approval",
        version=1,
        correlation_id="corr-1",
    )
    return assessment, plan, (action,), approval


@pytest.mark.emulator
async def test_firestore_transaction_does_not_duplicate_documents_for_same_fingerprint(firestore_client) -> None:
    user_id = f"u-{uuid4().hex}"
    repository = FirestoreRelayRepository(firestore_client)
    assessment, plan, actions, approval = _fixture(user_id)

    first, second = await asyncio.gather(
        repository.save_planning_result(
            user_id=user_id, assessment=assessment, plan=plan, action_records=actions, approval=approval
        ),
        repository.save_planning_result(
            user_id=user_id, assessment=assessment, plan=plan, action_records=actions, approval=approval
        ),
    )

    assert first.id == second.id
    snapshots = [
        snapshot
        async for snapshot in firestore_client.collection(f"users/{user_id}/repair_plans").stream()
    ]
    assert len(snapshots) == 1


@pytest.mark.emulator
async def test_saved_plan_round_trips_through_get_repair_plan_by_fingerprint(firestore_client) -> None:
    user_id = f"u-{uuid4().hex}"
    repository = FirestoreRelayRepository(firestore_client)
    assessment, plan, actions, approval = _fixture(user_id)

    await repository.save_planning_result(
        user_id=user_id, assessment=assessment, plan=plan, action_records=actions, approval=approval
    )

    found = await repository.get_repair_plan_by_fingerprint(
        user_id=user_id, disruption_id=assessment.disruption_id, input_fingerprint=assessment.input_fingerprint
    )
    assert found is not None
    assert found.id == plan.id
    assert found.candidates[0].id == plan.selected_candidate_id
