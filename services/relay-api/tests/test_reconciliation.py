from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import ActionRecord, ActionState, RecordCallOutcomeInput
from app.providers.calendar import CalendarVerification
from app.services.reconciliation import ReconciliationService


NOW = datetime(2027, 8, 23, 12, 0, tzinfo=timezone.utc)


def voice_action() -> ActionRecord:
    return ActionRecord(
        id="call-1",
        user_id="user-1",
        repair_plan_id="plan-1",
        repair_plan_version=1,
        type="voice_call",
        target_ref="place:toscano",
        idempotency_key="akey_call",
        authorization_snapshot={
            "type": "voice_call",
            "goal": "Confirm the reservation",
            "recipient_ref": "place:toscano",
            "identity_disclosure": "I am Relay, an assistant calling on Darshan's behalf.",
            "authorized_options": ["23:15"],
            "max_fee_inr": 0,
            "must_not": ["make_payment"],
            "required_evidence": ["venue", "date", "party_size", "confirmed_time"],
            "expires_at": NOW + timedelta(hours=1),
        },
        provider_ref="call-remote",
        state=ActionState.DISPATCHED,
        expires_at=NOW + timedelta(hours=1),
        correlation_id="corr-1",
    )


def calendar_action() -> ActionRecord:
    action = voice_action()
    return ActionRecord(
        **{
            **action.model_dump(),
            "id": "hold-1",
            "type": "calendar_hold",
            "provider_ref": "calendar-event",
            "state": ActionState.SUCCEEDED,
            "authorization_snapshot": {
                "type": "calendar_hold",
                "calendar_id": "primary",
                "start_at": NOW,
                "end_at": NOW + timedelta(hours=1),
                "visibility": "private",
            },
        }
    )


class FakeRepository:
    def __init__(self, record: ActionRecord) -> None:
        self.action = record
        self.updated: list[ActionState] = []

    async def get(self, user_id, action_id):
        if user_id == self.action.user_id and action_id == self.action.id:
            return self.action
        return None

    async def apply_provider_outcome(self, user_id, action_id, state, evidence, correlation_id):
        del user_id, action_id, evidence, correlation_id
        self.action = self.action.model_copy(update={"state": state})
        self.updated.append(state)
        return self.action


class FakeVapi:
    def __init__(self, outcome: RecordCallOutcomeInput | None) -> None:
        self.final_outcome = outcome
        self.create_call_count = 0

    async def get_final_outcome(self, provider_ref: str):
        del provider_ref
        return self.final_outcome


class FakeCalendar:
    def __init__(self, verification: CalendarVerification) -> None:
        self.verification = verification

    async def verify_private_hold(self, action):
        del action
        return self.verification


@pytest.mark.asyncio
async def test_reconciliation_fetches_vapi_outcome_before_retrying_create() -> None:
    repository = FakeRepository(voice_action())
    vapi = FakeVapi(RecordCallOutcomeInput(action_id="call-1", outcome="no_answer"))

    result = await ReconciliationService(
        repository,
        vapi=vapi,
        calendar=None,
        user_id="user-1",
    ).reconcile("call-1", now=NOW)

    assert result.state is ActionState.NEEDS_USER
    assert vapi.create_call_count == 0


@pytest.mark.asyncio
async def test_reconciliation_readback_verifies_calendar_success() -> None:
    repository = FakeRepository(calendar_action())
    calendar = FakeCalendar(
        CalendarVerification(
            state=ActionState.VERIFIED,
            evidence={"event_id_hash": "a" * 64},
            reason="calendar_hold_readback_verified",
        )
    )

    result = await ReconciliationService(
        repository,
        vapi=None,
        calendar=calendar,
        user_id="user-1",
    ).reconcile("hold-1", now=NOW)

    assert result.state is ActionState.VERIFIED
