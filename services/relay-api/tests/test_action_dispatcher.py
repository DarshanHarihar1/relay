from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import ActionRecord, ActionState, DispatchClaim
from app.services.action_dispatcher import ActionDispatcher, ProviderAttempt
from app.services.retry_policy import ProviderFailure, RetryPolicy


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def action(state: ActionState = ActionState.AUTHORIZED) -> ActionRecord:
    return ActionRecord(
        id="call-1",
        user_id="user-1",
        repair_plan_id="plan-1",
        repair_plan_version=1,
        type="voice_call",
        target_ref="contact:venue",
        idempotency_key="relay-action-v1:stable",
        authorization_snapshot={
            "type": "voice_call",
            "goal": "Confirm the reservation",
            "recipient_ref": "contact:venue",
            "identity_disclosure": "I am Relay, an assistant calling on Darshan's behalf.",
            "authorized_options": ["confirm"],
            "max_fee_inr": 0,
            "must_not": ["make_payment"],
            "required_evidence": ["confirmation"],
            "expires_at": NOW + timedelta(hours=1),
        },
        state=state,
        expires_at=NOW + timedelta(hours=1),
        correlation_id="corr-1",
    )


class FakeRepository:
    def __init__(self) -> None:
        self.action = action()
        self.claimed = False
        self.provider_ref: str | None = None

    async def claim_dispatch(self, user_id, action_id, now, correlation_id):
        del user_id, action_id, now, correlation_id
        if self.claimed:
            return DispatchClaim(claimed=False, action=self.action)
        self.claimed = True
        self.action = self.action.model_copy(update={"state": ActionState.DISPATCHED})
        return DispatchClaim(claimed=True, action=self.action)

    async def mark_provider_request(self, user_id, action_id, provider_ref, evidence, correlation_id):
        del user_id, action_id, correlation_id
        self.provider_ref = provider_ref
        self.action = self.action.model_copy(
            update={"provider_ref": provider_ref, "state": ActionState.IN_PROGRESS, "verification_evidence": evidence}
        )
        return self.action

    async def complete_dispatch_record(self, user_id, action_id, correlation_id):
        del user_id, action_id, correlation_id

    async def get(self, user_id, action_id):
        del user_id, action_id
        return self.action


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, action: ActionRecord) -> ProviderAttempt:
        del action
        self.calls += 1
        await asyncio.sleep(0)
        return ProviderAttempt(provider_ref="provider-call-1", evidence={"accepted": True})


@pytest.mark.asyncio
async def test_two_workers_claim_only_one_provider_effect() -> None:
    repository = FakeRepository()
    executor = FakeExecutor()
    dispatcher = ActionDispatcher(repository, {"voice_call": executor})

    first, second = await asyncio.gather(
        dispatcher.dispatch("user-1", "call-1", now=NOW),
        dispatcher.dispatch("user-1", "call-1", now=NOW),
    )

    assert executor.calls == 1
    assert {first.state, second.state} <= {ActionState.DISPATCHED, ActionState.IN_PROGRESS}


@pytest.mark.asyncio
async def test_uncertain_dispatched_action_is_reconciled_without_second_effect() -> None:
    repository = FakeRepository()
    repository.claimed = True
    repository.action = action(ActionState.DISPATCHED)
    executor = FakeExecutor()
    dispatcher = ActionDispatcher(repository, {"voice_call": executor})

    result = await dispatcher.dispatch("user-1", "call-1", now=NOW)

    assert result.state is ActionState.DISPATCHED
    assert executor.calls == 0


def test_retry_policy_retries_only_transient_failures_with_bounded_delay(monkeypatch) -> None:
    monkeypatch.setattr("app.services.retry_policy.random.uniform", lambda lower, upper: upper)
    decision = RetryPolicy().next_attempt(
        action(),
        ProviderFailure(status_code=503),
        now=NOW,
    )

    assert decision.state is ActionState.RETRYABLE_FAILURE
    assert decision.delay_seconds == 30


def test_retry_policy_stops_before_an_expired_voice_authorization(monkeypatch) -> None:
    monkeypatch.setattr("app.services.retry_policy.random.uniform", lambda lower, upper: upper)
    expired = action().model_copy(update={"expires_at": NOW + timedelta(seconds=5)})

    decision = RetryPolicy().next_attempt(
        expired,
        ProviderFailure(status_code=503),
        now=NOW,
    )

    assert decision.state is ActionState.NEEDS_USER


def test_retry_policy_fails_after_three_transient_attempts() -> None:
    exhausted = action().model_copy(update={"retry_count": 3})

    decision = RetryPolicy().next_attempt(
        exhausted,
        ProviderFailure(status_code=503),
        now=NOW,
    )

    assert decision.state is ActionState.FAILED
