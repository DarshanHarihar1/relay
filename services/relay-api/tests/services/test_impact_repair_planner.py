from datetime import datetime, timezone

import pytest

from app.contracts import Commitment, Disruption, Edge
from app.domain.impact import PlanningOptions, make_assessment_id, make_repair_plan_id
from app.services.feasibility import FeasibilityEngine
from app.services.impact_graph import DownstreamGraphWalker
from app.services.impact_repair_planner import ImpactRepairPlanner
from app.services.repair_candidates import CandidateFactory
from app.services.repair_policy import PolicyEngine, UserActionPolicy

USER_ID = "u1"
NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def flight() -> Commitment:
    return Commitment(
        id="flight",
        user_id=USER_ID,
        source_event_key="seed:flight",
        summary="Flight AI202",
        starts_at=parse("2026-08-22T20:20:00Z"),
        ends_at=parse("2026-08-22T22:05:00Z"),
        required_buffer_minutes=30,
        # Pinned: the default `datetime.now()` factory would otherwise leak
        # wall-clock time into the planning fingerprint (see canonical_json),
        # making candidate IDs and tie-break selection non-deterministic
        # across test runs. A real persisted commitment has a stable value.
        created_at=NOW,
        updated_at=NOW,
    )


def dinner(*, latest: str) -> Commitment:
    return Commitment(
        id="dinner",
        user_id=USER_ID,
        source_event_key="seed:dinner",
        summary="Dinner at Toscano",
        starts_at=parse("2026-08-22T22:00:00Z"),
        ends_at=parse("2026-08-22T23:00:00Z"),
        earliest_start=parse("2026-08-22T21:30:00Z"),
        latest_start=parse(latest),
        created_at=NOW,
        updated_at=NOW,
    )


def interview() -> Commitment:
    return Commitment(
        id="interview",
        user_id=USER_ID,
        source_event_key="seed:interview",
        summary="Interview",
        starts_at=parse("2026-08-23T09:00:00Z"),
        ends_at=parse("2026-08-23T09:30:00Z"),
        flexibility="NEVER_MOVE",
        created_at=NOW,
        updated_at=NOW,
    )


def make_disruption() -> Disruption:
    return Disruption(
        id="disruption_1",
        user_id=USER_ID,
        source_event_key="seed:disruption_1",
        kind="delay",
        occurred_at=NOW,
        commitment_id="flight",
        new_time=parse("2026-08-22T20:20:00Z"),
        correlation_id="corr-1",
        created_at=NOW,
        updated_at=NOW,
    )


DELAY = make_disruption()
EDGES = [Edge(id="e1", from_ref="flight", to_ref="dinner", relation="depends_on", kind="depends_on")]
EDGES_WITH_PROTECTED_INTERVIEW = EDGES + [
    Edge(id="e2", from_ref="flight", to_ref="interview", relation="depends_on", kind="depends_on")
]
OPTIONS = PlanningOptions(approval_expires_at=NOW)
POLICY = UserActionPolicy()


class _NoRouteReader:
    async def get_snapshot(self, origin_place_id, destination_place_id, departure_at):
        return None


class FakeRelayRepository:
    def __init__(self, commitments: dict[str, Commitment], edges: list[Edge] | None = None) -> None:
        self._commitments = commitments
        self._edges = edges if edges is not None else EDGES
        self._plans: dict[str, object] = {}
        self.save_count = 0

    async def get_commitment(self, *, user_id: str, commitment_id: str):
        return self._commitments.get(commitment_id)

    async def get_commitments(self, *, user_id: str, commitment_ids):
        return [self._commitments[cid] for cid in commitment_ids if cid in self._commitments]

    async def list_outgoing_edges(self, *, user_id: str, from_id: str):
        return [edge for edge in self._edges if edge.from_ref == from_id]

    async def get_repair_plan_by_fingerprint(self, *, user_id: str, disruption_id: str, input_fingerprint: str):
        plan_id = make_repair_plan_id(make_assessment_id(disruption_id, input_fingerprint), 1)
        return self._plans.get(plan_id)

    async def save_planning_result(self, *, user_id, assessment, plan, action_records, approval):
        self.save_count += 1
        if plan.id in self._plans:
            return self._plans[plan.id]
        self._plans[plan.id] = plan
        return plan


