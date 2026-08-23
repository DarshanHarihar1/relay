from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.domain.product import ActionAuditView, ActionOutcomeView, DashboardView, OutcomeStatus, PickupContactResponse
from app.main import app
from app.routes.product import (
    get_audit_projection_service,
    get_dashboard_projection_service,
    get_pickup_commitment_service,
    get_notification_service,
)


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _auth_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "relay-test")
    monkeypatch.setattr(
        "app.auth._verify_firebase_id_token",
        lambda token, project_id: {"aud": project_id, "uid": "user-1", "email": "user@example.com"},
    )
    return {"Authorization": "Bearer test-token", "X-Correlation-ID": "corr-product"}


class FakeDashboardService:
    async def build_dashboard(self, *, user_id: str):
        assert user_id == "user-1"
        return DashboardView(
            repair_plan_id="plan-1",
            repair_plan_version=1,
            generated_at=NOW,
            timeline=(),
            approval=None,
            outcomes=(
                ActionOutcomeView(
                    action_id="call-1",
                    kind="voice_call",
                    status=OutcomeStatus.NEEDS_USER,
                    summary="Needs your attention",
                    occurred_at=NOW,
                ),
            ),
            last_event_id=None,
        )


class FakePickupService:
    async def submit_pickup_contact(self, **kwargs):
        assert kwargs["user_id"] == "user-1"
        return PickupContactResponse(
            commitment_id=kwargs["commitment_id"],
            version=kwargs["command"].expected_version + 1,
            selection="no_pickup",
            display_name=None,
        )


class FakeAuditService:
    async def get_action_audit(self, *, user_id: str, action_id: str):
        assert user_id == "user-1"
        assert action_id == "call-1"
        return ActionAuditView(
            outcome=ActionOutcomeView(
                action_id=action_id,
                kind="voice_call",
                status=OutcomeStatus.NEEDS_USER,
                summary="Needs your attention",
                occurred_at=NOW,
                evidence_label="The recipient did not answer",
            ),
            events=(),
        )


class FakeNotificationService:
    def __init__(self) -> None:
        self.user_id: str | None = None
        self.token: str | None = None

    async def register_device(self, *, user_id: str, token: str, platform: str = "web") -> None:
        self.user_id = user_id
        self.token = token


def test_dashboard_requires_firebase_identity(monkeypatch) -> None:
    response = TestClient(app).get("/v1/dashboard")
    assert response.status_code == 401


def test_dashboard_and_audit_are_redacted_and_correlated(monkeypatch) -> None:
    headers = _auth_headers(monkeypatch)
    app.dependency_overrides[get_dashboard_projection_service] = lambda: FakeDashboardService()
    app.dependency_overrides[get_audit_projection_service] = lambda: FakeAuditService()
    try:
        client = TestClient(app)
        dashboard = client.get("/v1/dashboard", headers=headers)
        audit = client.get("/v1/actions/call-1/audit", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert dashboard.status_code == 200
    assert dashboard.headers["X-Correlation-ID"] == "corr-product"
    assert audit.status_code == 200
    assert "provider_ref" not in audit.text
    assert "enc:v1:" not in audit.text


def test_pickup_route_uses_token_subject_and_rejects_extra_user_input(monkeypatch) -> None:
    headers = _auth_headers(monkeypatch)
    app.dependency_overrides[get_pickup_commitment_service] = lambda: FakePickupService()
    try:
        response = TestClient(app).post(
            "/v1/commitments/pickup-1/pickup-contact",
            headers=headers,
            json={"selection": "no_pickup", "expected_version": 1, "user_id": "other"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_device_registration_uses_token_subject(monkeypatch) -> None:
    headers = _auth_headers(monkeypatch)
    service = FakeNotificationService()
    app.dependency_overrides[get_notification_service] = lambda: service
    try:
        response = TestClient(app).post(
            "/v1/devices",
            headers=headers,
            json={"token": "f" * 32, "platform": "web"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert service.user_id == "user-1"
    assert service.token == "f" * 32
