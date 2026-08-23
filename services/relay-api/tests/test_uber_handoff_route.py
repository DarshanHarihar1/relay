from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth import CurrentUser, require_current_user
from app.contracts import ActionRecord, HandoffResponse
from app.main import app
from app.routes.actions import get_action_repository


class FakeHandoffRepository:
    async def get(self, user_id, action_id):
        assert user_id == "user-1"
        return ActionRecord(
            id=action_id,
            user_id=user_id,
            repair_plan_id="plan-1",
            repair_plan_version=1,
            type="uber_deep_link",
            target_ref="commitment:dinner",
            idempotency_key="akey_uber",
            authorization_snapshot={
                "type": "uber_deep_link",
                "pickup": "Airport",
                "destination": "Toscano",
                "handoff_label": "Open Uber",
            },
            state="authorized",
            correlation_id="corr-1",
        )

    async def open_handoff(self, user_id, action_id, url, correlation_id):
        assert user_id == "user-1"
        assert action_id == "ride-1"
        assert "request" not in url
        return HandoffResponse(action_id=action_id, state="handoff_opened", url=url)


def test_open_handoff_returns_handoff_state_and_url() -> None:
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(uid="user-1", email=None)
    app.dependency_overrides[get_action_repository] = lambda: FakeHandoffRepository()
    client = TestClient(app)
    try:
        response = client.post("/v1/actions/ride-1/open-handoff")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["state"] == "handoff_opened"
    assert "request" not in response.json()["url"]
