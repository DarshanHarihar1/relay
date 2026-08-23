from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from google.cloud.firestore_v1 import AsyncClient
from pydantic import BaseModel

from app.auth import CurrentUser, require_current_user
from app.config import Settings
from app.contracts import Problem
from app.domain.impact import PlanningOptions
from app.repositories.disruptions import DisruptionRepository, FirestoreDisruptionRepository
from app.repositories.firestore import utc_now
from app.repositories.relay_repository import FirestoreRelayRepository, RelayRepository
from app.services.feasibility import FeasibilityEngine
from app.services.impact_graph import DownstreamGraphWalker
from app.services.impact_repair_planner import ImpactRepairPlanner
from app.services.repair_candidates import CandidateFactory
from app.services.repair_policy import PolicyEngine, UserActionPolicy


router = APIRouter()


class _NullRouteSnapshotReader:
    """No Maps/Routes integration yet (deferred, see infra/gcp/secret-manifest.txt).

    Every route-dependent edge is therefore always ROUTE_UNKNOWN / AT_RISK,
    which is the documented safe default, never a silent zero-travel-time guess.
    """

    async def get_snapshot(self, origin_place_id, destination_place_id, departure_at):
        del origin_place_id, destination_place_id, departure_at
        return None


class CreateRepairPlanRequest(BaseModel):
    disruption_id: str
    options: PlanningOptions


class CreateRepairPlanResponse(BaseModel):
    repair_plan_id: str
    version: int
    selected_candidate_id: str | None
    approval_id: str | None
    candidate_count: int


def get_disruption_repository(request: Request) -> DisruptionRepository:
    repository = getattr(request.app.state, "disruption_repository", None)
    if repository is not None:
        return repository

    project_id = Settings.from_env().google_cloud_project
    if project_id is None:
        raise HTTPException(status_code=503)
    repository = FirestoreDisruptionRepository(AsyncClient(project=project_id))
    request.app.state.disruption_repository = repository
    return repository


def get_relay_repository(request: Request) -> RelayRepository:
    repository = getattr(request.app.state, "relay_repository", None)
    if repository is not None:
        return repository

    project_id = Settings.from_env().google_cloud_project
    if project_id is None:
        raise HTTPException(status_code=503)
    repository = FirestoreRelayRepository(AsyncClient(project=project_id))
    request.app.state.relay_repository = repository
    return repository


def get_impact_repair_planner(
    current_user: CurrentUser = Depends(require_current_user),
    repository: RelayRepository = Depends(get_relay_repository),
) -> ImpactRepairPlanner:
    walker = DownstreamGraphWalker(repository, current_user.uid)
    feasibility = FeasibilityEngine(_NullRouteSnapshotReader(), utc_now())
    return ImpactRepairPlanner(repository, current_user.uid, walker, feasibility, CandidateFactory(), PolicyEngine())


@router.post(
    "/v1/disruptions/{disruption_id}/repair-plans",
    response_model=CreateRepairPlanResponse,
    status_code=201,
    responses={401: {"model": Problem}, 404: {"model": Problem}, 422: {"model": Problem}},
)
async def create_repair_plan(
    disruption_id: str,
    body: CreateRepairPlanRequest,
    current_user: CurrentUser = Depends(require_current_user),
    disruptions: DisruptionRepository = Depends(get_disruption_repository),
    planner: ImpactRepairPlanner = Depends(get_impact_repair_planner),
) -> CreateRepairPlanResponse:
    if body.disruption_id != disruption_id:
        raise HTTPException(status_code=422, detail="disruption_id must match request path")
    disruption = await disruptions.get_disruption(user_id=current_user.uid, disruption_id=disruption_id)
    if disruption is None:
        raise HTTPException(status_code=404, detail="disruption not found")

    # No per-user policy store exists yet; every action defaults to ASK,
    # which is already the mandatory outcome for every voice call and the
    # Uber handoff (see repair_policy._ALWAYS_ASK_KINDS).
    plan = await planner.create_plan(disruption, body.options, UserActionPolicy())

    return CreateRepairPlanResponse(
        repair_plan_id=plan.id,
        version=plan.version,
        selected_candidate_id=plan.selected_candidate_id,
        approval_id=plan.approval_id,
        candidate_count=len(plan.candidates),
    )
