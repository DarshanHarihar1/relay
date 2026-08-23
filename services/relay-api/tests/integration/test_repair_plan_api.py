from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.auth import CurrentUser, require_current_user
from app.contracts import Disruption
from app.domain.impact import ActionKind, CandidateChange, CandidateKind, RepairCandidate, RepairPlan
from app.main import app
from app.routes.repair_plans import get_disruption_repository, get_impact_repair_planner

USER_ID = "u1"
NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)
AUTH_HEADERS = {"Authorization": "Bearer test-token"}
REQUEST_JSON = {
    "disruption_id": "dis_1",
    "options": {"approval_expires_at": "2026-08-22T20:00:00Z"},
}


def _disruption() -> Disruption:
    return Disruption(
        id="dis_1",
        user_id=USER_ID,
        source_event_key="seed:dis_1",
        kind="delay",
        occurred_at=NOW,
        commitment_id="flight",
    )


def _plan() -> RepairPlan:
    candidate = RepairCandidate(
        id="candidate_1", kind="KEEP_AS_IS", changes=(), invalid_reasons=(), explanation="Keep dinner as scheduled."
    )
    return RepairPlan(
        id="plan_abc123",
        version=1,
        assessment_id="assessment_1",
        selected_candidate_id=candidate.id,
        candidates=(candidate,),
        approval_id=None,
        input_fingerprint="fp1",
    )


class _FakeDisruptionRepository:
    def __init__(self, disruption: Disruption) -> None:
        self._disruption = disruption

    async def create_disruption_if_absent(self, disruption, *, assessment=None):
        raise NotImplementedError

    async def get_disruption(self, *, user_id: str, disruption_id: str):
        if user_id == USER_ID and disruption_id == self._disruption.id:
            return self._disruption
        return None


class _FakePlanner:
    def __init__(self, plan: RepairPlan) -> None:
        self._plan = plan
        self.calls: list[tuple] = []

    async def create_plan(self, disruption, options, user_policy):
        self.calls.append((disruption, options, user_policy))
        return self._plan


def test_create_plan_returns_persisted_plan_without_dispatching() -> None:
    planner = _FakePlanner(_plan())
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(uid=USER_ID, email=None)
    app.dependency_overrides[get_disruption_repository] = lambda: _FakeDisruptionRepository(_disruption())
    app.dependency_overrides[get_impact_repair_planner] = lambda: planner
    try:
        response = TestClient(app).post(
            "/v1/disruptions/dis_1/repair-plans", json=REQUEST_JSON, headers=AUTH_HEADERS
        )
        assert response.status_code == 201
        body = response.json()
        assert body["repair_plan_id"].startswith("plan_")
        assert body["selected_candidate_id"] == "candidate_1"
        assert body["candidate_count"] == 1
        assert len(planner.calls) == 1
    finally:
        app.dependency_overrides.clear()


def test_request_path_and_body_disruption_ids_must_match() -> None:
    planner = _FakePlanner(_plan())
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(uid=USER_ID, email=None)
    app.dependency_overrides[get_disruption_repository] = lambda: _FakeDisruptionRepository(_disruption())
    app.dependency_overrides[get_impact_repair_planner] = lambda: planner
    try:
        response = TestClient(app).post(
            "/v1/disruptions/dis_1/repair-plans",
            json={**REQUEST_JSON, "disruption_id": "dis_2"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422
        assert planner.calls == []
    finally:
        app.dependency_overrides.clear()


def _handoff_plan() -> RepairPlan:
    change = CandidateChange(
        commitment_id="dinner",
        kind=CandidateKind.REPLACE_TRANSPORT,
        action_kinds=(ActionKind.OPEN_UBER_HANDOFF,),
        target_ref="uber_pickup_blr",
    )
    candidate = RepairCandidate(
        id="candidate_2",
        kind="REPLACE_TRANSPORT",
        changes=(change,),
        invalid_reasons=(),
        explanation="Arrange replacement transport for dinner.",
    )
    return RepairPlan(
        id="plan_def456",
        version=1,
        assessment_id="assessment_2",
        selected_candidate_id=candidate.id,
        candidates=(candidate,),
        approval_id="approval_1",
        input_fingerprint="fp2",
    )


def test_handoff_action_is_not_presented_as_booked_ride() -> None:
    planner = _FakePlanner(_handoff_plan())
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(uid=USER_ID, email=None)
    app.dependency_overrides[get_disruption_repository] = lambda: _FakeDisruptionRepository(_disruption())
    app.dependency_overrides[get_impact_repair_planner] = lambda: planner
    try:
        response = TestClient(app).post(
            "/v1/disruptions/dis_1/repair-plans", json=REQUEST_JSON, headers=AUTH_HEADERS
        )
        assert response.status_code == 201
        selected = next(c for c in planner._plan.candidates if c.id == planner._plan.selected_candidate_id)
        assert ActionKind.OPEN_UBER_HANDOFF in selected.changes[0].action_kinds
        assert "RIDE_BOOKED" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_unknown_disruption_returns_404() -> None:
    planner = _FakePlanner(_plan())
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(uid=USER_ID, email=None)
    app.dependency_overrides[get_disruption_repository] = lambda: _FakeDisruptionRepository(_disruption())
    app.dependency_overrides[get_impact_repair_planner] = lambda: planner
    try:
        response = TestClient(app).post(
            "/v1/disruptions/dis_missing/repair-plans",
            json={**REQUEST_JSON, "disruption_id": "dis_missing"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 404
        assert planner.calls == []
    finally:
        app.dependency_overrides.clear()
