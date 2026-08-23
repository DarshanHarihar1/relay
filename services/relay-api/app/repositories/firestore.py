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
