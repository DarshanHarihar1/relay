from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import Commitment, Disruption, Edge
from app.domain.impact import FeasibilityStatus, RouteSnapshot, TraversalDiagnosticCode
from app.services.feasibility import FeasibilityEngine, is_protected
from app.services.impact_graph import ReachableSubgraph

USER_ID = "u1"
NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def commitment(
    commitment_id: str,
    *,
    start: str,
    end: str,
    buffer: int = 0,
    place: str | None = None,
    earliest: str | None = None,
    latest: str | None = None,
    flexibility: str | None = None,
    protected: bool = False,
) -> Commitment:
    return Commitment(
        id=commitment_id,
        user_id=USER_ID,
        source_event_key=f"seed:{commitment_id}",
        summary=commitment_id,
        starts_at=parse(start),
        ends_at=parse(end),
        required_buffer_minutes=buffer,
        location_place_id=place,
        earliest_start=parse(earliest) if earliest else None,
        latest_start=parse(latest) if latest else None,
        flexibility=flexibility,
        protected=protected,
    )


def delay(source: Commitment) -> Disruption:
    return Disruption(
        id=f"disruption-{source.id}",
        user_id=USER_ID,
        source_event_key=f"seed:disruption-{source.id}",
        kind="delay",
        occurred_at=NOW,
        commitment_id=source.id,
        new_time=source.starts_at,
    )


def graph(from_id: str, to_id: str, *, kind: str) -> ReachableSubgraph:
    return ReachableSubgraph(
        commitment_ids=(from_id, to_id),
        edges=(Edge(id="e1", from_ref=from_id, to_ref=to_id, relation=kind, kind=kind),),
        diagnostics=(),
    )


def node(nodes, commitment_id: str):
    return next(n for n in nodes if n.commitment_id == commitment_id)


class _FakeRouteReader:
    def __init__(self, *, origin: str | None = None, destination: str | None = None, minutes: int = 0) -> None:
        self._origin = origin
        self._destination = destination
        self._minutes = minutes

    async def get_snapshot(self, origin_place_id, destination_place_id, departure_at):
        if origin_place_id != self._origin or destination_place_id != self._destination:
            return None
        return RouteSnapshot(
            origin_place_id=origin_place_id,
            destination_place_id=destination_place_id,
            departure_at=departure_at,
            duration_minutes=self._minutes,
            fetched_at=NOW,
            expires_at=departure_at + timedelta(days=1),
        )


def engine_with_route(origin: str, destination: str, minutes: int) -> FeasibilityEngine:
    return FeasibilityEngine(_FakeRouteReader(origin=origin, destination=destination, minutes=minutes), NOW)


def engine_without_routes() -> FeasibilityEngine:
    return FeasibilityEngine(_FakeRouteReader(), NOW)


FLIGHT = commitment("flight", start="2026-08-22T20:20:00Z", end="2026-08-22T22:05:00Z", buffer=30, place="blr")
DINNER = commitment(
    "dinner",
    start="2026-08-22T22:00:00Z",
    end="2026-08-22T23:00:00Z",
    earliest="2026-08-22T22:00:00Z",
    latest="2026-08-22T22:00:00Z",
    place="toscano",
)
COMMITMENTS = {"flight": FLIGHT, "dinner": DINNER}


@pytest.mark.asyncio
async def test_flight_delay_makes_2200_dinner_violated_from_buffer_and_route() -> None:
    nodes, diagnostics = await engine_with_route("blr", "toscano", 55).assess(
        delay(FLIGHT), COMMITMENTS, graph("flight", "dinner", kind="requires_travel")
    )
    dinner_node = node(nodes, "dinner")
    assert dinner_node.earliest_feasible_start.isoformat() == "2026-08-22T23:30:00+00:00"
    assert dinner_node.status is FeasibilityStatus.VIOLATED
    assert diagnostics == ()


@pytest.mark.asyncio
async def test_missing_required_route_is_at_risk_not_feasible() -> None:
    nodes, diagnostics = await engine_without_routes().assess(
        delay(FLIGHT), COMMITMENTS, graph("flight", "dinner", kind="requires_travel")
    )
    assert node(nodes, "dinner").status is FeasibilityStatus.AT_RISK
    assert TraversalDiagnosticCode.ROUTE_UNKNOWN in diagnostics


def test_protected_covers_never_move_and_explicit_flag() -> None:
    assert is_protected(
        commitment("interview", start="2026-08-22T10:00:00Z", end="2026-08-22T11:00:00Z", flexibility="NEVER_MOVE")
    )
    assert is_protected(
        commitment("medical", start="2026-08-22T10:00:00Z", end="2026-08-22T11:00:00Z", protected=True)
    )
    assert not is_protected(
        commitment("dinner", start="2026-08-22T10:00:00Z", end="2026-08-22T11:00:00Z")
    )
