from __future__ import annotations

from datetime import datetime, timedelta, time, timezone

from app.contracts import ActionState, CallContract, RecordCallOutcomeInput, validate_call_outcome


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def test_unlisted_confirmed_time_is_needs_user() -> None:
    contract = CallContract(
        action_id="action-1",
        goal="Confirm the reservation",
        recipient_ref="place:toscano",
        identity_disclosure="I am Relay, an assistant calling on Darshan's behalf.",
        authorized_options=["23:15"],
        max_fee_inr=0,
        must_not={"make_payment"},
        required_evidence={"venue", "date", "party_size", "confirmed_time"},
        expires_at=NOW + timedelta(hours=1),
    )
    outcome = RecordCallOutcomeInput(
        action_id="action-1",
        outcome="confirmed",
        venue="Toscano",
        date="2026-08-23",
        party_size=2,
        confirmed_time=time(23, 30),
    )

    result = validate_call_outcome(contract, outcome, now=NOW)

    assert result.state is ActionState.NEEDS_USER
    assert result.reason == "unlisted_confirmed_time"
