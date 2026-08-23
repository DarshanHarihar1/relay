from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth import CurrentUser, require_current_user
from app.main import app
from app.routes.actions import get_action_repository


class MinimalApprovalRepository:
    async def decide_approval(self, user_id, request, correlation_id):
        del user_id, correlation_id
        return {
            "approval_id": request.approval_id,
            "state": "approved",
            "action_ids": ["action-1"],
        }


def test_approval_route_returns_only_redacted_batch_result() -> None:
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(uid="user-1", email=None)
    app.dependency_overrides[get_action_repository] = lambda: MinimalApprovalRepository()
    client = TestClient(app)
    try:
        response = client.post(
            "/v1/approvals/approval-1/decision",
            json={"approval_id": "approval-1", "decision": "approve", "expected_version": 1},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "approval_id": "approval-1",
        "state": "approved",
        "action_ids": ["action-1"],
    }
    assert "authorization_snapshot" not in response.text
    assert "provider_ref" not in response.text


def test_approval_route_rejects_malformed_decision() -> None:
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(uid="user-1", email=None)
    app.dependency_overrides[get_action_repository] = lambda: MinimalApprovalRepository()
    client = TestClient(app)
    try:
        response = client.post(
            "/v1/approvals/approval-1/decision",
            json={"approval_id": "approval-1", "decision": "yes", "expected_version": 1},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
