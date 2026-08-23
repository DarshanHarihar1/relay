from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


USER_COLLECTIONS = frozenset(
    {
        "policies",
        "commitments",
        "edges",
        "disruptions",
        "impact_assessments",
        "repair_plans",
        "approvals",
        "actions",
        "action_dispatches",
        "provider_events",
        "source_events",
        "audit_log",
        "outbox",
        "google_connections",
        "selected_contacts",
        "device_tokens",
        "gmail_claims",
    }
)


def user_document(user_id: str, collection: str, document_id: str) -> str:
    """Return a Firestore path that cannot leave an authenticated user's namespace."""
    if not user_id or "/" in user_id:
        raise ValueError("A non-empty user ID without path separators is required")
    if collection not in USER_COLLECTIONS:
        raise ValueError("Collection is not available in a user namespace")
    if not document_id or "/" in document_id:
        raise ValueError("A non-empty document ID without path separators is required")
    return f"users/{user_id}/{collection}/{document_id}"


def firestore_data(record: Any) -> dict[str, Any]:
    """Serialize Pydantic models while retaining datetime values for Firestore."""
    return record.model_dump(mode="python")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_aware_datetimes(data: Mapping[str, Any]) -> dict[str, Any]:
    """Copy Firestore data and normalize timestamp values before Pydantic validation."""
    normalized = dict(data)
    for name, value in normalized.items():
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            normalized[name] = value.replace(tzinfo=timezone.utc)
    return normalized


class FirestoreDeviceRepository:
    """Stores encrypted browser tokens in the authenticated user's namespace."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def upsert_device(
        self,
        *,
        user_id: str,
        token_fingerprint: str,
        encrypted_token: str,
        platform: str,
        last_seen_at: datetime,
    ) -> None:
        reference = self._client.document(
            user_document(user_id, "device_tokens", token_fingerprint)
        )
        await reference.set(
            {
                "user_id": user_id,
                "token_fingerprint": token_fingerprint,
                "encrypted_token": encrypted_token,
                "platform": platform,
                "last_seen_at": last_seen_at,
            },
            merge=True,
        )

    async def list_devices(self, user_id: str) -> list[dict[str, object]]:
        return [
            snapshot.to_dict() or {}
            async for snapshot in self._client.collection(
                f"users/{user_id}/device_tokens"
            ).stream()
        ]

    async def remove_device(self, *, user_id: str, token_fingerprint: str) -> None:
        await self._client.document(
            user_document(user_id, "device_tokens", token_fingerprint)
        ).delete()
