from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.contracts import ProviderEvent
from app.repositories.provider_events import FirestoreProviderEventRepository


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def provider_event(payload_hash: str = "a" * 64) -> ProviderEvent:
    return ProviderEvent(
        id="vapi:event-1",
        action_id="action-1",
        provider="vapi",
        provider_event_key="event-1",
        event_type="record_call_outcome",
        payload_hash=payload_hash,
        occurred_at=NOW,
        correlation_id="corr-1",
    )


@pytest.mark.emulator
async def test_provider_event_record_once_deduplicates_same_hash(firestore_client):
    repository = FirestoreProviderEventRepository(firestore_client, user_id="user-1")

    assert await repository.record_once(provider_event()) is True
    assert await repository.record_once(provider_event()) is False


@pytest.mark.emulator
async def test_provider_event_hash_collision_is_audited_without_overwrite(firestore_client):
    repository = FirestoreProviderEventRepository(firestore_client, user_id="user-1")

    assert await repository.record_once(provider_event()) is True
    assert await repository.record_once(provider_event("b" * 64)) is False
