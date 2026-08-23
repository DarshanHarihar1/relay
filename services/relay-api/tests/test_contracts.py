from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contracts import ActionRecord, SourceEventEnvelope
from app.main import app


NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


def minimal_action() -> dict[str, object]:
    return {
        "id": "action-1",
        "user_id": "user-1",
        "repair_plan_id": "plan-1",
        "repair_plan_version": 1,
        "type": "calendar_hold",
        "target_ref": "calendar:primary",
        "idempotency_key": "repair-1:calendar-hold",
        "authorization_snapshot": {
            "type": "calendar_hold",
            "calendar_id": "primary",
            "start_at": NOW,
            "end_at": NOW,
            "visibility": "private",
        },
        "state": "planned",
        "correlation_id": "correlation-1",
    }


def test_action_state_schema_rejects_ride_booked():
    with pytest.raises(ValidationError):
        ActionRecord.model_validate({**minimal_action(), "state": "ride_booked"})


def test_source_event_requires_a_stable_idempotency_key():
    with pytest.raises(ValidationError):
        SourceEventEnvelope(
            source="gmail",
            source_event_key="",
            occurred_at=NOW,
            payload={},
            correlation_id="correlation-1",
        )


def test_voice_call_allows_zero_fee_but_requires_timezone_aware_expiry():
    action = minimal_action() | {
        "type": "voice_call",
        "authorization_snapshot": {
            "type": "voice_call",
            "goal": "Move appointment",
            "recipient_ref": "contact:clinic",
            "identity_disclosure": "I am calling on behalf of the user.",
            "authorized_options": ["Tuesday afternoon"],
            "max_fee_inr": 0,
            "must_not": ["Share private details"],
            "required_evidence": ["confirmed time"],
            "expires_at": "2026-08-23T12:00:00",
        },
    }

    with pytest.raises(ValidationError):
        ActionRecord.model_validate(action)


def test_snapshot_type_must_match_the_action_type():
    action = minimal_action() | {
        "type": "uber_deep_link",
        "authorization_snapshot": minimal_action()["authorization_snapshot"],
    }

    with pytest.raises(ValidationError):
        ActionRecord.model_validate(action)


def test_fastapi_openapi_declares_only_relay_contract_routes():
    document = app.openapi()

    assert set(document["paths"]) == {
        "/healthz",
        "/health",
        "/v1/me",
        "/v1/actions/{action_id}",
        "/v1/approvals/{approval_id}/decision",
        "/v1/google/connect",
        "/v1/google/callback",
        "/v1/google/connection",
        "/v1/google/contacts",
        "/v1/commitments/{commitment_id}/pickup-contact",
        "/v1/events/gmail",
        "/internal/maintenance/daily",
    }
    assert "SourceEventEnvelope" in document["components"]["schemas"]
    assert "ride_booked" not in document["components"]["schemas"]["ActionState"]["enum"]


def test_api_errors_have_a_safe_problem_body_and_correlation_id():
    response = TestClient(app).get("/not-a-route", headers={"X-Correlation-ID": "correlation-1"})

    assert response.status_code == 404
    assert response.headers["X-Correlation-ID"] == "correlation-1"
    assert response.json() == {
        "code": "not_found",
        "message": "The requested resource was not found.",
        "correlation_id": "correlation-1",
    }
