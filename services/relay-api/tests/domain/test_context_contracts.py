import inspect
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.domain.context import (
    CalendarWindow,
    PickerContact,
    PickerPhone,
    PlaceDetails,
    PlaceRef,
    RouteEstimate,
    TimeInterval,
)
from app.ports.google import CalendarPort, GmailPort, PeoplePort, PlacesPort, RoutesPort


NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


def test_picker_contact_has_no_google_resource_name() -> None:
    assert "resource_name" not in PickerContact.model_fields


def test_picker_contact_exposes_only_picker_safe_fields() -> None:
    contact = PickerContact(
        display_name="Rohan",
        phones=[PickerPhone(label="mobile", number="+919876543210")],
        avatar_url="https://example.test/avatar.png",
    )

    assert contact.model_dump() == {
        "display_name": "Rohan",
        "phones": [{"label": "mobile", "number": "+919876543210"}],
        "avatar_url": "https://example.test/avatar.png",
    }


def test_time_interval_requires_an_increasing_aware_interval() -> None:
    with pytest.raises(ValidationError):
        TimeInterval(start_at=NOW, end_at=NOW)
    with pytest.raises(ValidationError):
        TimeInterval(start_at=datetime(2026, 8, 23), end_at=NOW + timedelta(hours=1))


def test_context_models_preserve_minimal_read_only_data() -> None:
    interval = TimeInterval(start_at=NOW, end_at=NOW + timedelta(minutes=30))
    window = CalendarWindow(window=interval, busy=[interval])
    origin = PlaceRef(place_id="ChIJorigin", address="Origin")
    destination = PlaceRef(place_id="ChIJdestination", address="Destination")
    route = RouteEstimate(
        origin=origin,
        destination=destination,
        departure_time=NOW,
        duration_seconds=600,
        distance_meters=1000,
    )
    details = PlaceDetails(place_id="ChIJdestination", display_name="Restaurant", address="Address")

    assert window.busy == [interval]
    assert route.duration_seconds == 600
    assert details.place_id == destination.place_id


def test_provider_protocols_do_not_depend_on_google_sdk_types() -> None:
    for port in (CalendarPort, RoutesPort, PlacesPort, GmailPort, PeoplePort):
        source = inspect.getsource(port)
        assert "google." not in source.lower()


def test_routes_port_accepts_an_aware_datetime_departure() -> None:
    departure = inspect.signature(RoutesPort.compute_route).parameters["departure_time"]

    assert departure.annotation == "datetime"
