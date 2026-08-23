from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import ActionRecord, ActionState
from app.providers.calendar import CalendarAdapter, deterministic_calendar_event_id


NOW = datetime(2027, 8, 23, 12, 0, tzinfo=timezone.utc)


def calendar_action() -> ActionRecord:
    return ActionRecord(
        id="hold-1",
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
        state=ActionState.SUCCEEDED,
        correlation_id="corr-1",
    )


class ReadbackClient:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def insert(self, calendar_id: str, event: dict) -> dict:
        del calendar_id, event
        return self.result

    async def get(self, calendar_id: str, event_id: str) -> dict:
        del calendar_id, event_id
        return self.result


@pytest.mark.asyncio
async def test_readback_mismatch_is_not_verified() -> None:
    action = calendar_action()
    client = ReadbackClient(
        {
            "id": deterministic_calendar_event_id(action.id),
            "visibility": "default",
            "status": "confirmed",
        }
    )

    verification = await CalendarAdapter(client).verify_private_hold(action)

    assert verification.state is ActionState.NEEDS_USER


@pytest.mark.asyncio
async def test_matching_private_readback_is_verified() -> None:
    action = calendar_action()
    client = ReadbackClient(
        {
            "id": deterministic_calendar_event_id(action.id),
            "visibility": "private",
            "status": "confirmed",
            "start": {"dateTime": NOW.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": (NOW + timedelta(hours=1)).isoformat(), "timeZone": "UTC"},
            "extendedProperties": {"private": {"relay_action_id": action.id}},
        }
    )

    verification = await CalendarAdapter(client).verify_private_hold(action)

    assert verification.state is ActionState.VERIFIED
    assert "event_id_hash" in verification.evidence
