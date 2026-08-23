from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import uuid4

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1 import AsyncClient

from app.adapters.gmail import GmailTerminalError
from app.contracts import AuditLogEntry, SourceEventEnvelope
from app.domain.ingestion import GoogleConnection
from app.repositories.firestore import firestore_data, user_document, utc_now
from app.repositories.google_connections import FirestoreGoogleStore


class FirestoreGmailIngestionRepository:
    """Durable cursor, source-event claim, and audit storage.

    The claim is a document create, so two Cloud Run instances processing the
    same redelivered notification cannot both win it.
    """

    def __init__(self, client: AsyncClient, *, connections: FirestoreGoogleStore) -> None:
        self._client = client
        self._connections = connections

    # -- Connections ------------------------------------------------------

    async def get_connection(self, user_id: str) -> GoogleConnection | None:
        return await self._connections.get_connection(user_id)

    async def put_connection(self, connection: GoogleConnection) -> None:
        await self._connections.put_connection(connection)

    async def list_connections_due_for_watch_renewal(
        self, before: datetime
    ) -> list[GoogleConnection]:
        return await self._connections.list_connections_due_for_watch_renewal(before)

    # -- Cursor -----------------------------------------------------------

    async def get_gmail_cursor(self, *, user_id: str, mailbox: str) -> int | None:
        connection = await self._connections.get_connection(user_id)
        if connection is None or connection.gmail_email_address != mailbox:
            return None
        return connection.gmail_history_id

    async def update_gmail_cursor_if_newer(
        self, *, user_id: str, mailbox: str, proposed_history_id: int
    ) -> int:
        connection = await self._connections.get_connection(user_id)
        if connection is None or connection.gmail_email_address != mailbox:
            raise GmailTerminalError("No active Gmail connection is available")
        current = connection.gmail_history_id or 0
        if proposed_history_id <= current:
            return current
        await self._connections.put_connection(
            connection.model_copy(update={"gmail_history_id": proposed_history_id})
        )
        return proposed_history_id

    # -- Source-event claims ----------------------------------------------

    async def claim_source_event(self, *, user_id: str, event: SourceEventEnvelope) -> bool:
        document = self._client.document(
            user_document(user_id, "gmail_claims", _claim_id(event.source_event_key))
        )
        try:
            await document.create(
                {
                    "source_event_key": event.source_event_key,
                    "correlation_id": event.correlation_id,
                    "created_at": utc_now(),
                }
            )
            return True
        except AlreadyExists:
            return False

    async def release_source_event_claim(self, *, user_id: str, source_event_key: str) -> None:
        await self._client.document(
            user_document(user_id, "gmail_claims", _claim_id(source_event_key))
        ).delete()

    # -- Audit ------------------------------------------------------------

    async def append_ingestion_audit(
        self,
        *,
        user_id: str,
        outcome: str,
        correlation_id: str,
        source_event_key: str | None = None,
        detail: dict[str, str] | None = None,
    ) -> None:
        entry = AuditLogEntry(
            id=uuid4().hex,
            user_id=user_id,
            outcome=outcome,
            correlation_id=correlation_id,
            source_event_key=source_event_key,
            # Reasons, counts, and hashes only. Never message content.
            payload=detail or {},
        )
        await self._client.document(
            user_document(user_id, "audit_log", entry.id)
        ).create(firestore_data(entry))


def _claim_id(source_event_key: str) -> str:
    return sha256(source_event_key.encode("utf-8")).hexdigest()


__all__ = ["FirestoreGmailIngestionRepository"]
