from __future__ import annotations

from datetime import datetime
from typing import Protocol

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1 import AsyncClient

from app.contracts import Commitment, Disruption
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document


class DisruptionRepository(Protocol):
    async def create_disruption_if_absent(self, disruption: Disruption) -> bool: ...


class FirestoreDisruptionRepository:
    """Creates one disruption per (source event, commitment) pair, or none."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create_disruption_if_absent(self, disruption: Disruption) -> bool:
        document = self._client.document(
            user_document(disruption.user_id, "disruptions", disruption.id)
        )
        try:
            # `create` fails if the document exists, so a redelivered source event
            # cannot produce a second disruption.
            await document.create(firestore_data(disruption))
            return True
        except AlreadyExists:
            return False

    async def list_commitments_in_window(
        self, *, user_id: str, start: datetime, end: datetime
    ) -> list[Commitment]:
        query = (
            self._client.collection(f"users/{user_id}/commitments")
            .where("starts_at", ">=", start)
            .where("starts_at", "<=", end)
        )
        return [
            Commitment.model_validate(as_aware_datetimes(snapshot.to_dict()))
            async for snapshot in query.stream()
        ]


__all__ = ["DisruptionRepository", "FirestoreDisruptionRepository"]
