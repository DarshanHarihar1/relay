from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from app.contracts import ActionRecord, ActionState, CallContract, JsonValue, validate_call_outcome


class ReconciliationRepository(Protocol):
    async def get(self, user_id: str, action_id: str) -> ActionRecord | None: ...

    async def apply_provider_outcome(
        self,
        user_id: str,
        action_id: str,
        state: ActionState,
        evidence: dict[str, JsonValue],
        correlation_id: str,
    ) -> ActionRecord: ...

    async def list_actions_requiring_reconciliation(
        self, now: datetime, limit: int
    ) -> list[ActionRecord]: ...


class VapiOutcomeReader(Protocol):
    async def get_final_outcome(self, provider_ref: str): ...


class CalendarVerifier(Protocol):
    async def verify_private_hold(self, action: ActionRecord): ...


class ReconciliationService:
    def __init__(
        self,
        repository: ReconciliationRepository,
        *,
        vapi: VapiOutcomeReader | None,
        calendar: CalendarVerifier | None,
        user_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._vapi = vapi
        self._calendar = calendar
        self._user_id = user_id

    async def reconcile(
        self,
        action_id: str,
        *,
        user_id: str | None = None,
        now: datetime | None = None,
        correlation_id: str = "reconciliation",
    ) -> ActionRecord:
        scope = user_id or self._user_id
        if not scope:
            raise ValueError("A user ID is required for action reconciliation")
        action = await self._repository.get(scope, action_id)
        if action is None:
            raise LookupError
        check_at = now or datetime.now(timezone.utc)

        if action.type == "voice_call" and action.provider_ref and self._vapi is not None:
            outcome = await self._vapi.get_final_outcome(action.provider_ref)
            if outcome is not None:
                contract_values = action.authorization_snapshot.model_dump(mode="python")
                contract_values.pop("type", None)
                contract = CallContract.model_validate({**contract_values, "action_id": action.id})
                validation = validate_call_outcome(contract, outcome, now=check_at)
                state = (
                    ActionState.SUCCEEDED
                    if validation.state is ActionState.SUCCEEDED
                    else ActionState.NEEDS_USER
                )
                return await self._repository.apply_provider_outcome(
                    scope,
                    action.id,
                    state,
                    {
                        "reason": validation.reason,
                        "missing_evidence": validation.missing_evidence,
                    },
                    correlation_id,
                )
            if action.state in {ActionState.DISPATCHED, ActionState.IN_PROGRESS}:
                return await self._repository.apply_provider_outcome(
                    scope,
                    action.id,
                    ActionState.NEEDS_USER,
                    {"reason": "provider_outcome_unavailable"},
                    correlation_id,
                )

        if action.type == "calendar_hold" and action.state is ActionState.SUCCEEDED and self._calendar:
            verification = await self._calendar.verify_private_hold(action)
            if verification.state is action.state:
                return action
            return await self._repository.apply_provider_outcome(
                scope,
                action.id,
                verification.state,
                verification.evidence,
                correlation_id,
            )

        return action

    async def reconcile_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
        user_id: str | None = None,
        correlation_id: str = "reconciliation",
    ) -> list[ActionRecord]:
        check_at = now or datetime.now(timezone.utc)
        actions = await self._repository.list_actions_requiring_reconciliation(check_at, limit)
        results: list[ActionRecord] = []
        for action in actions:
            if user_id is not None and action.user_id != user_id:
                continue
            results.append(
                await self.reconcile(
                    action.id,
                    user_id=action.user_id,
                    now=check_at,
                    correlation_id=correlation_id,
                )
            )
        return results


__all__ = ["ReconciliationService"]
