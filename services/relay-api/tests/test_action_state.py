from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth import CurrentUser, require_current_user
from app.contracts import ActionRecord, ActionState, Approval, ApprovalDecisionRequest, ApprovalDecisionResponse
from app.main import app
from app.routes.actions import get_action_repository
from app.services.action_state import (
    InvalidActionTransition,
    derive_action_idempotency_key,
    valid_action_idempotency_keys,
    validate_transition,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def test_authorized_action_can_be_claimed_once():
    assert validate_transition(ActionState.AUTHORIZED, ActionState.DISPATCHED) is None


def test_authorized_action_cannot_skip_to_verified():
    with pytest.raises(InvalidActionTransition):
        validate_transition(ActionState.AUTHORIZED, ActionState.VERIFIED)


def test_succeeded_action_can_be_reconciled_for_a_retryable_failure():
    assert validate_transition(ActionState.SUCCEEDED, ActionState.RETRYABLE_FAILURE) is None


def test_handoff_is_only_available_for_an_uber_action():
    with pytest.raises(InvalidActionTransition):
        validate_transition(
            ActionState.AUTHORIZED,
            ActionState.HANDOFF_OPENED,
            action_type="calendar_hold",
        )

    assert (
        validate_transition(
            ActionState.AUTHORIZED,
            ActionState.HANDOFF_OPENED,
            action_type="uber_deep_link",
        )
        is None
    )


def test_action_idempotency_key_is_canonical_and_does_not_include_provider_data():
    first = derive_action_idempotency_key(
        3,
        "voice_call",
        "contact:clinic",
        {
            "type": "voice_call",
            "goal": "Move appointment",
            "authorized_options": ["Tuesday afternoon"],
        },
    )
    second = derive_action_idempotency_key(
        3,
        "voice_call",
        "contact:clinic",
        {
            "authorized_options": ["Tuesday afternoon"],
            "goal": "Move appointment",
            "type": "voice_call",
        },
    )

    assert first.startswith("relay-action-v1:")
    assert first == second


def test_phase3_and_legacy_action_keys_are_both_accepted_during_migration():
    action = InMemoryActionRepository().action.model_copy(
        update={
            "idempotency_key": derive_action_idempotency_key(
                1,
                "calendar_hold",
                "calendar:primary",
                InMemoryActionRepository().action.authorization_snapshot,
            )
        }
    )

    assert action.idempotency_key in valid_action_idempotency_keys(action)
    assert derive_action_idempotency_key(
        action.repair_plan_version,
        action.type,
        action.target_ref,
        action.authorization_snapshot,
    ) in valid_action_idempotency_keys(action)


class InMemoryActionRepository:
    def __init__(self) -> None:
        self.approval = Approval(
            id="approval-1",
            user_id="user-1",
            action_ids=["action-1"],
            state="awaiting_approval",
            version=1,
            correlation_id="created-correlation",
        )
        self.action = ActionRecord(
            id="action-1",
            user_id="user-1",
            repair_plan_id="plan-1",
            repair_plan_version=1,
            type="calendar_hold",
            target_ref="calendar:primary",
            idempotency_key="relay-action-v1:test",
            authorization_snapshot={
                "type": "calendar_hold",
                "calendar_id": "primary",
                "start_at": NOW,
                "end_at": datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc),
                "visibility": "private",
            },
            state=ActionState.AWAITING_APPROVAL,
            correlation_id="created-correlation",
        )

    async def decide_approval(
        self,
        user_id: str,
        request: ApprovalDecisionRequest,
        correlation_id: str,
    ) -> ApprovalDecisionResponse:
        if user_id != self.approval.user_id or request.approval_id != self.approval.id:
            raise LookupError
        if self.approval.version != request.expected_version or self.approval.state != "awaiting_approval":
            from app.repositories.actions import ApprovalVersionConflict

            raise ApprovalVersionConflict
        state = "approved" if request.decision == "approve" else "declined"
        action_state = ActionState.AUTHORIZED if state == "approved" else ActionState.NEEDS_USER
        self.approval = self.approval.model_copy(
            update={"state": state, "version": 2, "correlation_id": correlation_id}
        )
        self.action = self.action.model_copy(update={"state": action_state, "version": 2})
        return ApprovalDecisionResponse(
            approval_id=self.approval.id,
            state=state,
            action_ids=self.approval.action_ids,
        )


def test_calendar_action_cannot_persist_an_uber_handoff_state():
    action = InMemoryActionRepository().action.model_dump()

    with pytest.raises(ValueError):
        ActionRecord.model_validate({**action, "state": ActionState.HANDOFF_OPENED})


def test_second_approval_click_returns_a_redacted_version_conflict():
    repository = InMemoryActionRepository()
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(uid="user-1", email=None)
    app.dependency_overrides[get_action_repository] = lambda: repository
    client = TestClient(app)
    payload = {"approval_id": "approval-1", "decision": "approve", "expected_version": 1}
    try:
        first = client.post("/v1/approvals/approval-1/decision", json=payload)
        second = client.post("/v1/approvals/approval-1/decision", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert first.json() == {
        "approval_id": "approval-1",
        "state": "approved",
        "action_ids": ["action-1"],
    }
    assert second.status_code == 409
    assert second.json()["code"] == "approval_version_conflict"
    assert "version" not in second.json()["message"].lower()
    assert repository.action.state is ActionState.AUTHORIZED
    assert repository.action.version == 2
