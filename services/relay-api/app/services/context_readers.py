from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from app.adapters.errors import RetryableProviderError
from app.adapters.google_context import ContextUnavailable
from app.contracts import Commitment
from app.domain.context import (
    CalendarWindow,
    CommitmentContext,
    PlaceDetails,
    PlaceRef,
    RouteEstimate,
    TimeInterval,
)
from app.domain.ingestion import GoogleConnection
from app.ports.google import CalendarPort, PlacesPort, RoutesPort


# Route and place answers go stale quickly but not instantly.
CACHE_TTL = timedelta(minutes=15)


class ConnectionReader(Protocol):
    async def get_connection(self, user_id: str) -> GoogleConnection | None: ...


class _TtlCache:
    """A small time-bounded cache. Free/busy is deliberately never stored here."""

    def __init__(self, ttl: timedelta) -> None:
        self._ttl = ttl
        self._entries: dict[Any, tuple[datetime, Any]] = {}

    def get(self, key: Any, now: datetime) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if now - stored_at >= self._ttl:
            del self._entries[key]
            return None
        return value

    def put(self, key: Any, value: Any, now: datetime) -> None:
        self._entries[key] = (now, value)


class CommitmentContextReader:
    """Reads bounded, read-only context for one commitment.

    Every provider is optional. A provider that fails contributes an explicit
    unavailable reason and never blocks the others or fabricates a value.
    """

    def __init__(
        self,
        *,
        calendar: CalendarPort,
        routes: RoutesPort,
        places: PlacesPort,
        connections: ConnectionReader,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._calendar = calendar
        self._routes = routes
        self._places = places
        self._connections = connections
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._route_cache = _TtlCache(CACHE_TTL)
        self._place_cache = _TtlCache(CACHE_TTL)

    async def read_commitment_context(
        self,
        *,
        user_id: str,
        commitment: Commitment,
        origin: PlaceRef | None,
        horizon: TimeInterval,
        destination: PlaceRef | None = None,
    ) -> CommitmentContext:
        reasons: list[str] = []
        calendar = await self._read_calendar(user_id=user_id, horizon=horizon, reasons=reasons)
        # Phase 1 commitments carry no structured place, so the caller supplies the
        # resolved destination when it has one. Otherwise only the summary is known.
        destination = destination or _destination(commitment)
        route = await self._read_route(
            origin=origin,
            destination=destination,
            departure_time=commitment.starts_at,
            reasons=reasons,
        )
        place = await self._read_place(destination, reasons)
        return CommitmentContext(
            commitment_id=commitment.id,
            calendar=calendar,
            route_to_commitment=route,
            place=place,
            unavailable_reasons=reasons,
        )

    async def _read_calendar(
        self, *, user_id: str, horizon: TimeInterval, reasons: list[str]
    ) -> CalendarWindow | None:
        connection = await self._connections.get_connection(user_id)
        if connection is None:
            reasons.append("CALENDAR_NOT_CONNECTED")
            return None
        try:
            # Never cached here. Free/busy is held only by the Phase 3 job.
            return await self._calendar.get_busy(connection=connection, window=horizon)
        except RetryableProviderError:
            reasons.append("CALENDAR_TEMPORARILY_UNAVAILABLE")
        except ContextUnavailable as error:
            reasons.append(error.reason)
        return None

    async def _read_route(
        self,
        *,
        origin: PlaceRef | None,
        destination: PlaceRef | None,
        departure_time: datetime,
        reasons: list[str],
    ) -> RouteEstimate | None:
        if origin is None:
            reasons.append("ROUTE_ORIGIN_UNKNOWN")
            return None
        if destination is None:
            reasons.append("ROUTE_DESTINATION_UNKNOWN")
            return None
        now = self._now()
        key = (_key(origin), _key(destination), departure_time)
        cached = self._route_cache.get(key, now)
        if cached is not None:
            return cached
        try:
            estimate = await self._routes.compute_route(
                origin=origin, destination=destination, departure_time=departure_time
            )
        except RetryableProviderError:
            reasons.append("ROUTES_TEMPORARILY_UNAVAILABLE")
            return None
        except ContextUnavailable as error:
            reasons.append(error.reason)
            return None
        self._route_cache.put(key, estimate, now)
        return estimate

    async def _read_place(
        self, destination: PlaceRef | None, reasons: list[str]
    ) -> PlaceDetails | None:
        if destination is None:
            return None
        if not destination.place_id:
            reasons.append("PLACE_ID_UNKNOWN")
            return None
        now = self._now()
        cached = self._place_cache.get(destination.place_id, now)
        if cached is not None:
            return cached
        try:
            details = await self._places.get_details(place_id=destination.place_id)
        except RetryableProviderError:
            reasons.append("PLACES_TEMPORARILY_UNAVAILABLE")
            return None
        except ContextUnavailable as error:
            reasons.append(error.reason)
            return None
        self._place_cache.put(destination.place_id, details, now)
        return details


def _destination(commitment: Commitment) -> PlaceRef | None:
    return PlaceRef(address=commitment.summary) if commitment.summary else None


def _key(reference: PlaceRef) -> str:
    return (reference.place_id or reference.address or "").strip().casefold()


__all__ = ["CACHE_TTL", "CommitmentContextReader"]
