from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from app.adapters.errors import RetryableProviderError
from app.domain.context import CalendarWindow, PlaceDetails, PlaceRef, RouteEstimate, TimeInterval
from app.domain.ingestion import GoogleConnection


_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_FREE_BUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
_COMPUTE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_PLACES_URL = "https://places.googleapis.com/v1/places"

# Exactly the public venue fields Relay needs. Nothing else is requested.
PLACE_FIELD_MASK = "id,formattedAddress,nationalPhoneNumber"
ROUTE_FIELD_MASK = "routes.duration,routes.distanceMeters"


class ContextUnavailable(Exception):
    """Context could not be read and must be reported as absent, never guessed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _raise_for_status(response: httpx.Response, *, unavailable: str, not_found: str | None = None) -> None:
    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableProviderError(f"{unavailable}: {response.status_code}")
    if response.status_code == 404 and not_found is not None:
        raise ContextUnavailable(not_found)
    if response.status_code >= 400:
        raise ContextUnavailable(unavailable)


async def _request(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    json: Any = None,
    transport: httpx.AsyncBaseTransport | None,
    timeout: float,
    unavailable: str,
    not_found: str | None = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.request(method, url, json=json, headers=headers)
    except httpx.TimeoutException as error:
        raise RetryableProviderError(f"{unavailable}: timeout") from error
    _raise_for_status(response, unavailable=unavailable, not_found=not_found)
    payload = response.json()
    if not isinstance(payload, dict):
        raise ContextUnavailable(unavailable)
    return payload


class GoogleCalendarAdapter:
    """Read-only Calendar access. Only `freebusy.query` is ever called."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token_reader: Callable[[GoogleConnection], str],
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token_reader = refresh_token_reader
        self._transport = transport
        self._timeout = timeout

    async def get_busy(
        self,
        *,
        connection: GoogleConnection,
        window: TimeInterval,
        calendar_id: str = "primary",
    ) -> CalendarWindow:
        access_token = await self._access_token(connection)
        payload = await _request(
            method="POST",
            url=_FREE_BUSY_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "timeMin": window.start_at.isoformat(),
                "timeMax": window.end_at.isoformat(),
                "items": [{"id": calendar_id}],
            },
            transport=self._transport,
            timeout=self._timeout,
            unavailable="CALENDAR_UNAVAILABLE",
        )
        calendars = payload.get("calendars")
        entry = calendars.get(calendar_id) if isinstance(calendars, dict) else None
        periods = entry.get("busy") if isinstance(entry, dict) else None
        busy: list[TimeInterval] = []
        for period in periods or []:
            if not isinstance(period, dict):
                continue
            start, end = period.get("start"), period.get("end")
            if not isinstance(start, str) or not isinstance(end, str):
                continue
            # Only the interval is kept. Event IDs, titles, guests, and
            # descriptions are never requested and never stored.
            busy.append(TimeInterval(start_at=_parse(start), end_at=_parse(end)))
        return CalendarWindow(window=window, busy=busy)

    async def _access_token(self, connection: GoogleConnection) -> str:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    _GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": self._refresh_token_reader(connection),
                        "grant_type": "refresh_token",
                    },
                )
        except httpx.TimeoutException as error:
            raise RetryableProviderError("CALENDAR_UNAVAILABLE: token timeout") from error
        _raise_for_status(response, unavailable="CALENDAR_UNAVAILABLE")
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise ContextUnavailable("CALENDAR_UNAVAILABLE")
        return token


class GoogleRoutesAdapter:
    """Read-only Compute Routes. Requests one driving route and two fields."""

    def __init__(
        self,
        *,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    async def compute_route(
        self, *, origin: PlaceRef, destination: PlaceRef, departure_time: datetime
    ) -> RouteEstimate:
        payload = await _request(
            method="POST",
            url=_COMPUTE_ROUTES_URL,
            headers={"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": ROUTE_FIELD_MASK},
            json={
                "origin": _waypoint(origin),
                "destination": _waypoint(destination),
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_AWARE",
                "departureTime": departure_time.isoformat(),
            },
            transport=self._transport,
            timeout=self._timeout,
            unavailable="ROUTES_UNAVAILABLE",
        )
        routes = payload.get("routes")
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            # No route is a real answer. Never substitute an estimate.
            raise ContextUnavailable("NO_ROUTE_AVAILABLE")
        route = routes[0]
        duration = route.get("duration")
        distance = route.get("distanceMeters")
        if not isinstance(duration, str) or not duration.endswith("s"):
            raise ContextUnavailable("NO_ROUTE_AVAILABLE")
        try:
            duration_seconds = int(float(duration[:-1]))
            distance_meters = int(distance)
        except (TypeError, ValueError) as error:
            raise ContextUnavailable("NO_ROUTE_AVAILABLE") from error
        return RouteEstimate(
            origin=origin,
            destination=destination,
            departure_time=departure_time,
            duration_seconds=duration_seconds,
            distance_meters=distance_meters,
        )


class GooglePlacesAdapter:
    """Read-only Place Details limited to public venue fields."""

    def __init__(
        self,
        *,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    async def get_details(self, *, place_id: str) -> PlaceDetails:
        payload = await _request(
            method="GET",
            url=f"{_PLACES_URL}/{place_id}",
            headers={"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": PLACE_FIELD_MASK},
            transport=self._transport,
            timeout=self._timeout,
            unavailable="PLACES_UNAVAILABLE",
            not_found="PLACE_NOT_FOUND",
        )
        address = payload.get("formattedAddress")
        if not isinstance(address, str) or not address:
            raise ContextUnavailable("PLACE_NOT_FOUND")
        phone = payload.get("nationalPhoneNumber")
        return PlaceDetails(
            place_id=payload.get("id") or place_id,
            address=address,
            # A public venue line. It never becomes a personal pickup contact.
            phone_number=phone if isinstance(phone, str) and phone else None,
        )


def _waypoint(reference: PlaceRef) -> dict[str, str]:
    if reference.place_id:
        return {"placeId": reference.place_id}
    return {"address": reference.address or ""}


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


__all__ = [
    "ContextUnavailable",
    "GoogleCalendarAdapter",
    "GooglePlacesAdapter",
    "GoogleRoutesAdapter",
    "PLACE_FIELD_MASK",
    "ROUTE_FIELD_MASK",
]
