from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.adapters.errors import RetryableProviderError, TerminalProviderError
from app.contracts import ActionRecord, ActionState


class CalendarConflict(Exception):
    """The event may already exist and must be read before another write."""


@dataclass(frozen=True)
class CalendarWriteResult:
    event_id: str
    created: bool


@dataclass(frozen=True)
class CalendarVerification:
    state: ActionState
    evidence: dict[str, str]
    reason: str


class CalendarClient(Protocol):
    async def insert(self, calendar_id: str, event: dict[str, Any]) -> dict[str, Any]: ...

    async def get(self, calendar_id: str, event_id: str) -> dict[str, Any]: ...


class GoogleCalendarEventsClient:
    def __init__(
        self,
        *,
        access_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._access_token = access_token
        self._transport = transport
        self._timeout = timeout

    async def insert(self, calendar_id: str, event: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/calendars/{quote(calendar_id, safe='')}/events",
            json=event,
        )

    async def get(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}",
        )

    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.request(
                    method,
                    f"https://www.googleapis.com/calendar/v3{path}",
                    json=json,
                    headers={"Authorization": f"Bearer {self._access_token}"},
                )
        except httpx.TimeoutException as error:
            raise RetryableProviderError("Calendar request timed out") from error
        if response.status_code == 409:
            raise CalendarConflict
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableProviderError(f"Calendar unavailable: {response.status_code}")
        if response.status_code >= 400:
            raise TerminalProviderError(f"Calendar rejected the request: {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise TerminalProviderError("Calendar returned an invalid response")
        return payload


def deterministic_calendar_event_id(action_id: str) -> str:
    encoded = base64.b32hexencode(sha256(action_id.encode()).digest()).decode().lower().rstrip("=")
    return "r" + encoded[:40]


class CalendarAdapter:
    def __init__(self, client: CalendarClient) -> None:
        self._client = client

    async def create_or_get_private_hold(self, action: ActionRecord) -> CalendarWriteResult:
        if action.type != "calendar_hold":
            raise ValueError("CalendarAdapter only accepts calendar_hold actions")
        event_id = deterministic_calendar_event_id(action.id)
        event = self._event(action, event_id)
        try:
            await self._client.insert(action.authorization_snapshot.calendar_id, event)
            created = True
        except (CalendarConflict, TimeoutError, httpx.TimeoutException):
            created = False
        existing = await self._client.get(action.authorization_snapshot.calendar_id, event_id)
        if not self._matches(action, existing):
            raise CalendarConflict("Calendar hold could not be matched after write uncertainty")
        return CalendarWriteResult(event_id=event_id, created=created)

    async def verify_private_hold(self, action: ActionRecord) -> CalendarVerification:
        if action.type != "calendar_hold":
            raise ValueError("CalendarAdapter only accepts calendar_hold actions")
        event_id = deterministic_calendar_event_id(action.id)
        try:
            event = await self._client.get(action.authorization_snapshot.calendar_id, event_id)
        except (CalendarConflict, TerminalProviderError):
            return CalendarVerification(ActionState.NEEDS_USER, {}, "calendar_hold_missing")
        if not self._matches(action, event):
            return CalendarVerification(ActionState.NEEDS_USER, {}, "calendar_hold_mismatch")
        return CalendarVerification(
            ActionState.VERIFIED,
            {"event_id_hash": sha256(event_id.encode()).hexdigest()},
            "calendar_hold_readback_verified",
        )

    @staticmethod
    def _event(action: ActionRecord, event_id: str) -> dict[str, Any]:
        snapshot = action.authorization_snapshot
        return {
            "id": event_id,
            "summary": "Relay private hold",
            "start": {"dateTime": snapshot.start_at.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": snapshot.end_at.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
            "visibility": "private",
            "transparency": "opaque",
            "extendedProperties": {
                "private": {"relay_action_id": action.id, "relay_kind": "private_hold"}
            },
        }

    @staticmethod
    def _matches(action: ActionRecord, event: dict[str, Any]) -> bool:
        if event.get("id") != deterministic_calendar_event_id(action.id):
            return False
        if event.get("visibility") != "private" or event.get("status") == "cancelled":
            return False
        private = event.get("extendedProperties", {}).get("private", {})
        if not isinstance(private, dict) or private.get("relay_action_id") != action.id:
            return False
        snapshot = action.authorization_snapshot
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")
        return _parse_datetime(start) == snapshot.start_at.astimezone(timezone.utc) and _parse_datetime(end) == snapshot.end_at.astimezone(timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


__all__ = [
    "CalendarAdapter",
    "CalendarClient",
    "CalendarConflict",
    "CalendarVerification",
    "CalendarWriteResult",
    "GoogleCalendarEventsClient",
    "deterministic_calendar_event_id",
]
