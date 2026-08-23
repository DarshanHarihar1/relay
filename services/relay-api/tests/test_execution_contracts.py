from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.contracts import (
    ActionState,
    CallContract,
    DispatchClaim,
    ProviderEvent,
    RecordCallOutcomeInput,
    validate_call_outcome,
)
from app.main import app


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def call_contract(**overrides: object) -> CallContract:
    values: dict[str, object] = {
        "action_id": "action-1",
        "goal": "reschedule_restaurant_reservation",
        "recipient_ref": "place:toscano",
        "identity_disclosure": "I am Relay, an assistant calling on the user's behalf.",
        "authorized_options": ["23:15"],
        "max_fee_inr": 0,
        "must_not": {"make_payment", "share_sensitive_data", "accept_unlisted_time"},
        "required_evidence": {"venue", "date", "party_size", "confirmed_time"},
        "expires_at": NOW + timedelta(hours=1),
    }
    values.update(overrides)
    return CallContract.model_validate(values)


def test_confirmed_call_outcome_requires_every_evidence_field() -> None:
    contract = call_contract()
    outcome = RecordCallOutcomeInput(
        action_id=contract.action_id,
        outcome="confirmed",
        venue="Toscano",
    )

    validation = validate_call_outcome(contract, outcome, now=NOW)

    assert validation.state is ActionState.NEEDS_USER
    assert validation.reason == "missing_required_evidence"


def test_zero_fee_is_an_allowed_call_bound() -> None:
    assert call_contract().max_fee_inr == 0


def test_provider_event_carries_a_payload_hash_without_raw_payload() -> None:
    event = ProviderEvent(
        id="vapi:event-1",
        action_id="action-1",
        provider="vapi",
        provider_event_key="event-1",
        event_type="record_call_outcome",
        provider_ref="call-1",
        payload_hash="a" * 64,
        occurred_at=NOW,
        correlation_id="corr-1",
    )

    assert event.payload_hash == "a" * 64
    assert not hasattr(event, "payload")


def test_dispatch_claim_defaults_to_no_reconciliation() -> None:
    claim = DispatchClaim(claimed=False)

    assert claim.reconciliation_required is False


def test_execution_models_are_exposed_in_the_fastapi_schema() -> None:
    schemas = app.openapi()["components"]["schemas"]

    assert {"CallContract", "RecordCallOutcomeInput", "OutcomeValidation"} <= set(schemas)
