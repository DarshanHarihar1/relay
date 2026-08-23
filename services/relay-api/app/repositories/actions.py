from __future__ import annotations

from datetime import datetime
from typing import Protocol

from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import async_transactional
from google.cloud.firestore_v1.base_query import FieldFilter

from app.contracts import (
    ActionDispatchRecord,
    ActionRecord,
    ActionState,
    Approval,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    AuditLogEntry,
    DispatchClaim,
    JsonValue,
)
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document, utc_now
from app.services.action_state import valid_action_idempotency_keys, validate_transition


class ApprovalVersionConflict(Exception):
    """The approval has already changed since the user last read it."""


class ActionRepository(Protocol):
    async def get(self, user_id: str, action_id: str) -> ActionRecord | None: ...

    async def resolve_action(
        self,
        *,
        action_id: str | None = None,
        provider_ref: str | None = None,
    ) -> tuple[str, ActionRecord] | None: ...

    async def create(self, action: ActionRecord) -> ActionRecord: ...

    async def create_approval(self, approval: Approval) -> Approval: ...

    async def get_dispatch(self, user_id: str, action_id: str) -> ActionDispatchRecord | None: ...

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

    async def mark_provider_request(
        self,
        user_id: str,
        action_id: str,
        provider_ref: str,
        evidence: dict[str, object],
        correlation_id: str,
    ) -> ActionRecord: ...

    async def complete_dispatch_record(self, user_id: str, action_id: str, correlation_id: str) -> None: ...

    async def list_pending_dispatches(self, user_id: str, limit: int) -> list[str]: ...

    async def record_dispatch_failure(
        self,
        user_id: str,
        action_id: str,
        state: ActionState,
        retry_count: int,
        reason: str,
        correlation_id: str,
    ) -> ActionRecord: ...


class FirestoreActionRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def _document(self, user_id: str, action_id: str):
        return self._client.document(user_document(user_id, "actions", action_id))

    def _approval_document(self, user_id: str, approval_id: str):
        return self._client.document(user_document(user_id, "approvals", approval_id))

    def _dispatch_document(self, user_id: str, action_id: str):
        return self._client.document(user_document(user_id, "action_dispatches", action_id))

    def _audit_document(self, user_id: str, audit_id: str):
        return self._client.document(user_document(user_id, "audit_log", audit_id))

    async def get(self, user_id: str, action_id: str) -> ActionRecord | None:
        snapshot = await self._document(user_id, action_id).get()
        if not snapshot.exists:
            return None
        return ActionRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))

    async def create(self, action: ActionRecord) -> ActionRecord:
        await self._document(action.user_id, action.id).create(firestore_data(action))
        return action

    async def resolve_action(
        self,
        *,
        action_id: str | None = None,
        provider_ref: str | None = None,
    ) -> tuple[str, ActionRecord] | None:
        if not action_id and not provider_ref:
            return None
        field = "id" if action_id else "provider_ref"
        value = action_id or provider_ref
        query = self._client.collection_group("actions").where(
            filter=FieldFilter(field, "==", value)
        ).limit(2)
        snapshots = [snapshot async for snapshot in query.stream()]
        if len(snapshots) != 1:
            return None
        action = ActionRecord.model_validate(as_aware_datetimes(snapshots[0].to_dict()))
        return action.user_id, action

    async def create_approval(self, approval: Approval) -> Approval:
        await self._approval_document(approval.user_id, approval.id).create(firestore_data(approval))
        return approval

    async def get_dispatch(self, user_id: str, action_id: str) -> ActionDispatchRecord | None:
        snapshot = await self._dispatch_document(user_id, action_id).get()
        if not snapshot.exists:
            return None
        return ActionDispatchRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))

    async def apply_provider_outcome(
        self,
        user_id: str,
        action_id: str,
        state: ActionState,
        evidence: dict[str, JsonValue],
        correlation_id: str,
    ) -> ActionRecord:
        document = self._document(user_id, action_id)

        @async_transactional
        async def apply(transaction):
            snapshot = await document.get(transaction=transaction)
            if not snapshot.exists:
                raise LookupError
            action = ActionRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))
            if action.state is state:
                return action
            validate_transition(action.state, state)
            now = utc_now()
            updated = action.model_copy(
                update={
                    "state": state,
                    "verification_evidence": evidence,
                    "updated_at": now,
                    "correlation_id": correlation_id,
                    "version": action.version + 1,
                }
            )
            transaction.update(document, firestore_data(updated))
            return updated

        return await apply(self._client.transaction())

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
            if approval.user_id != user_id:
                raise LookupError
            if approval.version != request.expected_version or approval.state != "awaiting_approval":
                raise ApprovalVersionConflict

            if len(set(approval.action_ids)) != len(approval.action_ids):
                raise ApprovalVersionConflict

            action_rows: list[tuple[object, ActionRecord, object, object]] = []
            for action_id in approval.action_ids:
                action_document = self._document(user_id, action_id)
                action_snapshot = await action_document.get(transaction=transaction)
                if not action_snapshot.exists:
                    raise ApprovalVersionConflict
                action = ActionRecord.model_validate(as_aware_datetimes(action_snapshot.to_dict()))
                if action.user_id != user_id:
                    raise ApprovalVersionConflict
                if action.state is not ActionState.AWAITING_APPROVAL:
                    raise ApprovalVersionConflict
                if action.idempotency_key not in valid_action_idempotency_keys(action):
                    raise ApprovalVersionConflict
                dispatch_document = self._dispatch_document(user_id, action_id)
                dispatch_snapshot = None
                if request.decision == "approve":
                    dispatch_snapshot = await dispatch_document.get(transaction=transaction)
                action_rows.append((action_document, action, dispatch_document, dispatch_snapshot))

            plan_identity = {(action.repair_plan_id, action.repair_plan_version) for _, action, _, _ in action_rows}
            if len(plan_identity) != 1:
                raise ApprovalVersionConflict

            changed_at = utc_now()
            if request.decision == "approve":
                if approval.expires_at is not None and approval.expires_at <= changed_at:
                    raise ApprovalVersionConflict
                for _, action, _, dispatch_snapshot in action_rows:
                    if action.type == "voice_call":
                        action_expires_at = action.expires_at or action.authorization_snapshot.expires_at
                        if action_expires_at <= changed_at:
                            raise ApprovalVersionConflict
                    if dispatch_snapshot is not None and dispatch_snapshot.exists:
                        dispatch = ActionDispatchRecord.model_validate(
                            as_aware_datetimes(dispatch_snapshot.to_dict())
                        )
                        if dispatch.status != "pending" or dispatch.action_id != action.id:
                            raise ApprovalVersionConflict

            approved = request.decision == "approve"
            requested_state = ActionState.AUTHORIZED if approved else ActionState.NEEDS_USER
            for action_document, action, dispatch_document, dispatch_snapshot in action_rows:
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
                audit = AuditLogEntry(
                    id=f"approval-{approval.id}-{action.id}-{request.decision}",
                    user_id=user_id,
                    outcome=f"approval_{request.decision}",
                    correlation_id=correlation_id,
                    created_at=changed_at,
                    updated_at=changed_at,
                    payload={"action_state": requested_state.value},
                )
                transaction.create(self._audit_document(user_id, audit.id), firestore_data(audit))
                if approved and (dispatch_snapshot is None or not dispatch_snapshot.exists):
                    dispatch = ActionDispatchRecord(
                        id=action.id,
                        user_id=user_id,
                        action_id=action.id,
                        status="pending",
                        correlation_id=correlation_id,
                        created_at=changed_at,
                        updated_at=changed_at,
                    )
                    transaction.create(dispatch_document, firestore_data(dispatch))

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
        dispatch_document = self._dispatch_document(user_id, action_id)

        @async_transactional
        async def claim(transaction):
            snapshot = await document.get(transaction=transaction)
            if not snapshot.exists:
                return DispatchClaim(claimed=False)

            action = ActionRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))
            dispatch_snapshot = await dispatch_document.get(transaction=transaction)
            dispatch = (
                ActionDispatchRecord.model_validate(as_aware_datetimes(dispatch_snapshot.to_dict()))
                if dispatch_snapshot.exists
                else None
            )
            if action.state is not ActionState.AUTHORIZED:
                return DispatchClaim(
                    claimed=False,
                    action=action,
                    reconciliation_required=(
                        action.state is ActionState.DISPATCHED and action.provider_ref is None
                    ),
                )

            expiry = action.expires_at
            if action.type == "voice_call":
                expiry = expiry or action.authorization_snapshot.expires_at
            if expiry is not None and expiry <= now:
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

            if action.idempotency_key not in valid_action_idempotency_keys(action):
                return DispatchClaim(claimed=False, action=action)
            if dispatch is not None and dispatch.status != "pending":
                return DispatchClaim(claimed=False, action=action, reconciliation_required=True)

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
            claimed_dispatch = ActionDispatchRecord(
                id=action.id,
                user_id=user_id,
                action_id=action.id,
                status="claimed",
                correlation_id=correlation_id,
                attempts=(dispatch.attempts + 1) if dispatch is not None else 1,
                provider_ref=dispatch.provider_ref if dispatch is not None else None,
                created_at=dispatch.created_at if dispatch is not None else now,
                updated_at=now,
                version=(dispatch.version + 1) if dispatch is not None else 1,
            )
            if dispatch is None:
                transaction.create(dispatch_document, firestore_data(claimed_dispatch))
            else:
                transaction.update(dispatch_document, firestore_data(claimed_dispatch))
            return DispatchClaim(claimed=True, action=claimed_action)

        return await claim(self._client.transaction())

    async def mark_provider_request(
        self,
        user_id: str,
        action_id: str,
        provider_ref: str,
        evidence: dict[str, JsonValue],
        correlation_id: str,
    ) -> ActionRecord:
        document = self._document(user_id, action_id)
        dispatch_document = self._dispatch_document(user_id, action_id)

        @async_transactional
        async def mark(transaction):
            snapshot = await document.get(transaction=transaction)
            if not snapshot.exists:
                raise LookupError
            action = ActionRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))
            dispatch_snapshot = await dispatch_document.get(transaction=transaction)
            dispatch = (
                ActionDispatchRecord.model_validate(as_aware_datetimes(dispatch_snapshot.to_dict()))
                if dispatch_snapshot.exists
                else None
            )
            if action.provider_ref is not None:
                return action
            validate_transition(action.state, ActionState.IN_PROGRESS)
            now = utc_now()
            updated_action = action.model_copy(
                update={
                    "state": ActionState.IN_PROGRESS,
                    "provider_ref": provider_ref,
                    "verification_evidence": evidence,
                    "updated_at": now,
                    "correlation_id": correlation_id,
                    "version": action.version + 1,
                }
            )
            transaction.update(document, firestore_data(updated_action))
            if dispatch is not None:
                transaction.update(
                    dispatch_document,
                    firestore_data(
                        dispatch.model_copy(
                            update={
                                "provider_ref": provider_ref,
                                "updated_at": now,
                                "correlation_id": correlation_id,
                                "version": dispatch.version + 1,
                            }
                        )
                    ),
                )
            return updated_action

        return await mark(self._client.transaction())

    async def complete_dispatch_record(self, user_id: str, action_id: str, correlation_id: str) -> None:
        dispatch_document = self._dispatch_document(user_id, action_id)

        @async_transactional
        async def complete(transaction):
            snapshot = await dispatch_document.get(transaction=transaction)
            if not snapshot.exists:
                return
            dispatch = ActionDispatchRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))
            if dispatch.status == "completed":
                return
            now = utc_now()
            transaction.update(
                dispatch_document,
                firestore_data(
                    dispatch.model_copy(
                        update={
                            "status": "completed",
                            "updated_at": now,
                            "correlation_id": correlation_id,
                            "version": dispatch.version + 1,
                        }
                    )
                ),
            )

        await complete(self._client.transaction())

    async def record_dispatch_failure(
        self,
        user_id: str,
        action_id: str,
        state: ActionState,
        retry_count: int,
        reason: str,
        correlation_id: str,
    ) -> ActionRecord:
        document = self._document(user_id, action_id)
        dispatch_document = self._dispatch_document(user_id, action_id)

        @async_transactional
        async def record(transaction):
            snapshot = await document.get(transaction=transaction)
            if not snapshot.exists:
                raise LookupError
            action = ActionRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))
            validate_transition(action.state, state)
            dispatch_snapshot = await dispatch_document.get(transaction=transaction)
            dispatch = (
                ActionDispatchRecord.model_validate(as_aware_datetimes(dispatch_snapshot.to_dict()))
                if dispatch_snapshot.exists
                else None
            )
            now = utc_now()
            updated_action = action.model_copy(
                update={
                    "state": state,
                    "retry_count": retry_count,
                    "verification_evidence": {"reason": reason},
                    "updated_at": now,
                    "correlation_id": correlation_id,
                    "version": action.version + 1,
                }
            )
            transaction.update(document, firestore_data(updated_action))
            if dispatch is not None:
                transaction.update(
                    dispatch_document,
                    firestore_data(
                        dispatch.model_copy(
                            update={
                                "status": "pending" if state is ActionState.RETRYABLE_FAILURE else "completed",
                                "updated_at": now,
                                "correlation_id": correlation_id,
                                "version": dispatch.version + 1,
                            }
                        )
                    ),
                )
            return updated_action

        return await record(self._client.transaction())

    async def list_pending_dispatches(self, user_id: str, limit: int) -> list[str]:
        query = self._client.collection(f"users/{user_id}/action_dispatches").where(
            filter=FieldFilter("status", "==", "pending")
        ).limit(limit)
        return [
            snapshot.id
            async for snapshot in query.stream()
        ]
