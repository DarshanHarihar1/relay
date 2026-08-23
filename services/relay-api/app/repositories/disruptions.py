from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from google.cloud.firestore_v1 import AsyncClient, async_transactional
from google.cloud.firestore_v1.base_query import FieldFilter

from app.contracts import Commitment, Disruption
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document, utc_now
from app.services.retention import AssessDisruption


logger = logging.getLogger("relay.disruptions")


class DisruptionRepository(Protocol):
    async def create_disruption_if_absent(
        self, disruption: Disruption, *, assessment: AssessDisruption | None = None
    ) -> bool: ...


class CommandPublisher(Protocol):
    async def publish(self, command: AssessDisruption) -> None: ...


class FirestoreDisruptionRepository:
    """Creates one disruption per (source event, commitment) pair, or none.

    The Phase 3 assessment command is written into the user's outbox inside the
    same transaction as the disruption, so a crash can never leave a disruption
    whose assessment was silently dropped.
    """

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create_disruption_if_absent(
        self, disruption: Disruption, *, assessment: AssessDisruption | None = None
    ) -> bool:
        disruption_ref = self._client.document(
            user_document(disruption.user_id, "disruptions", disruption.id)
        )
        outbox_ref = self._client.document(
            user_document(disruption.user_id, "outbox", disruption.id)
        )
        payload = firestore_data(disruption)
        command = assessment.model_dump(mode="python") if assessment is not None else None

        @async_transactional
        async def create(transaction) -> bool:
            existing = await disruption_ref.get(transaction=transaction)
            if existing.exists:
                return False
            transaction.create(disruption_ref, payload)
            if command is not None:
                transaction.create(
                    outbox_ref,
                    {
                        "id": disruption.id,
                        "user_id": disruption.user_id,
                        "command": command,
                        "created_at": utc_now(),
                        "published_at": None,
                    },
                )
            return True

        return await create(self._client.transaction())

    async def list_commitments_in_window(
        self, *, user_id: str, start: datetime, end: datetime
    ) -> list[Commitment]:
        query = (
            self._client.collection(f"users/{user_id}/commitments")
            .where(filter=FieldFilter("starts_at", ">=", start))
            .where(filter=FieldFilter("starts_at", "<=", end))
        )
        return [
            Commitment.model_validate(as_aware_datetimes(snapshot.to_dict()))
            async for snapshot in query.stream()
        ]


class FirestoreOutbox:
    """Publishes assessment commands that are already durably recorded.

    Publishing is at-least-once: a command is marked published only after the
    publisher accepts it, so a failure simply leaves it for the next drain.
    """

    def __init__(self, client: AsyncClient, *, publisher: CommandPublisher) -> None:
        self._client = client
        self._publisher = publisher

    async def enqueue_assessment(self, command: AssessDisruption) -> None:
        """Publish immediately. A failure is not fatal; the drain retries it."""
        try:
            await self._publish(command)
        except Exception:  # noqa: BLE001 - the command is safe in the outbox
            logger.warning(
                "assessment_publish_deferred", extra={"correlation_id": command.correlation_id}
            )

    async def drain(self, *, limit: int = 100) -> int:
        published = 0
        query = (
            self._client.collection_group("outbox")
            .where(filter=FieldFilter("published_at", "==", None))
            .limit(limit)
        )
        async for snapshot in query.stream():
            data = snapshot.to_dict() or {}
            try:
                command = AssessDisruption.model_validate(data["command"])
            except (KeyError, ValueError):
                logger.warning("outbox_entry_malformed")
                continue
            try:
                await self._publish(command)
            except Exception:  # noqa: BLE001 - retried on the next drain
                logger.warning(
                    "assessment_publish_failed",
                    extra={"correlation_id": command.correlation_id},
                )
                continue
            await snapshot.reference.update({"published_at": utc_now()})
            published += 1
        return published

    async def _publish(self, command: AssessDisruption) -> None:
        await self._publisher.publish(command)


__all__ = [
    "CommandPublisher",
    "DisruptionRepository",
    "FirestoreDisruptionRepository",
    "FirestoreOutbox",
]
