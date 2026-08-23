from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.impact import RouteSnapshot


class RouteSnapshotReader(Protocol):
    async def get_snapshot(
        self,
        origin_place_id: str,
        destination_place_id: str,
        departure_at: datetime,
    ) -> RouteSnapshot | None: ...


def validated_route_minutes(
    snapshot: RouteSnapshot | None,
    origin_place_id: str | None,
    destination_place_id: str | None,
    departure_at: datetime,
    now: datetime,
) -> int | None:
    if not origin_place_id or not destination_place_id or snapshot is None:
        return None
    if snapshot.origin_place_id != origin_place_id or snapshot.destination_place_id != destination_place_id:
        return None
    if snapshot.expires_at <= now or snapshot.departure_at != departure_at:
        return None
    return snapshot.duration_minutes
