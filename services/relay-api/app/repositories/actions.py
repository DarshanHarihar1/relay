from __future__ import annotations

from datetime import datetime
from typing import Protocol

from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import async_transactional

from app.contracts import ActionRecord, ActionState, Approval, ApprovalDecisionRequest, ApprovalDecisionResponse, DispatchClaim
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document, utc_now
from app.services.action_state import derive_action_idempotency_key, validate_transition


class ApprovalVersionConflict(Exception):
    """The approval has already changed since the user last read it."""


class ActionRepository(Protocol):
    async def get(self, user_id: str, action_id: str) -> ActionRecord | None: ...

    async def create(self, action: ActionRecord) -> ActionRecord: ...

    async def create_approval(self, approval: Approval) -> Approval: ...

    async def decide_approval(
        self,
        user_id: str,
        request: ApprovalDecisionRequest,
        correlation_id: str,
    ) -> ApprovalDecisionResponse: ...

    async def claim_dispatch(
        self,
        user_id: str,
        action_id: str,
        now: datetime,
        correlation_id: str,
    ) -> DispatchClaim: ...


class FirestoreActionRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def _document(self, user_id: str, action_id: str):
        return self._client.document(user_document(user_id, "actions", action_id))

    def _approval_document(self, user_id: str, approval_id: str):
        return self._client.document(user_document(user_id, "approvals", approval_id))

    async def get(self, user_id: str, action_id: str) -> ActionRecord | None:
        snapshot = await self._document(user_id, action_id).get()
        if not snapshot.exists:
            return None
        return ActionRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))

    async def create(self, action: ActionRecord) -> ActionRecord:
        await self._document(action.user_id, action.id).create(firestore_data(action))
        return action

    async def create_approval(self, approval: Approval) -> Approval:
        await self._approval_document(approval.user_id, approval.id).create(firestore_data(approval))
        return approval

    async def decide_approval(
        self,
        user_id: str,
        request: ApprovalDecisionRequest,
        correlation_id: str,
    ) -> ApprovalDecisionResponse:
        document = self._approval_document(user_id, request.approval_id)

        @async_transactional
        async def decide(transaction):
            snapshot = await document.get(transaction=transaction)
            if not snapshot.exists:
                raise LookupError

            approval = Approval.model_validate(as_aware_datetimes(snapshot.to_dict()))
            if approval.version != request.expected_version or approval.state != "awaiting_approval":
                raise ApprovalVersionConflict

            actions: list[tuple[object, ActionRecord]] = []
            for action_id in approval.action_ids:
                action_document = self._document(user_id, action_id)
                action_snapshot = await action_document.get(transaction=transaction)
                if not action_snapshot.exists:
                    raise ApprovalVersionConflict
                action = ActionRecord.model_validate(as_aware_datetimes(action_snapshot.to_dict()))
                if action.state is not ActionState.AWAITING_APPROVAL:
                    raise ApprovalVersionConflict
                actions.append((action_document, action))

            approved = request.decision == "approve"
            requested_state = ActionState.AUTHORIZED if approved else ActionState.NEEDS_USER
            changed_at = utc_now()
            for action_document, action in actions:
                validate_transition(action.state, requested_state)
                updated_action = action.model_copy(
                    update={
                        "state": requested_state,
                        "updated_at": changed_at,
                        "correlation_id": correlation_id,
                        "version": action.version + 1,
                    }
                )
                transaction.update(action_document, firestore_data(updated_action))

            updated_approval = approval.model_copy(
                update={
                    "state": "approved" if approved else "declined",
                    "updated_at": changed_at,
                    "correlation_id": correlation_id,
                    "version": approval.version + 1,
                }
            )
            transaction.update(document, firestore_data(updated_approval))
            return ApprovalDecisionResponse(
                approval_id=approval.id,
                state=updated_approval.state,
                action_ids=approval.action_ids,
            )

        return await decide(self._client.transaction())

    async def claim_dispatch(
        self,
        user_id: str,
        action_id: str,
        now: datetime,
        correlation_id: str,
    ) -> DispatchClaim:
        document = self._document(user_id, action_id)

        @async_transactional
        async def claim(transaction):
            snapshot = await document.get(transaction=transaction)
            if not snapshot.exists:
                return DispatchClaim(claimed=False)

            action = ActionRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))
            if action.state is not ActionState.AUTHORIZED:
                return DispatchClaim(claimed=False, action=action)

            if action.expires_at is not None and action.expires_at <= now:
                validate_transition(action.state, ActionState.NEEDS_USER)
                expired_action = action.model_copy(
                    update={
                        "state": ActionState.NEEDS_USER,
                        "verification_evidence": {"reason": "authorization_expired"},
                        "updated_at": now,
                        "correlation_id": correlation_id,
                        "version": action.version + 1,
                    }
                )
                transaction.update(document, firestore_data(expired_action))
                return DispatchClaim(claimed=False, action=expired_action)

            stored_key = derive_action_idempotency_key(
                action.repair_plan_version,
                action.type,
                action.target_ref,
                action.authorization_snapshot,
            )
            if action.idempotency_key != stored_key:
                return DispatchClaim(claimed=False, action=action)

            validate_transition(action.state, ActionState.DISPATCHED)
            claimed_action = action.model_copy(
                update={
                    "state": ActionState.DISPATCHED,
                    "dispatched_at": now,
                    "updated_at": now,
                    "correlation_id": correlation_id,
                    "version": action.version + 1,
                }
            )
            transaction.update(document, firestore_data(claimed_action))
            return DispatchClaim(claimed=True, action=claimed_action)

        return await claim(self._client.transaction())
