from __future__ import annotations

from hashlib import sha256
from typing import Protocol

from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import async_transactional

from app.contracts import AuditLogEntry, ProviderEvent
from app.repositories.firestore import firestore_data, user_document, utc_now


class ProviderEventRepository(Protocol):
    async def record_once(self, event: ProviderEvent) -> bool: ...


class FirestoreProviderEventRepository:
    def __init__(self, client: AsyncClient, *, user_id: str) -> None:
        self._client = client
        self._user_id = user_id

    def _document(self, event: ProviderEvent):
        return self._client.document(user_document(self._user_id, "provider_events", event.id))

    async def record_once(self, event: ProviderEvent) -> bool:
        document = self._document(event)

        @async_transactional
        async def record(transaction):
            snapshot = await document.get(transaction=transaction)
            if snapshot.exists:
                existing = snapshot.to_dict()
                if existing.get("payload_hash") != event.payload_hash:
                    audit_id = "provider-collision-" + sha256(event.id.encode()).hexdigest()[:32]
                    audit_document = self._client.document(
                        user_document(self._user_id, "audit_log", audit_id)
                    )
                    audit_snapshot = await audit_document.get(transaction=transaction)
                    if not audit_snapshot.exists:
                        audit = AuditLogEntry(
                            id=audit_id,
                            user_id=self._user_id,
                            outcome="provider_event_hash_collision",
                            correlation_id=event.correlation_id,
                            source_event_key=event.provider_event_key,
                            created_at=utc_now(),
                            updated_at=utc_now(),
                            payload={
                                "provider_event_id": event.id,
                                "stored_payload_hash": str(existing.get("payload_hash", "")),
                                "received_payload_hash": str(event.payload_hash or ""),
                            },
                        )
                        transaction.create(audit_document, firestore_data(audit))
                return False
            transaction.create(
                document,
                {
                    **firestore_data(event),
                    "user_id": self._user_id,
                },
            )
            return True

        return await record(self._client.transaction())


__all__ = ["FirestoreProviderEventRepository", "ProviderEventRepository"]
