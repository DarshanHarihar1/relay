from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.contracts import ActionRecord, CallContract, JsonValue
from app.providers.base import VoiceAdapter
from app.repositories.actions import ActionRepository
from app.services.retry_policy import ProviderFailure, RetryDecision, RetryPolicy


@dataclass(frozen=True)
class ProviderAttempt:
    provider_ref: str
    evidence: dict[str, JsonValue]


class ActionExecutor(Protocol):
    async def execute(self, action: ActionRecord) -> ProviderAttempt: ...


class VoiceCallExecutor:
    def __init__(self, adapter: VoiceAdapter) -> None:
        self._adapter = adapter

    async def execute(self, action: ActionRecord) -> ProviderAttempt:
        if action.type != "voice_call":
            raise ValueError("VoiceCallExecutor only accepts voice_call actions")
        snapshot = action.authorization_snapshot
        contract = CallContract.model_validate(
            {
                **snapshot.model_dump(mode="python"),
                "action_id": action.id,
            }
        )
        reference = await self._adapter.create_call(contract, idempotency_key=action.idempotency_key)
        return ProviderAttempt(provider_ref=reference.provider_ref, evidence={"provider": "vapi"})


class ActionDispatcher:
    """Claims durable work before invoking exactly one injected executor."""

    def __init__(
        self,
        repository: ActionRepository,
        executors: dict[str, ActionExecutor],
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._executors = executors
        self._retry_policy = retry_policy or RetryPolicy()

    async def dispatch(
        self,
        user_id: str,
        action_id: str,
        *,
        correlation_id: str = "action-dispatch",
        now: datetime | None = None,
    ) -> ActionRecord:
        check_at = now or datetime.now(timezone.utc)
        claim = await self._repository.claim_dispatch(user_id, action_id, check_at, correlation_id)
        if not claim.claimed:
            if claim.action is not None:
                return claim.action
            action = await self._repository.get(user_id, action_id)
            if action is None:
                raise LookupError
            return action

        action = claim.action
        if action is None:
            raise RuntimeError("A successful dispatch claim must include its action")
        executor = self._executors.get(action.type)
        if executor is None:
            return action
        try:
            attempt = await executor.execute(action)
        except ProviderFailure as error:
            decision = self._retry_policy.next_attempt(action, error, now=check_at)
            return await self._record_failure(user_id, action, decision, correlation_id)

        updated = await self._repository.mark_provider_request(
            user_id,
            action_id,
            attempt.provider_ref,
            attempt.evidence,
            correlation_id,
        )
        await self._repository.complete_dispatch_record(user_id, action_id, correlation_id)
        return updated

    async def _record_failure(
        self,
        user_id: str,
        action: ActionRecord,
        decision: RetryDecision,
        correlation_id: str,
    ) -> ActionRecord:
        return await self._repository.record_dispatch_failure(
            user_id,
            action.id,
            decision.state,
            decision.attempt,
            decision.reason,
            correlation_id,
        )


__all__ = ["ActionDispatcher", "ActionExecutor", "ProviderAttempt", "VoiceCallExecutor"]
