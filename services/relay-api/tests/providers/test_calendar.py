from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import ActionRecord, ActionState
from app.providers.calendar import (
    CalendarAdapter,
    CalendarConflict,
    deterministic_calendar_event_id,
)


NOW = datetime(2027, 8, 23, 12, 0, tzinfo=timezone.utc)


def calendar_action(action_id: str = "hold-1") -> ActionRecord:
    return ActionRecord(
        id=action_id,
        user_id="user-1",
        repair_plan_id="plan-1",
        repair_plan_version=1,
        type="calendar_hold",
        target_ref="calendar:primary",
        idempotency_key="akey_hold",
        authorization_snapshot={
            "type": "calendar_hold",
            "calendar_id": "primary",
            "start_at": NOW,
            "end_at": NOW + timedelta(hours=1),
            "visibility": "private",
        },
        state=ActionState.IN_PROGRESS,
        correlation_id="corr-1",
    )


class FakeCalendarClient:
    def __init__(self) -> None:
        self.insert_calls = 0
        self.get_calls = 0
        self.insert_result: dict | None = None
        self.get_result: dict | None = None
        self.insert_error: Exception | None = None

    async def insert(self, calendar_id: str, event: dict) -> dict:
        del calendar_id
        self.insert_calls += 1
        if self.insert_error:
            raise self.insert_error
        self.insert_result = event
        return event

    async def get(self, calendar_id: str, event_id: str) -> dict:
        del calendar_id, event_id
        self.get_calls += 1
        return self.get_result or self.insert_result or {}


@pytest.mark.asyncio
async def test_calendar_conflict_fetches_existing_deterministic_hold() -> None:
    client = FakeCalendarClient()
    action = calendar_action()
    client.insert_error = CalendarConflict()
    client.get_result = {
        "id": deterministic_calendar_event_id(action.id),
        "visibility": "private",
        "status": "confirmed",
        "start": {"dateTime": NOW.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": (NOW + timedelta(hours=1)).isoformat(), "timeZone": "UTC"},
        "extendedProperties": {"private": {"relay_action_id": action.id}},
    }

    result = await CalendarAdapter(client).create_or_get_private_hold(action)

    assert result.event_id == deterministic_calendar_event_id("hold-1")
    assert client.insert_calls == 1
    assert client.get_calls == 1


def test_calendar_event_id_uses_lowercase_base32hex() -> None:
    event_id = deterministic_calendar_event_id("hold-1")

    assert event_id.startswith("r")
    assert len(event_id) == 41
    assert set(event_id[1:]) <= set("0123456789abcdefghijklmnopqrstuv")
