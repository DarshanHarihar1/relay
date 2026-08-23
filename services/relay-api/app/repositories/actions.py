from __future__ import annotations

from datetime import datetime
from typing import Protocol

from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import async_transactional

from app.contracts import ActionRecord, ActionState, DispatchClaim
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document


class ActionRepository(Protocol):
    async def get(self, user_id: str, action_id: str) -> ActionRecord | None: ...

    async def create(self, action: ActionRecord) -> ActionRecord: ...

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

    async def get(self, user_id: str, action_id: str) -> ActionRecord | None:
        snapshot = await self._document(user_id, action_id).get()
        if not snapshot.exists:
            return None
        return ActionRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))

    async def create(self, action: ActionRecord) -> ActionRecord:
        await self._document(action.user_id, action.id).create(firestore_data(action))
        return action

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
