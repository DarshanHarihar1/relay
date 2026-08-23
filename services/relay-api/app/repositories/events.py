from __future__ import annotations

from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1 import AsyncClient

from app.contracts import AuditLogEntry, SourceEventEnvelope
from app.repositories.firestore import firestore_data, utc_now, user_document


class EventRepository(Protocol):
    async def record_once(self, user_id: str, event: SourceEventEnvelope) -> bool: ...


def source_event_key(event: SourceEventEnvelope) -> str:
    if event.source != "gmail":
        return event.source_event_key

    message_id = event.payload.get("message_id")
    history_id = event.payload.get("history_id")
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("Gmail events require a message_id")
    if not isinstance(history_id, str) or not history_id:
        raise ValueError("Gmail events require a history_id")
    return f"gmail:{message_id}:{history_id}"


class FirestoreEventRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def record_once(self, user_id: str, event: SourceEventEnvelope) -> bool:
        key = source_event_key(event)
        event_id = sha256(key.encode("utf-8")).hexdigest()
        document = self._client.document(user_document(user_id, "source_events", event_id))
        now = utc_now()
        document_data = {
            "id": event_id,
            "user_id": user_id,
            "source": event.source,
            "source_event_key": key,
            "occurred_at": event.occurred_at,
            "payload": event.payload,
            "correlation_id": event.correlation_id,
            "created_at": now,
            "updated_at": now,
            "version": 1,
        }
        try:
            await document.create(document_data)
            return True
        except AlreadyExists:
            await self._record_duplicate(user_id, key, event.correlation_id)
            return False

    async def _record_duplicate(self, user_id: str, key: str, correlation_id: str) -> None:
        audit = AuditLogEntry(
            id=uuid4().hex,
            user_id=user_id,
            outcome="duplicate_ignored",
            correlation_id=correlation_id,
            source_event_key=key,
        )
        await self._client.document(user_document(user_id, "audit_log", audit.id)).create(firestore_data(audit))
