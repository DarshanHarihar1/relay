from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from google.api_core.exceptions import NotFound
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.base_query import FieldFilter

from app.domain.ingestion import GoogleConnection
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document


# One Google connection per user, at a fixed document ID.
_CONNECTION_ID = "google"
_NONCE_COLLECTION = "oauth_state_nonces"


class FirestoreGoogleStore:
    """Durable replacement for the development in-memory OAuth store.

    Refresh tokens are stored already encrypted by the caller's FieldCipher; this
    class never sees or produces plaintext.
    """

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    # -- OAuth state ------------------------------------------------------

    async def create_state_nonce(self, nonce: str, expires_at: datetime) -> None:
        await self._nonce_document(nonce).create({"expires_at": expires_at})

    async def consume_state_nonce(self, nonce: str) -> bool:
        """Single-use by construction: the delete is what proves we won the race."""
        document = self._nonce_document(nonce)
        snapshot = await document.get()
        if not snapshot.exists:
            return False
        try:
            await document.delete()
        except NotFound:
            return False
        expires_at = (snapshot.to_dict() or {}).get("expires_at")
        if not isinstance(expires_at, datetime):
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)

    # -- Connections ------------------------------------------------------

    async def put_connection(self, connection: GoogleConnection) -> None:
        await self._connection_document(connection.user_id).set(firestore_data(connection))

    async def get_connection(self, user_id: str) -> GoogleConnection | None:
        snapshot = await self._connection_document(user_id).get()
        if not snapshot.exists:
            return None
        return _to_connection(snapshot.to_dict())

    async def delete_connection(self, user_id: str) -> None:
        await self._connection_document(user_id).delete()

    async def get_active_connections_by_gmail_email(
        self, email_address: str
    ) -> list[GoogleConnection]:
        """Resolve a push mailbox. The caller requires exactly one result."""
        query = self._client.collection_group("google_connections").where(
            filter=FieldFilter("gmail_email_address", "==", email_address)
        )
        return [_to_connection(snapshot.to_dict()) async for snapshot in query.stream()]

    async def list_connections_due_for_watch_renewal(
        self, before: datetime
    ) -> list[GoogleConnection]:
        query = self._client.collection_group("google_connections").where(
            filter=FieldFilter("gmail_watch_expires_at", "<=", before)
        )
        return [_to_connection(snapshot.to_dict()) async for snapshot in query.stream()]

    # -- Disconnect cleanup ----------------------------------------------

    async def delete_gmail_cursor(self, user_id: str) -> None:
        # The cursor lives on the connection record, which is deleted with it.
        return None

    async def cancel_watch_renewal(self, user_id: str) -> None:
        # Renewal is driven by gmail_watch_expires_at on the connection record.
        return None

    async def remove_unreferenced_selected_contacts(self, user_id: str) -> None:
        collection = self._client.collection(f"users/{user_id}/selected_contacts")
        async for snapshot in collection.stream():
            await snapshot.reference.delete()

    # -- Helpers ----------------------------------------------------------

    def _connection_document(self, user_id: str):
        return self._client.document(
            user_document(user_id, "google_connections", _CONNECTION_ID)
        )

    def _nonce_document(self, nonce: str):
        # The nonce is hashed so a raw OAuth state value is never a document ID.
        return self._client.document(f"{_NONCE_COLLECTION}/{sha256(nonce.encode()).hexdigest()}")


def _to_connection(data: dict | None) -> GoogleConnection:
    normalized = as_aware_datetimes(data or {})
    scopes = normalized.get("granted_scopes")
    if isinstance(scopes, list):
        normalized["granted_scopes"] = frozenset(scopes)
    return GoogleConnection.model_validate(normalized)


__all__ = ["FirestoreGoogleStore"]
