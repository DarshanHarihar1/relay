from datetime import datetime, timedelta, timezone

from app.domain.impact import RouteSnapshot
from app.services.route_snapshots import validated_route_minutes

UTC_NOW = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)
DEPARTURE = datetime(2026, 8, 22, 21, 0, tzinfo=timezone.utc)


def make_snapshot(
    *,
    origin: str = "airport",
    destination: str = "dinner",
    duration_minutes: int = 30,
    departure_at: datetime = DEPARTURE,
    expires_at: datetime = UTC_NOW + timedelta(hours=1),
) -> RouteSnapshot:
    return RouteSnapshot(
        origin_place_id=origin,
        destination_place_id=destination,
        departure_at=departure_at,
        duration_minutes=duration_minutes,
        fetched_at=UTC_NOW - timedelta(minutes=5),
        expires_at=expires_at,
    )


def test_expired_or_mismatched_snapshot_is_not_usable() -> None:
    snapshot = make_snapshot(origin="airport", destination="dinner", expires_at=UTC_NOW - timedelta(seconds=1))
    assert validated_route_minutes(snapshot, "airport", "dinner", DEPARTURE, UTC_NOW) is None
    assert validated_route_minutes(make_snapshot(origin="hotel", destination="dinner"), "airport", "dinner", DEPARTURE, UTC_NOW) is None


def test_valid_snapshot_returns_its_nonnegative_duration() -> None:
    assert validated_route_minutes(make_snapshot(duration_minutes=55), "airport", "dinner", DEPARTURE, UTC_NOW) == 55


def test_missing_snapshot_or_place_ids_are_not_usable() -> None:
    assert validated_route_minutes(None, "airport", "dinner", DEPARTURE, UTC_NOW) is None
    assert validated_route_minutes(make_snapshot(), None, "dinner", DEPARTURE, UTC_NOW) is None
    assert validated_route_minutes(make_snapshot(), "airport", None, DEPARTURE, UTC_NOW) is None


def test_snapshot_bound_to_a_different_departure_time_is_not_usable() -> None:
    snapshot = make_snapshot(departure_at=DEPARTURE - timedelta(minutes=1))
    assert validated_route_minutes(snapshot, "airport", "dinner", DEPARTURE, UTC_NOW) is None
