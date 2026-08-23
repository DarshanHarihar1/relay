from __future__ import annotations

from typing import Protocol

from google.cloud.firestore_v1 import AsyncClient

from app.contracts import Commitment
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document


class CommitmentRepository(Protocol):
    async def get(self, user_id: str, commitment_id: str) -> Commitment | None: ...

    async def create(self, commitment: Commitment) -> Commitment: ...


class FirestoreCommitmentRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def _document(self, user_id: str, commitment_id: str):
        return self._client.document(user_document(user_id, "commitments", commitment_id))

    async def get(self, user_id: str, commitment_id: str) -> Commitment | None:
        snapshot = await self._document(user_id, commitment_id).get()
        if not snapshot.exists:
            return None
        return Commitment.model_validate(as_aware_datetimes(snapshot.to_dict()))

    async def create(self, commitment: Commitment) -> Commitment:
        await self._document(commitment.user_id, commitment.id).create(firestore_data(commitment))
        return commitment
