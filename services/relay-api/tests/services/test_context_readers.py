from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import Commitment
from app.domain.context import (
    CalendarWindow,
    PlaceDetails,
    PlaceRef,
    RouteEstimate,
    TimeInterval,
)


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
WINDOW = TimeInterval(start_at=NOW, end_at=NOW + timedelta(hours=12))
AIRPORT = PlaceRef(place_id="ChIJairport")
VENUE = PlaceRef(place_id="ChIJvenue")
DINNER = Commitment(
    id="dinner_1",
    user_id="u1",
    source_event_key="seed:dinner_1",
    summary="Dinner at Example Restaurant",
    starts_at=NOW + timedelta(hours=6),
    ends_at=NOW + timedelta(hours=8),
)


class FakeCalendar:
    def __init__(self) -> None:
        self.calls = 0
        self.error: Exception | None = None

    async def get_busy(self, *, connection, window, calendar_id="primary"):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return CalendarWindow(window=window, busy=[])


class FakeRoutes:
    def __init__(self) -> None:
        self.calls = 0
        self.error: Exception | None = None

    async def compute_route(self, *, origin, destination, departure_time):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return RouteEstimate(
            origin=origin,
            destination=destination,
            departure_time=departure_time,
            duration_seconds=2400,
            distance_meters=31000,
        )


class FakePlaces:
    def __init__(self) -> None:
        self.calls = 0
        self.error: Exception | None = None

    async def get_details(self, *, place_id):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return PlaceDetails(place_id=place_id, address="1 Example Road")


def _reader(calendar, routes, places, now=lambda: NOW):
    from app.services.context_readers import CommitmentContextReader

    return CommitmentContextReader(
        calendar=calendar,
        routes=routes,
        places=places,
        connections=_Connections(),
        now=now,
    )


class _Connections:
    async def get_connection(self, user_id):
        from app.domain.ingestion import GoogleConnection

        return GoogleConnection(
            user_id=user_id,
            granted_scopes=frozenset({"https://www.googleapis.com/auth/calendar.readonly"}),
            encrypted_refresh_token="encrypted",
            connected_at=NOW,
        )


@pytest.mark.asyncio
async def test_route_timeout_does_not_block_calendar() -> None:
    from app.adapters.errors import RetryableProviderError

    routes = FakeRoutes()
    routes.error = RetryableProviderError("Routes timed out")
    calendar = FakeCalendar()

    context = await _reader(calendar, routes, FakePlaces()).read_commitment_context(
        user_id="u1", commitment=DINNER, origin=AIRPORT, horizon=WINDOW, destination=VENUE
    )

    assert context.calendar is not None
    assert context.route_to_commitment is None
    assert context.unavailable_reasons == ["ROUTES_TEMPORARILY_UNAVAILABLE"]


@pytest.mark.asyncio
async def test_every_provider_failure_degrades_independently() -> None:
    from app.adapters.errors import RetryableProviderError
    from app.adapters.google_context import ContextUnavailable

    calendar, routes, places = FakeCalendar(), FakeRoutes(), FakePlaces()
    calendar.error = RetryableProviderError("Calendar unavailable")
    routes.error = ContextUnavailable("NO_ROUTE_AVAILABLE")
    places.error = ContextUnavailable("PLACE_NOT_FOUND")

    context = await _reader(calendar, routes, places).read_commitment_context(
        user_id="u1", commitment=DINNER, origin=AIRPORT, horizon=WINDOW, destination=VENUE
    )

    assert context.commitment_id == "dinner_1"
    assert context.calendar is None
    assert context.route_to_commitment is None
    assert context.place is None
    assert context.unavailable_reasons == [
        "CALENDAR_TEMPORARILY_UNAVAILABLE",
        "NO_ROUTE_AVAILABLE",
        "PLACE_NOT_FOUND",
    ]


@pytest.mark.asyncio
async def test_a_missing_origin_skips_routes_without_calling_the_provider() -> None:
    routes = FakeRoutes()

    context = await _reader(FakeCalendar(), routes, FakePlaces()).read_commitment_context(
        user_id="u1", commitment=DINNER, origin=None, horizon=WINDOW, destination=VENUE
    )

    assert routes.calls == 0
    assert context.route_to_commitment is None
    assert context.unavailable_reasons == ["ROUTE_ORIGIN_UNKNOWN"]


@pytest.mark.asyncio
async def test_route_and_place_reads_are_cached_for_fifteen_minutes() -> None:
    clock = {"now": NOW}
    routes, places = FakeRoutes(), FakePlaces()
    reader = _reader(FakeCalendar(), routes, places, now=lambda: clock["now"])
    async def read():
        return await reader.read_commitment_context(
            user_id="u1", commitment=DINNER, origin=AIRPORT, horizon=WINDOW, destination=VENUE
        )

    await read()
    await read()
    assert (routes.calls, places.calls) == (1, 1)

    clock["now"] = NOW + timedelta(minutes=16)
    await read()
    assert (routes.calls, places.calls) == (2, 2)


@pytest.mark.asyncio
async def test_a_commitment_without_a_place_id_reports_the_gap_instead_of_guessing() -> None:
    places = FakePlaces()

    context = await _reader(FakeCalendar(), FakeRoutes(), places).read_commitment_context(
        user_id="u1", commitment=DINNER, origin=AIRPORT, horizon=WINDOW
    )

    assert places.calls == 0
    assert context.place is None
    assert context.unavailable_reasons == ["PLACE_ID_UNKNOWN"]


@pytest.mark.asyncio
async def test_free_busy_is_never_cached_across_reads() -> None:
    calendar = FakeCalendar()
    reader = _reader(calendar, FakeRoutes(), FakePlaces())

    for _ in range(2):
        await reader.read_commitment_context(
            user_id="u1", commitment=DINNER, origin=AIRPORT, horizon=WINDOW
        )

    assert calendar.calls == 2
