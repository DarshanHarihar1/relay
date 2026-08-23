from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.domain.context import PlaceRef, TimeInterval
from app.domain.ingestion import GoogleConnection
from app.security import FernetFieldCipher


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
CIPHER = FernetFieldCipher(FernetFieldCipher.generate_key())


def _connection() -> GoogleConnection:
    return GoogleConnection(
        user_id="u1",
        granted_scopes=frozenset({"https://www.googleapis.com/auth/calendar.readonly"}),
        gmail_label_id="Label_123",
        encrypted_refresh_token=CIPHER.encrypt("refresh-token"),
        connected_at=NOW,
    )


def _calendar(handler):
    from app.adapters.google_context import GoogleCalendarAdapter

    return GoogleCalendarAdapter(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token_reader=lambda connection: CIPHER.decrypt(connection.encrypted_refresh_token),
        transport=httpx.MockTransport(handler),
    )


def _routes(handler):
    from app.adapters.google_context import GoogleRoutesAdapter

    return GoogleRoutesAdapter(api_key="maps-key", transport=httpx.MockTransport(handler))


def _places(handler):
    from app.adapters.google_context import GooglePlacesAdapter

    return GooglePlacesAdapter(api_key="maps-key", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_calendar_reads_free_busy_only_and_keeps_no_event_details() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "access-token"})
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "calendars": {
                    "primary": {
                        "busy": [
                            {
                                "start": "2026-08-23T09:00:00Z",
                                "end": "2026-08-23T10:00:00Z",
                            }
                        ]
                    }
                }
            },
        )

    window = TimeInterval(start_at=NOW, end_at=NOW + timedelta(hours=8))

    result = await _calendar(handler).get_busy(connection=_connection(), window=window)

    assert captured["method"] == "POST"
    assert captured["path"] == "/calendar/v3/freeBusy"
    assert captured["body"]["items"] == [{"id": "primary"}]
    assert result.window == window
    assert len(result.busy) == 1
    assert result.busy[0].start_at == datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    # Only intervals are mapped. No event ID, title, guest, or description exists.
    assert set(result.busy[0].model_dump()) == {"start_at", "end_at"}


@pytest.mark.asyncio
async def test_routes_requests_only_duration_and_distance_for_one_driving_route() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"routes": [{"duration": "2400s", "distanceMeters": 31000}, {"duration": "9999s"}]},
        )

    estimate = await _routes(handler).compute_route(
        origin=PlaceRef(place_id="ChIJorigin"),
        destination=PlaceRef(address="Terminal 1"),
        departure_time=NOW,
    )

    assert captured["headers"]["x-goog-fieldmask"] == "routes.duration,routes.distanceMeters"
    assert captured["headers"]["x-goog-api-key"] == "maps-key"
    assert captured["body"]["travelMode"] == "DRIVE"
    assert captured["body"]["routingPreference"] == "TRAFFIC_AWARE"
    assert captured["body"]["departureTime"] == "2026-08-23T08:00:00+00:00"
    assert estimate.duration_seconds == 2400
    assert estimate.distance_meters == 31000


@pytest.mark.asyncio
async def test_no_route_is_an_explicit_absence_not_a_fabricated_estimate() -> None:
    from app.adapters.google_context import ContextUnavailable

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"routes": []})

    with pytest.raises(ContextUnavailable) as error:
        await _routes(handler).compute_route(
            origin=PlaceRef(place_id="ChIJorigin"),
            destination=PlaceRef(address="Terminal 1"),
            departure_time=NOW,
        )

    assert error.value.reason == "NO_ROUTE_AVAILABLE"


@pytest.mark.asyncio
async def test_places_requests_only_public_fields() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["headers"] = request.headers
        return httpx.Response(
            200,
            json={
                "id": "ChIJ123",
                "formattedAddress": "1 Airport Road, Bengaluru",
                "nationalPhoneNumber": "080 1234 5678",
            },
        )

    details = await _places(handler).get_details(place_id="ChIJ123")

    assert captured["method"] == "GET"
    assert captured["path"] == "/v1/places/ChIJ123"
    assert captured["headers"]["x-goog-fieldmask"] == "id,formattedAddress,nationalPhoneNumber"
    assert details.place_id == "ChIJ123"
    assert details.address == "1 Airport Road, Bengaluru"
    assert details.phone_number == "080 1234 5678"
    assert details.display_name is None


@pytest.mark.asyncio
async def test_a_missing_place_is_non_retryable() -> None:
    from app.adapters.errors import RetryableProviderError
    from app.adapters.google_context import ContextUnavailable

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(ContextUnavailable) as error:
        await _places(handler).get_details(place_id="ChIJ404")

    assert error.value.reason == "PLACE_NOT_FOUND"
    assert not isinstance(error.value, RetryableProviderError)


@pytest.mark.asyncio
async def test_provider_rate_limiting_is_retryable() -> None:
    from app.adapters.errors import RetryableProviderError

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "slow down"})

    with pytest.raises(RetryableProviderError):
        await _places(handler).get_details(place_id="ChIJ429")


@pytest.mark.asyncio
async def test_a_forbidden_provider_response_is_a_non_retryable_reason() -> None:
    from app.adapters.google_context import ContextUnavailable

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(ContextUnavailable) as error:
        await _routes(handler).compute_route(
            origin=PlaceRef(place_id="a"),
            destination=PlaceRef(place_id="b"),
            departure_time=NOW,
        )

    assert error.value.reason == "ROUTES_UNAVAILABLE"


@pytest.mark.asyncio
async def test_no_context_adapter_ever_issues_a_write() -> None:
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "access-token"})
        if "freeBusy" in request.url.path:
            return httpx.Response(200, json={"calendars": {"primary": {"busy": []}}})
        if "places" in request.url.path:
            return httpx.Response(200, json={"id": "ChIJ123", "formattedAddress": "somewhere"})
        return httpx.Response(200, json={"routes": [{"duration": "60s", "distanceMeters": 100}]})

    await _calendar(handler).get_busy(
        connection=_connection(),
        window=TimeInterval(start_at=NOW, end_at=NOW + timedelta(hours=1)),
    )
    await _places(handler).get_details(place_id="ChIJ123")
    await _routes(handler).compute_route(
        origin=PlaceRef(place_id="a"), destination=PlaceRef(place_id="b"), departure_time=NOW
    )

    # freeBusy and computeRoutes are POST queries. Nothing uses PUT, PATCH, or DELETE.
    assert set(methods) <= {"GET", "POST"}
    assert not any("/events" in path for path in [])
