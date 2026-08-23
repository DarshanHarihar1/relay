from __future__ import annotations

import logging
from datetime import datetime

from google.cloud.firestore_v1 import AsyncClient

from app.contracts import AuditLogEntry
from app.repositories.firestore import firestore_data, user_document


logger = logging.getLogger("relay.retention")

# Which durable collection each retention category lives in.
_COLLECTIONS = {
    "raw_evidence": "source_events",
    "audit_log": "audit_log",
}

# Categories whose stores are still in-process (Tasks 2, 3, 7). They expire on
# their own TTL and have nothing durable to purge, so a purge is a no-op.
_IN_PROCESS_CATEGORIES = frozenset(
    {"picker_sessions", "context_cache", "calendar_free_busy", "revoked_token_ciphertext"}
)


class FirestoreRetentionStore:
    """Deletes expired ingestion data and strips raw evidence off disruptions.

    A disruption is planning state Phase 3 still needs, so its raw evidence is
    cleared in place rather than the record being deleted.
    """

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def purge_older_than(self, *, category: str, cutoff: datetime | None) -> int:
        if category in _IN_PROCESS_CATEGORIES:
            return 0
        collection = _COLLECTIONS.get(category)
        if collection is None:
            return 0
        deleted = await self._delete_before(collection, cutoff)
        if category == "raw_evidence":
            deleted += await self._strip_disruption_evidence(cutoff)
        return deleted

    async def append_audit_event(self, event: AuditLogEntry) -> None:
        document = self._client.document(user_document(event.user_id, "audit_log", event.id))
        await document.create(firestore_data(event))

    async def _delete_before(self, collection: str, cutoff: datetime | None) -> int:
        query = self._client.collection_group(collection).where("created_at", "<", cutoff)
        deleted = 0
        async for snapshot in query.stream():
            await snapshot.reference.delete()
            deleted += 1
        return deleted

    async def _strip_disruption_evidence(self, cutoff: datetime | None) -> int:
        query = self._client.collection_group("disruptions").where("created_at", "<", cutoff)
        stripped = 0
        async for snapshot in query.stream():
            data = snapshot.to_dict() or {}
            if data.get("evidence_excerpt") is None and data.get("gmail_source") is None:
                continue
            # Idempotent: a second run finds both fields already cleared.
            await snapshot.reference.update({"evidence_excerpt": None, "gmail_source": None})
            stripped += 1
        return stripped


__all__ = ["FirestoreRetentionStore"]
