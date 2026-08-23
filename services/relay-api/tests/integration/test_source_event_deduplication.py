from datetime import datetime, timezone

import pytest

from app.contracts import SourceEventEnvelope


def gmail_event(*, message_id: str, history_id: str) -> SourceEventEnvelope:
    return SourceEventEnvelope(
        source="gmail",
        source_event_key="ignored-for-gmail",
        occurred_at=datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
        payload={"message_id": message_id, "history_id": history_id},
        correlation_id="correlation-1",
    )


@pytest.mark.emulator
async def test_record_once_accepts_the_same_gmail_event_only_once(events):
    event = gmail_event(message_id="m-1", history_id="h-2")

    assert await events.record_once("user-a", event) is True
    assert await events.record_once("user-a", event) is False
