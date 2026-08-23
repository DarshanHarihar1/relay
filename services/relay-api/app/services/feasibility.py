from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from app.contracts import Commitment, Disruption, Edge
from app.domain.impact import ConstraintTrace, FeasibilityStatus, ImpactNode, TraversalDiagnosticCode
from app.services.impact_graph import ReachableSubgraph
from app.services.route_snapshots import RouteSnapshotReader, validated_route_minutes

RISK_SLACK_MINUTES = 15

_ROUTE_EDGE_KINDS = {"requires_travel", "requires_location"}


def is_protected(commitment: Commitment) -> bool:
    return commitment.protected or commitment.flexibility == "NEVER_MOVE"


def effective_schedule(
    commitment: Commitment,
    disruption: Disruption,
    schedule_overrides: Mapping[str, tuple[datetime, datetime]],
) -> tuple[datetime, datetime]:
    if commitment.id in schedule_overrides:
        return schedule_overrides[commitment.id]
    duration = commitment.ends_at - commitment.starts_at
    if disruption.commitment_id == commitment.id and disruption.new_time is not None:
        effective_start = disruption.new_time
        return effective_start, effective_start + duration
    return commitment.starts_at, commitment.ends_at


def constrained_arrival(release_at: datetime, edge: Edge, route_minutes: int) -> datetime:
    return release_at + timedelta(minutes=edge.min_gap_minutes + route_minutes)


class FeasibilityEngine:
    def __init__(self, routes: RouteSnapshotReader, now: datetime) -> None:
        self._routes = routes
        self._now = now

    async def assess(
        self,
        disruption: Disruption,
        commitments: Mapping[str, Commitment],
        subgraph: ReachableSubgraph,
        schedule_overrides: Mapping[str, tuple[datetime, datetime]] = {},  # noqa: B006 - never mutated
    ) -> tuple[tuple[ImpactNode, ...], tuple[TraversalDiagnosticCode, ...]]:
        edges_into: dict[str, list[Edge]] = {}
        for edge in subgraph.edges:
            edges_into.setdefault(edge.to_ref, []).append(edge)

        schedules: dict[str, tuple[datetime, datetime]] = {}
        nodes_by_id: dict[str, ImpactNode] = {}
        diagnostics: set[TraversalDiagnosticCode] = set()

        for commitment_id in subgraph.commitment_ids:
            commitment = commitments[commitment_id]
            effective_start, effective_end = effective_schedule(commitment, disruption, schedule_overrides)
            schedules[commitment_id] = (effective_start, effective_end)

            traces: list[ConstraintTrace] = []
            known_arrivals: list[datetime] = []
            for edge in sorted(edges_into.get(commitment_id, []), key=lambda item: item.id):
                source = commitments.get(edge.from_ref)
                source_schedule = schedules.get(edge.from_ref)
                if source is None or source_schedule is None:
                    # Not yet processed within this traversal order: a genuine
                    # back-edge in a cycle already flagged by the graph walker.
                    continue
                _, source_effective_end = source_schedule
                release_at = source_effective_end + timedelta(minutes=source.required_buffer_minutes)

                route_minutes: int | None = 0
                if edge.kind in _ROUTE_EDGE_KINDS:
                    origin = source.location_place_id
                    destination = commitment.location_place_id
                    snapshot = (
                        await self._routes.get_snapshot(origin, destination, release_at)
                        if origin and destination
                        else None
                    )
                    route_minutes = validated_route_minutes(snapshot, origin, destination, release_at, self._now)

                if route_minutes is None:
                    diagnostics.add(TraversalDiagnosticCode.ROUTE_UNKNOWN)
                    traces.append(
                        ConstraintTrace(
                            edge_id=edge.id,
                            source_id=edge.from_ref,
                            target_id=commitment_id,
                            release_at=release_at,
                            route_minutes=None,
                            constrained_arrival_at=None,
                            status=FeasibilityStatus.AT_RISK,
                            reason="route snapshot unavailable",
                        )
                    )
                    continue

                arrival = constrained_arrival(release_at, edge, route_minutes)
                known_arrivals.append(arrival)
                traces.append(
                    ConstraintTrace(
                        edge_id=edge.id,
                        source_id=edge.from_ref,
                        target_id=commitment_id,
                        release_at=release_at,
                        route_minutes=route_minutes,
                        constrained_arrival_at=arrival,
                        status=FeasibilityStatus.FEASIBLE,
                        reason="constraint satisfied",
                    )
                )

            candidates = list(known_arrivals)
            if commitment.earliest_start is not None:
                candidates.append(commitment.earliest_start)
            earliest_feasible_start = max(candidates) if candidates else None
            route_unknown_here = any(trace.constrained_arrival_at is None for trace in traces)

            status = FeasibilityStatus.FEASIBLE
            slack_minutes: int | None = None
            if commitment.latest_start is not None and earliest_feasible_start is not None:
                slack_minutes = int((commitment.latest_start - earliest_feasible_start).total_seconds() // 60)
                if earliest_feasible_start > commitment.latest_start:
                    status = FeasibilityStatus.VIOLATED
                elif slack_minutes < RISK_SLACK_MINUTES:
                    status = FeasibilityStatus.AT_RISK
            if status is not FeasibilityStatus.VIOLATED and route_unknown_here:
                status = FeasibilityStatus.AT_RISK

            nodes_by_id[commitment_id] = ImpactNode(
                commitment_id=commitment_id,
                effective_start=effective_start,
                effective_end=effective_end,
                earliest_feasible_start=earliest_feasible_start,
                latest_start=commitment.latest_start if commitment.latest_start is not None else effective_end,
                slack_minutes=slack_minutes,
                status=status,
                constraint_traces=tuple(sorted(traces, key=lambda item: item.edge_id)),
            )

        nodes = tuple(nodes_by_id[commitment_id] for commitment_id in sorted(nodes_by_id))
        return nodes, tuple(sorted(diagnostics, key=lambda code: code.value))