def make_planner(repository: FakeRelayRepository) -> ImpactRepairPlanner:
    walker = DownstreamGraphWalker(repository, USER_ID)
    feasibility = FeasibilityEngine(_NoRouteReader(), NOW)
    return ImpactRepairPlanner(repository, USER_ID, walker, feasibility, CandidateFactory(), PolicyEngine())


@pytest.mark.asyncio
async def test_same_disruption_graph_options_and_policy_returns_existing_plan() -> None:
    # dinner's latest_start leaves 5 minutes of slack against the delayed
    # arrival: AT_RISK, not VIOLATED, so KEEP_AS_IS is a feasible repair.
    repository = FakeRelayRepository({"flight": flight(), "dinner": dinner(latest="2026-08-22T22:40:00Z")})
    planner = make_planner(repository)
    first = await planner.create_plan(DELAY, OPTIONS, POLICY)
    second = await planner.create_plan(DELAY, OPTIONS, POLICY)
    assert second.id == first.id
    assert repository.save_count == 1


@pytest.mark.asyncio
async def test_blocked_or_empty_candidate_set_persists_an_explainable_plan_without_authorized_actions() -> None:
    # dinner's latest_start is before the delayed arrival can ever reach it:
    # no candidate, including KEEP_AS_IS, can ever be feasible.
    repository = FakeRelayRepository({"flight": flight(), "dinner": dinner(latest="2026-08-22T22:00:00Z")})
    planner = make_planner(repository)
    plan = await planner.create_plan(DELAY, OPTIONS, POLICY)
    assert plan.selected_candidate_id is None
    assert plan.approval_id is None


def _acceptance_repository() -> FakeRelayRepository:
    return FakeRelayRepository(
        {"flight": flight(), "dinner": dinner(latest="2026-08-22T22:40:00Z"), "interview": interview()},
        edges=EDGES_WITH_PROTECTED_INTERVIEW,
    )


def _selected_candidate(plan):
    return next(candidate for candidate in plan.candidates if candidate.id == plan.selected_candidate_id)


@pytest.mark.asyncio
async def test_flight_delay_repairs_only_dinner_not_protected_interview() -> None:
    # dinner is AT_RISK (5 minutes of slack), not VIOLATED, so the
    # least-disruptive valid repair is KEEP_AS_IS: no schedule change, no
    # action, and therefore no manufactured approval. interview is
    # NEVER_MOVE-protected and stays FEASIBLE regardless, so it never
    # generates a candidate at all.
    options = PlanningOptions(late_arrival_target_refs={"dinner": "place_toscano_blr"}, approval_expires_at=NOW)
    plan = await make_planner(_acceptance_repository()).create_plan(DELAY, options, POLICY)

    selected = _selected_candidate(plan)
    changed_ids = {change.commitment_id for candidate in plan.candidates for change in candidate.changes}
    assert changed_ids == {"dinner"}
    assert selected.kind == "KEEP_AS_IS"
    assert plan.approval_id is None


@pytest.mark.asyncio
async def test_same_fixture_serializes_to_the_same_plan_snapshot() -> None:
    options = PlanningOptions(late_arrival_target_refs={"dinner": "place_toscano_blr"}, approval_expires_at=NOW)

    async def build():
        plan = await make_planner(_acceptance_repository()).create_plan(DELAY, options, POLICY)
        return plan.model_dump(mode="json")

    first = await build()
    second = await build()
    assert first == second


@pytest.mark.asyncio
async def test_uber_replacement_transport_is_a_handoff_never_a_booked_ride() -> None:
    from app.domain.impact import ActionKind, TransportOption

    options = PlanningOptions(
        transport_options=(
            TransportOption(
                commitment_id="dinner",
                start=parse("2026-08-22T22:00:00Z"),
                end=parse("2026-08-22T22:30:00Z"),
                target_ref="uber_pickup_blr",
                cost_inr=400,
            ),
        ),
        approval_expires_at=NOW,
    )
    plan = await make_planner(_acceptance_repository()).create_plan(DELAY, options, POLICY)

    transport_candidates = [c for c in plan.candidates if c.kind == "REPLACE_TRANSPORT"]
    assert transport_candidates
    assert ActionKind.OPEN_UBER_HANDOFF in transport_candidates[0].changes[0].action_kinds
    assert "RIDE_BOOKED" not in plan.model_dump_json()
