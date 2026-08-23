from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.base_query import FieldFilter

from app.contracts import Commitment, Edge
from app.repositories.firestore import as_aware_datetimes, user_document


class RelayRepository(Protocol):
    async def get_commitment(self, *, user_id: str, commitment_id: str) -> Commitment | None: ...

    async def get_commitments(self, *, user_id: str, commitment_ids: Sequence[str]) -> list[Commitment]: ...

    async def list_outgoing_edges(self, *, user_id: str, from_id: str) -> list[Edge]: ...


class FirestoreRelayRepository:
    """Read-only graph access for phase-03 planning. Never writes a commitment or edge."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get_commitment(self, *, user_id: str, commitment_id: str) -> Commitment | None:
        snapshot = await self._client.document(user_document(user_id, "commitments", commitment_id)).get()
        if not snapshot.exists:
            return None
        return Commitment.model_validate(as_aware_datetimes(snapshot.to_dict()))

    async def get_commitments(self, *, user_id: str, commitment_ids: Sequence[str]) -> list[Commitment]:
        commitments = []
        for commitment_id in commitment_ids:
            commitment = await self.get_commitment(user_id=user_id, commitment_id=commitment_id)
            if commitment is not None:
                commitments.append(commitment)
        return commitments

    async def list_outgoing_edges(self, *, user_id: str, from_id: str) -> list[Edge]:
        query = self._client.collection(f"users/{user_id}/edges").where(
            filter=FieldFilter("from_ref", "==", from_id)
        )
        return [Edge.model_validate(as_aware_datetimes(snapshot.to_dict())) async for snapshot in query.stream()]
